import { useCallback, useEffect, useRef, useState } from 'react';
import { brokerService, BrokerService } from '../services/brokerService';
import { describeError } from '../services/apiError';
import type { RobinhoodStatus } from '../models/robinhood';

/**
 * Drives the Robinhood connect screen (iOS plan §5.1.3), ported from the Swift
 * `BrokerConnectViewModel`: kick off server-mediated OAuth, present the system
 * auth session, then poll `GET /api/v2/robinhood/status` until the server reports
 * `connected: true`. The phone never holds a brokerage token — it only launches
 * the authorization URL and observes server state.
 */

export type ConnectPhase =
  | { kind: 'unknown' } // before the first status load
  | { kind: 'disconnected' }
  | { kind: 'starting' } // calling /authorize
  | { kind: 'authorizing' } // system auth session presented
  | { kind: 'polling' } // bounced back; polling /status
  | { kind: 'connected' }
  | { kind: 'failed'; message: string };

const POLL_INTERVAL_MS = 1_500;
const MAX_POLL_ATTEMPTS = 20; // ~30s ceiling

const sleep = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

export interface BrokerConnect {
  phase: ConnectPhase;
  status: RobinhoodStatus | null;
  isConnected: boolean;
  isBusy: boolean;
  loadStatus: () => Promise<void>;
  connect: () => Promise<void>;
  cancel: () => void;
}

export function useBrokerConnect(): BrokerConnect {
  const [phase, setPhase] = useState<ConnectPhase>({ kind: 'unknown' });
  const [status, setStatus] = useState<RobinhoodStatus | null>(null);

  // Avoid setting state after unmount (the auth session + polling are async).
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      brokerService.cancel();
    };
  }, []);

  const safeSetPhase = useCallback((next: ConnectPhase) => {
    if (mounted.current) setPhase(next);
  }, []);
  const safeSetStatus = useCallback((next: RobinhoodStatus | null) => {
    if (mounted.current) setStatus(next);
  }, []);

  const loadStatus = useCallback(async () => {
    try {
      const next = await brokerService.status();
      safeSetStatus(next);
      safeSetPhase(next.connected ? { kind: 'connected' } : { kind: 'disconnected' });
    } catch (error) {
      // A 401 here usually just means the app token isn't present yet; show the
      // reason rather than a hard failure so the user can still try to connect.
      safeSetPhase({ kind: 'failed', message: describeError(error) });
    }
  }, [safeSetPhase, safeSetStatus]);

  const pollUntilConnected = useCallback(async (): Promise<boolean> => {
    for (let attempt = 0; attempt < MAX_POLL_ATTEMPTS; attempt += 1) {
      if (!mounted.current) return false;
      try {
        const next = await brokerService.status();
        safeSetStatus(next);
        if (next.connected) return true;
      } catch {
        // Transient poll error — keep trying until the attempt ceiling.
      }
      await sleep(POLL_INTERVAL_MS);
    }
    return false;
  }, [safeSetStatus]);

  const connect = useCallback(async () => {
    safeSetPhase({ kind: 'starting' });
    try {
      const authorize = await brokerService.authorize();

      safeSetPhase({ kind: 'authorizing' });
      const session = await brokerService.openAuthSession(authorize.authorizationUrl);
      if (session.kind === 'cancelled') {
        // User backed out of the sheet — return to the prior state quietly.
        safeSetPhase(status?.connected ? { kind: 'connected' } : { kind: 'disconnected' });
        return;
      }
      const callback = BrokerService.callbackStatus(session.url);

      // The server is authoritative (§5.1.3 step 5): poll regardless, but surface
      // the bounce-back reason early when it explicitly failed.
      safeSetPhase({ kind: 'polling' });
      const connected = await pollUntilConnected();
      if (connected) {
        safeSetPhase({ kind: 'connected' });
      } else if (callback.status === 'error') {
        safeSetPhase({
          kind: 'failed',
          message:
            callback.reason ?? status?.error ?? 'Robinhood reported an authorization error.',
        });
      } else {
        safeSetPhase({
          kind: 'failed',
          message: status?.error ?? 'Timed out waiting for Robinhood to connect.',
        });
      }
    } catch (error) {
      safeSetPhase({ kind: 'failed', message: describeError(error) });
    }
  }, [pollUntilConnected, safeSetPhase, status]);

  const cancel = useCallback(() => {
    brokerService.cancel();
  }, []);

  const isConnected = phase.kind === 'connected' || (status?.connected ?? false);
  const isBusy =
    phase.kind === 'starting' || phase.kind === 'authorizing' || phase.kind === 'polling';

  return { phase, status, isConnected, isBusy, loadStatus, connect, cancel };
}
