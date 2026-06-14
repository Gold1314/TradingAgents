import { secureStore } from '../services/secureStore';
import type { RunRequest } from '../models/run';

/**
 * Persists the in-flight run handle so the app can restore a streaming run on
 * relaunch — the RN equivalent of the web app's `sessionStorage` +
 * `tryRestoreActiveRun()` and the Swift `ActiveRunStore`. On launch the app
 * reads this, calls `GET /api/runs/{id}`, and reconnects the SSE stream.
 *
 * The handle is tiny and non-secret; it is stored via `expo-secure-store` simply
 * to avoid adding an extra persistence dependency for the MVP.
 */
const KEY = 'stockagents.activeRun';

export interface ActiveRun {
  runId: string;
  request: RunRequest;
  nodes: string[];
}

export const activeRunStore = {
  async save(run: ActiveRun): Promise<void> {
    await secureStore.set(KEY, JSON.stringify(run));
  },
  async load(): Promise<ActiveRun | null> {
    const raw = await secureStore.get(KEY);
    if (!raw) return null;
    try {
      return JSON.parse(raw) as ActiveRun;
    } catch {
      return null;
    }
  },
  async clear(): Promise<void> {
    await secureStore.delete(KEY);
  },
};
