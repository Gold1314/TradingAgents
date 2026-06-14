import { fetch as streamFetch } from 'expo/fetch';
import { authService } from './authService';
import { parseAgentEvent, type AgentEvent } from '../models/events';
import type { ApiError } from './apiError';

/**
 * Server-Sent Events consumer for `GET /api/runs/{run_id}/events`, ported from
 * the Swift `RunStreamClient`.
 *
 * React Native has no `EventSource` that supports custom request headers, so
 * this is built on **`expo/fetch`** streaming: `Response.body` is a real
 * `ReadableStream<Uint8Array>`, which lets us send `Authorization: Bearer …` and
 * `Last-Event-ID` and read frames incrementally. It reads the byte stream,
 * decodes UTF-8, splits on `\n`, buffers `id:` / `data:` fields, ignores
 * `:` keepalive comments, decodes each frame into an `AgentEvent`, and yields it
 * with its SSE id through an async generator.
 *
 * Resume: the last seen `id:` is tracked and sent as `Last-Event-ID` on
 * reconnect, so the server (`run_events()` in `web/server.py`) resumes from
 * `last-event-id + 1`. A long run can outlive the access token, so a mid-stream
 * 401 triggers a single-flight token refresh + immediate reconnect (preserving
 * `Last-Event-ID`), exactly like the Swift client.
 */

export interface StreamedEvent {
  id: number | null;
  event: AgentEvent;
}

export interface StreamOptions {
  baseURL: string;
  runId: string;
  /** Resume point; null/undefined for a fresh connection (full replay). */
  lastEventId?: number | null;
  /** External cancellation (consumer unmount / new run). */
  signal: AbortSignal;
}

const MAX_RECONNECT_ATTEMPTS = 5;
const MAX_AUTH_REFRESH_ATTEMPTS = 2;

const unauthorized = (): ApiError => ({ kind: 'unauthorized' });
const transport = (message: string): ApiError => ({ kind: 'transport', message });

function isUnauthorized(error: unknown): boolean {
  return Boolean(error && typeof error === 'object' && (error as ApiError).kind === 'unauthorized');
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Open one connection and validate the HTTP status, returning the byte reader. */
async function openConnection(
  opts: StreamOptions,
  lastEventId: number | null,
): Promise<ReadableStreamDefaultReader<Uint8Array>> {
  const url = `${opts.baseURL.replace(/\/$/, '')}/api/runs/${encodeURIComponent(opts.runId)}/events`;
  const headers: Record<string, string> = { Accept: 'text/event-stream' };
  if (lastEventId != null) headers['Last-Event-ID'] = String(lastEventId);
  const authHeader = await authService.getAuthHeader();
  if (authHeader) headers.Authorization = authHeader;

  const res = await streamFetch(url, { method: 'GET', headers, signal: opts.signal });
  if (!(res.status >= 200 && res.status < 300)) {
    if (res.status === 401) throw unauthorized();
    throw transport(`SSE connection failed (HTTP ${res.status}).`);
  }
  if (!res.body) throw transport('SSE response had no body stream.');
  return res.body.getReader();
}

/**
 * Read SSE frames from one connection. Yields each decoded event; reports the
 * latest id via `onId`; returns true once a terminal `done` event is seen.
 */
async function* readFrames(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  signal: AbortSignal,
  onId: (id: number) => void,
): AsyncGenerator<StreamedEvent, boolean, void> {
  const decoder = new TextDecoder();
  let buffer = '';
  let dataBuffer = '';
  let pendingId: number | null = null;

  try {
    while (!signal.aborted) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let newlineIndex: number;
      while ((newlineIndex = buffer.indexOf('\n')) >= 0) {
        // Strip a trailing CR (CRLF line endings) along with the LF.
        let line = buffer.slice(0, newlineIndex);
        if (line.endsWith('\r')) line = line.slice(0, -1);
        buffer = buffer.slice(newlineIndex + 1);

        if (line === '') {
          // Blank line => dispatch the buffered frame.
          if (dataBuffer !== '') {
            const event = parseAgentEvent(safeJsonParse(dataBuffer));
            yield { id: pendingId, event };
            if (pendingId != null) onId(pendingId);
            if (event.type === 'done') return true;
          }
          dataBuffer = '';
          pendingId = null;
          continue;
        }
        if (line.startsWith(':')) continue; // keepalive comment

        const { field, fieldValue } = parseField(line);
        if (field === 'id') {
          const parsed = Number.parseInt(fieldValue, 10);
          if (Number.isFinite(parsed)) pendingId = parsed;
        } else if (field === 'data') {
          dataBuffer += dataBuffer === '' ? fieldValue : `\n${fieldValue}`;
        }
        // ignore event:/retry: — the server doesn't use them
      }
    }
  } finally {
    try {
      await reader.cancel();
    } catch {
      // already closed
    }
    reader.releaseLock?.();
  }
  return false;
}

/** Split an SSE field line into (field, value), trimming one optional leading space. */
export function parseField(line: string): { field: string; fieldValue: string } {
  const colon = line.indexOf(':');
  if (colon < 0) return { field: line, fieldValue: '' };
  const field = line.slice(0, colon);
  let fieldValue = line.slice(colon + 1);
  if (fieldValue.startsWith(' ')) fieldValue = fieldValue.slice(1);
  return { field, fieldValue };
}

function safeJsonParse(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return {};
  }
}

/**
 * Open (and transparently re-open) the event stream for a run. Yields decoded
 * events; finishes on a terminal `done`; throws an `ApiError` if it gives up.
 */
export async function* streamRun(opts: StreamOptions): AsyncGenerator<StreamedEvent, void, void> {
  let resumeId = opts.lastEventId ?? null;
  let attempts = 0;
  let authRefreshAttempts = 0;

  while (!opts.signal.aborted) {
    let reader: ReadableStreamDefaultReader<Uint8Array>;
    try {
      reader = await openConnection(opts, resumeId);
      // Connected (past auth) → any prior token expiry is resolved.
      authRefreshAttempts = 0;
    } catch (error) {
      if (opts.signal.aborted) return;
      if (isUnauthorized(error)) {
        authRefreshAttempts += 1;
        const refreshed =
          authRefreshAttempts <= MAX_AUTH_REFRESH_ATTEMPTS && (await authService.attemptRefresh());
        if (!refreshed) {
          await authService.signOut();
          throw error;
        }
        continue; // reconnect now, no backoff — the token is fresh
      }
      attempts += 1;
      if (attempts >= MAX_RECONNECT_ATTEMPTS) throw error;
      await sleep(Math.min(attempts, 3) * 1000);
      continue;
    }

    try {
      const sawDone = yield* readFrames(reader, opts.signal, (id) => {
        resumeId = id;
      });
      if (sawDone) return;
      // Stream ended without a terminal `done` (server closed). Reconnect.
      attempts += 1;
    } catch (error) {
      if (opts.signal.aborted) return;
      attempts += 1;
      if (attempts >= MAX_RECONNECT_ATTEMPTS) {
        throw transport(error instanceof Error ? error.message : String(error));
      }
    }
    if (!opts.signal.aborted) await sleep(Math.min(attempts, 3) * 1000);
  }
}
