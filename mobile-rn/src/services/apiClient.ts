import { authService } from './authService';
import type { ApiError } from './apiError';
import { DEFAULT_API_BASE_URL } from '../config/env';
import {
  parseRunConfig,
  parseStartRunResponse,
  parseRunStatus,
  runRequestToWire,
  type RunConfig,
  type RunRequest,
  type StartRunResponse,
  type RunStatus,
} from '../models/run';
import { parseChartPayload, type ChartPayload } from '../models/chart';
import type { PlaceOrderRequest } from '../models/order';
import {
  parseVoiceConfig,
  parseVoiceReconcileResponse,
  parseVoiceSessionStartResponse,
  type VoiceConfig,
  type VoiceReconcileResponse,
  type VoiceSessionStartResponse,
} from '../models/voice';

/**
 * REST client for the StockAgents backend, ported from the Swift `APIClient`.
 *
 * Responsibilities: a configurable base URL, bearer-token injection (from
 * `authService`), JSON decode against the typed model parsers, non-2xx → typed
 * `ApiError` (incl. 401 + 429-with-Retry-After), per-call timeouts, and a single
 * transparent token refresh on 401. SSE is handled separately by `sseClient`.
 */
class ApiClient {
  /** Mutable so a Settings screen can repoint the app at a new backend. */
  baseURL: string = DEFAULT_API_BASE_URL;

  /** Default per-request timeout. Runs start fast (the POST returns a run_id). */
  private timeoutMs = 60_000;

  // ── REST endpoints ──────────────────────────────────────────────────────
  //
  // Reads (config/chart/status) and SSE use the open legacy `/api/*` routes.
  // Run *creation* uses the authenticated `POST /api/v2/runs` (web/mobile/
  // router.py), which adds per-user auth + rate limiting in front of the same
  // run machinery — so the run it creates is still served by the legacy
  // `GET /api/runs/{id}` status + `/events` SSE endpoints. The bearer is
  // injected from `authService`; a 401 triggers one transparent refresh+retry.

  /** `GET /api/config` — public bootstrap call; drives the run form defaults. */
  async fetchConfig(): Promise<RunConfig> {
    return parseRunConfig(await this.request('GET', '/api/config'));
  }

  /**
   * `POST /api/v2/runs` — start a run (authenticated). Requires a signed-in
   * session; mirrors the legacy `/api/runs` discriminator: returns
   * `{cached:true, run}` on a 60-minute cache hit (same ticker + trade_date,
   * cache toggle on) and `{cached:false, run_id, nodes}` otherwise. Cache hits
   * intentionally bypass the per-user run quota. On 401 the request
   * transparently refreshes once; a 429 surfaces as a `rateLimited` error
   * carrying `Retry-After`.
   */
  async startRun(request: RunRequest): Promise<StartRunResponse> {
    return parseStartRunResponse(
      await this.request('POST', '/api/v2/runs', runRequestToWire(request)),
    );
  }

  /** `GET /api/runs/{run_id}` — run status for session restore. */
  async runStatus(runId: string): Promise<RunStatus> {
    return parseRunStatus(await this.request('GET', `/api/runs/${encodeURIComponent(runId)}`));
  }

  /** `GET /api/chart` — candles + indicators for the Market Analyst card. */
  async fetchChart(
    ticker: string,
    tradeDate: string,
    assetType = 'stock',
    lookback = 180,
  ): Promise<ChartPayload> {
    const qs = new URLSearchParams({
      ticker,
      trade_date: tradeDate,
      asset_type: assetType,
      lookback: String(lookback),
    }).toString();
    return parseChartPayload(await this.request('GET', `/api/chart?${qs}`));
  }

  // ── v2 mobile endpoints (auth + per-user broker). Defined for contract
  //    completeness; the trading UI is a deferred milestone (see README). ──

  /** `POST /api/v2/runs/{run_id}/orders` — submit the run's proposed order (v1). */
  async submitOrder(runId: string, edits: PlaceOrderRequest): Promise<unknown> {
    const body: Record<string, unknown> = {};
    if (edits.action != null) body.action = edits.action;
    if (edits.quantity != null) body.quantity = edits.quantity;
    if (edits.notional != null) body.notional = edits.notional;
    if (edits.orderType != null) body.order_type = edits.orderType;
    return this.request('POST', `/api/v2/runs/${encodeURIComponent(runId)}/orders`, body);
  }

  // ── Voice-conversational layer (additive). Default-off until the backend
  //    is wired with LiveKit + Deepgram + ElevenLabs creds. The capability
  //    probe drives Talk-button visibility on the agent cards; the start
  //    endpoint mints a LiveKit room JWT for the React Native LiveKit SDK. ──

  /**
   * `GET /api/voice/config` — capability probe. The Talk button on each
   * agent card stays hidden unless `ready === true`. Failures collapse to
   * `enabled = ready = false` so the rest of the run UI is unaffected when
   * the optional voice deps aren't installed.
   */
  async fetchVoiceConfig(): Promise<VoiceConfig> {
    try {
      return parseVoiceConfig(await this.request('GET', '/api/voice/config'));
    } catch {
      return {
        enabled: false,
        ready: false,
        reason: 'voice_unavailable',
        ttsProvider: null,
        sessionMaxSeconds: null,
        dailyMinutesPerUser: null,
        personasCount: null,
      };
    }
  }

  /**
   * `POST /api/voice/sessions` — mint a LiveKit room JWT for one agent in a
   * completed run. The room is single-user; concurrent agents from the same
   * run live in separate rooms (per the plan §8 default).
   */
  async startVoiceSession(runId: string, agentId: string): Promise<VoiceSessionStartResponse> {
    return parseVoiceSessionStartResponse(
      await this.request('POST', '/api/voice/sessions', {
        run_id: runId,
        agent_id: agentId,
      }),
    );
  }

  /**
   * `POST /api/voice/sessions/{id}/reconcile` — re-invoke the Portfolio
   * Manager with the user's objection as new context, without re-running
   * the full pipeline. Surfaces the (possibly flipped) decision and
   * updated rationale for the in-call composer card.
   */
  async reconcilePortfolioManager(
    sessionId: string,
    objection: string,
  ): Promise<VoiceReconcileResponse> {
    return parseVoiceReconcileResponse(
      await this.request(
        'POST',
        `/api/voice/sessions/${encodeURIComponent(sessionId)}/reconcile`,
        { objection },
      ),
    );
  }

  // ── plumbing ──────────────────────────────────────────────────────────

  /**
   * Build → send, with a single transparent token refresh on 401 (iOS plan
   * §3.4.1). The request is rebuilt after the refresh so the retry carries the
   * new bearer. If the refresh fails, the session is cleared and 401 propagates.
   */
  private async request(
    method: string,
    path: string,
    body?: Record<string, unknown>,
  ): Promise<unknown> {
    try {
      return await this.send(method, path, body);
    } catch (error) {
      if (isUnauthorized(error)) {
        const outcome = await authService.attemptRefresh();
        if (outcome.ok) {
          return this.send(method, path, body);
        }
        // Only a definitive rejection wipes the session; a transport failure
        // during refresh keeps the tokens and surfaces the (retryable) 401.
        if (outcome.reason === 'rejected') {
          await authService.signOut();
        }
        throw error;
      }
      throw error;
    }
  }

  private async send(
    method: string,
    path: string,
    body?: Record<string, unknown>,
  ): Promise<unknown> {
    const url = this.resolve(path);
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
      if (controller.signal.aborted) throw timeout();
      throw transport(err instanceof Error ? err.message : String(err));
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
      throw decoding(err instanceof Error ? err.message : String(err));
    }
  }

  private resolve(path: string): string {
    return `${this.baseURL.replace(/\/$/, '')}${path.startsWith('/') ? path : `/${path}`}`;
  }
}

// MARK: - Error mapping

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

/** Pull FastAPI's `{"detail": ...}` body when present. */
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

const timeout = (): ApiError => ({ kind: 'timeout' });
const transport = (message: string): ApiError => ({ kind: 'transport', message });
const decoding = (message: string): ApiError => ({ kind: 'decoding', message });

export const apiClient = new ApiClient();
