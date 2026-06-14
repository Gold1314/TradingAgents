import * as WebBrowser from 'expo-web-browser';
import { apiClient } from './apiClient';
import { authService } from './authService';
import type { ApiError } from './apiError';
import {
  parseRobinhoodAuthorize,
  parseRobinhoodStatus,
  type RobinhoodAuthorize,
  type RobinhoodStatus,
} from '../models/robinhood';
import type { PlaceOrderRequest } from '../models/order';
import { parseAgentEvent, type TradeEvent } from '../models/events';

/**
 * Brokerage (Robinhood) service for the v1 trading milestone. Owns the two
 * security-sensitive collaborations from the iOS reference:
 *
 * 1. The `expo-web-browser` half of the **server-mediated** OAuth connect flow
 *    (port of `BrokerConnectService` + `ASWebAuthenticationSession`): it only
 *    opens the server's authorization URL and observes the `stockagents://`
 *    bounce-back — the phone never sees `code`/`state` or a brokerage token.
 * 2. The authenticated `/api/v2/*` REST calls (`authorize` / `status` /
 *    `submitOrder`).
 *
 * It does NOT modify the shared `apiClient`; it reuses `apiClient.baseURL` (so a
 * Settings repoint still applies) and `authService` for the bearer + the single
 * transparent 401→refresh→retry, mirroring `apiClient`'s own private plumbing.
 */

/**
 * Must match `scheme: 'stockagents'` in `app.config.ts` and the server's
 * `MOBILE_OAUTH_APP_REDIRECT` (`stockagents://oauth/robinhood`). The server 302s
 * the OAuth callback to this URL with `?status=ok|error&reason=...`.
 */
export const CALLBACK_SCHEME = 'stockagents';
export const APP_REDIRECT_URL = `${CALLBACK_SCHEME}://oauth/robinhood`;

/** Outcome of the system auth session, normalized for the connect flow. */
export type AuthSessionResult =
  | { kind: 'callback'; url: string }
  | { kind: 'cancelled' };

/** Parsed `status`/`reason` query items from the bounce-back deep link. */
export interface CallbackStatus {
  status: string | null;
  reason: string | null;
}

class BrokerService {
  /** Default per-request timeout, matching `apiClient`. */
  private timeoutMs = 60_000;

  // ── v2 brokerage REST ────────────────────────────────────────────────

  /** `GET /api/v2/robinhood/authorize` — start server-mediated OAuth. */
  async authorize(): Promise<RobinhoodAuthorize> {
    const parsed = parseRobinhoodAuthorize(await this.request('GET', '/api/v2/robinhood/authorize'));
    if (!parsed) {
      const err: ApiError = { kind: 'decoding', message: 'invalid authorization response' };
      throw err;
    }
    return parsed;
  }

  /** `GET /api/v2/robinhood/status` — per-user connection + trade-safety. */
  async status(): Promise<RobinhoodStatus> {
    return parseRobinhoodStatus(await this.request('GET', '/api/v2/robinhood/status'));
  }

  /**
   * `POST /api/v2/runs/{run_id}/orders` — submit the run's proposed order with
   * optional ticket edits. The ticker is NEVER sent (the server forces the run's
   * own). Returns the `trade`-shaped result.
   */
  async submitOrder(runId: string, edits: PlaceOrderRequest): Promise<TradeEvent> {
    const body: Record<string, unknown> = {};
    if (edits.action != null) body.action = edits.action;
    if (edits.quantity != null) body.quantity = edits.quantity;
    if (edits.notional != null) body.notional = edits.notional;
    if (edits.orderType != null) body.order_type = edits.orderType;
    const result = parseAgentEvent(
      await this.request('POST', `/api/v2/runs/${encodeURIComponent(runId)}/orders`, body),
    );
    if (result.type !== 'trade') {
      const err: ApiError = { kind: 'decoding', message: 'unexpected order response' };
      throw err;
    }
    return result;
  }

  // ── OAuth system auth session (expo-web-browser) ─────────────────────

  /**
   * Present the system auth session for `authorizationUrl` and resolve with the
   * app-scheme callback URL the server 302s to (or `cancelled` when the user
   * dismisses the sheet). The brokerage session cookie never reaches app code —
   * the server completes the PKCE exchange.
   */
  async openAuthSession(authorizationUrl: string): Promise<AuthSessionResult> {
    const result = await WebBrowser.openAuthSessionAsync(authorizationUrl, APP_REDIRECT_URL);
    // Only the redirect result (`type: 'success'`) carries the callback URL; every
    // other result (cancel / dismiss / locked / opened) is a benign back-out. An
    // `if` on the `success` discriminant is used rather than a `switch` because
    // expo-web-browser types the non-redirect `type` as the string enum
    // `WebBrowserResultType`, which does not narrow against string-literal cases.
    if (result.type === 'success') {
      return { kind: 'callback', url: result.url };
    }
    return { kind: 'cancelled' };
  }

  /** Best-effort dismissal of a lingering auth session (e.g. on screen exit). */
  cancel(): void {
    void WebBrowser.dismissAuthSession?.();
  }

  /**
   * Parse `status` / `reason` from the bounce-back URL
   * (`stockagents://oauth/robinhood?status=ok|error&reason=...`). The server
   * `/status` poll remains authoritative; this only short-circuits the UI copy.
   * Parsed manually because RN's `URL` does not reliably parse custom schemes.
   */
  static callbackStatus(url: string): CallbackStatus {
    const queryStart = url.indexOf('?');
    if (queryStart < 0) return { status: null, reason: null };
    const query = url.slice(queryStart + 1);
    const params: Record<string, string> = {};
    for (const pair of query.split('&')) {
      if (!pair) continue;
      const eq = pair.indexOf('=');
      const key = eq < 0 ? pair : pair.slice(0, eq);
      const value = eq < 0 ? '' : pair.slice(eq + 1);
      try {
        params[decodeURIComponent(key)] = decodeURIComponent(value);
      } catch {
        params[key] = value;
      }
    }
    return { status: params.status ?? null, reason: params.reason ?? null };
  }

  // ── plumbing (mirrors apiClient's private request/send) ───────────────

  /** Build → send, with a single transparent token refresh on 401. */
  private async request(
    method: string,
    path: string,
    body?: Record<string, unknown>,
  ): Promise<unknown> {
    try {
      return await this.send(method, path, body);
    } catch (error) {
      if (isUnauthorized(error)) {
        const refreshed = await authService.attemptRefresh();
        if (!refreshed) {
          await authService.signOut();
          throw error;
        }
        return this.send(method, path, body);
      }
      throw error;
    }
  }

  private async send(
    method: string,
    path: string,
    body?: Record<string, unknown>,
  ): Promise<unknown> {
    const url = `${apiClient.baseURL.replace(/\/$/, '')}${path.startsWith('/') ? path : `/${path}`}`;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);

    const headers: Record<string, string> = { Accept: 'application/json' };
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    const authHeader = await authService.getAuthHeader();
    if (authHeader) headers.Authorization = authHeader;

    let res: Response;
    try {
      res = await fetch(url, {
        method,
        headers,
        body: body !== undefined ? JSON.stringify(body) : undefined,
        signal: controller.signal,
      });
    } catch (err) {
      clearTimeout(timer);
      if (controller.signal.aborted) throw timeoutError();
      throw transportError(err instanceof Error ? err.message : String(err));
    }
    clearTimeout(timer);

    const text = await res.text();
    if (!(res.status >= 200 && res.status < 300)) {
      throw mapHttpError(res, text);
    }
    if (!text) return {};
    try {
      return JSON.parse(text);
    } catch (err) {
      throw decodingError(err instanceof Error ? err.message : String(err));
    }
  }
}

// MARK: - Error mapping (replicated locally; apiClient's helpers are private)

function mapHttpError(res: Response, body: string): ApiError {
  switch (res.status) {
    case 401:
      return { kind: 'unauthorized' };
    case 429: {
      const header = res.headers.get('Retry-After');
      const retryAfter = header != null ? Number.parseInt(header, 10) : NaN;
      return { kind: 'rateLimited', retryAfter: Number.isFinite(retryAfter) ? retryAfter : null };
    }
    default:
      return { kind: 'http', status: res.status, detail: extractDetail(body) };
  }
}

function extractDetail(body: string): string | null {
  if (!body) return null;
  try {
    const obj = JSON.parse(body) as Record<string, unknown>;
    const detail = obj.detail;
    if (typeof detail === 'string') return detail;
    if (detail != null) return JSON.stringify(detail);
  } catch {
    // not JSON — fall through
  }
  return null;
}

function isUnauthorized(error: unknown): boolean {
  return Boolean(error && typeof error === 'object' && (error as ApiError).kind === 'unauthorized');
}

const timeoutError = (): ApiError => ({ kind: 'timeout' });
const transportError = (message: string): ApiError => ({ kind: 'transport', message });
const decodingError = (message: string): ApiError => ({ kind: 'decoding', message });

export const brokerService = new BrokerService();
export { BrokerService };
