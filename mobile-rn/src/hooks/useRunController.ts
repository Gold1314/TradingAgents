import { useCallback, useEffect, useRef, useState } from 'react';
import { AppState } from 'react-native';
import { apiClient } from '../services/apiClient';
import { streamRun } from '../services/sseClient';
import { describeError } from '../services/apiError';
import { useRunSession } from '../state/runSessionStore';
import { activeRunStore } from '../state/activeRunStore';

interface RunStreamArgs {
  runId: string | null;
  /** Resume point for SSE (null = fresh connect / full replay). */
  lastEventId?: number | null;
  /** When false, no streaming happens (e.g. a cached run). */
  enabled: boolean;
}

/**
 * Owns the live SSE side-effect: opens the stream and feeds each decoded event
 * into the Zustand store (`applyEvent`), then clears the persisted active-run
 * handle when the run completes. This is the RN equivalent of the streaming
 * lifecycle inside the Swift `RunSessionViewModel`. (The price chart is fetched
 * separately by `useChart`, since cached runs also show it.)
 */
export function useRunStream(args: RunStreamArgs): void {
  const { runId, lastEventId, enabled } = args;

  // Bumping this nonce tears down and re-establishes the stream — used by the
  // foreground resync below when the SSE connection may have dropped while
  // backgrounded. The resume point is tracked across restarts so a reconnect
  // resumes from the last seen event rather than replaying the whole run.
  const [resyncNonce, setResyncNonce] = useState(0);
  const resumeRef = useRef<number | null>(lastEventId ?? null);

  useEffect(() => {
    if (!enabled || !runId) return;

    const controller = new AbortController();
    let cancelled = false;
    const store = useRunSession.getState;

    void (async () => {
      try {
        for await (const { id, event } of streamRun({
          baseURL: apiClient.baseURL,
          runId,
          lastEventId: resumeRef.current,
          signal: controller.signal,
        })) {
          if (cancelled) break;
          if (id != null) resumeRef.current = id;
          store().applyEvent(event);
        }
        // The loop ends either because the stream genuinely completed (a terminal
        // `done` event) OR because the controller was aborted (component unmount /
        // resync teardown). On an abort/cancel we MUST NOT finalize or clear the
        // persisted handle — that would destroy an in-flight run's resume point.
        if (cancelled || controller.signal.aborted) return;
        // Genuine completion → finalize and drop the active-run handle.
        const phase = store().phase;
        if (phase === 'streaming' || phase === 'connecting') store().setPhase('finished');
        await activeRunStore.clear();
      } catch (error) {
        if (!cancelled && !controller.signal.aborted) {
          store().setFailed(describeError(error));
          // A genuine terminal failure — the run won't resume, so drop the handle.
          await activeRunStore.clear();
        }
      }
    })();

    return () => {
      cancelled = true;
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, enabled, resyncNonce]);

  // Foreground resync: SSE can silently drop while the app is backgrounded (and
  // the reconnect budget won't retry a suspended run). On returning to the
  // foreground during an active run, ask the server for authoritative status and
  // either finalize (already done) or re-establish the stream from the resume id.
  const resync = useCallback(() => {
    if (!enabled || !runId) return;
    const store = useRunSession.getState();
    const phase = store.phase;
    if (phase !== 'streaming' && phase !== 'connecting') return;
    void (async () => {
      try {
        const status = await apiClient.runStatus(runId);
        if (status.finished) {
          const current = useRunSession.getState();
          if (current.phase === 'streaming' || current.phase === 'connecting') {
            current.setPhase('finished');
          }
          await activeRunStore.clear();
          return;
        }
      } catch {
        // Status probe failed — fall through and let the stream reconnect anyway.
      }
      // Still running (or status unknown): re-establish the SSE stream.
      setResyncNonce((n) => n + 1);
    })();
  }, [enabled, runId]);

  useEffect(() => {
    if (!enabled || !runId) return;
    const sub = AppState.addEventListener('change', (next) => {
      if (next === 'active') resync();
    });
    return () => sub.remove();
  }, [enabled, runId, resync]);
}

/** One-shot price-chart fetch for the Market Analyst card (all run modes). */
export function useChart(ticker: string, tradeDate: string, assetType: string): void {
  useEffect(() => {
    if (!ticker || !tradeDate) return;
    let cancelled = false;
    const store = useRunSession.getState;
    void (async () => {
      try {
        const chart = await apiClient.fetchChart(ticker, tradeDate, assetType);
        if (!cancelled) store().setChart(chart);
      } catch (error) {
        if (!cancelled) store().setChartError(describeError(error));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [ticker, tradeDate, assetType]);
}
