import { useEffect } from 'react';
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

  useEffect(() => {
    if (!enabled || !runId) return;

    const controller = new AbortController();
    let cancelled = false;
    const store = useRunSession.getState;

    void (async () => {
      try {
        for await (const { event } of streamRun({
          baseURL: apiClient.baseURL,
          runId,
          lastEventId: lastEventId ?? null,
          signal: controller.signal,
        })) {
          if (cancelled) break;
          store().applyEvent(event);
        }
        // Stream finished without throwing.
        const phase = store().phase;
        if (phase === 'streaming' || phase === 'connecting') {
          store().markFinished();
        }
        await activeRunStore.clear();
      } catch (error) {
        if (!cancelled && !controller.signal.aborted) {
          store().setFailed(describeError(error));
        }
      }
    })();

    return () => {
      cancelled = true;
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, enabled]);
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
