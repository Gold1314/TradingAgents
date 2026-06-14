import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { brokerService } from '../services/brokerService';
import {
  biometrics,
  isBiometricGateError,
  type BiometryKind,
} from '../services/biometrics';
import { isApiError, describeError, type ApiError } from '../services/apiError';
import type { TradeEvent } from '../models/events';
import type { OrderIntent, PlaceOrderRequest } from '../models/order';

/**
 * Drives the order ticket + the LIVE submission gate (iOS plan §5.3, decision 4),
 * ported from the Swift `OrderTicketViewModel`.
 *
 * For a real-money order the REQUIRED gate is the explicit on-screen confirmation
 * (the caller only invokes `confirmAndSubmit` from a deliberate confirm `Alert`).
 * Biometric authentication (Face ID / Touch ID) is an OPTIONAL second factor: it
 * runs immediately before the network call ONLY when the device has an enrolled
 * biometric, and it must never block submission when unavailable (e.g. in Expo Go
 * or on devices without enrolled biometrics). When biometrics ARE present a failed
 * evaluation still aborts the order. There is exactly ONE path to
 * `brokerService.submitOrder`, reached only through this deliberate confirm.
 */

export type Side = 'buy' | 'sell';

/**
 * Typed, actionable classification of a submission failure, mapped from the
 * server's HTTP status (`web/mobile/orders.py`). These all occur *after* the
 * biometric gate, so they can never bypass it.
 */
export type SubmissionFailure =
  | { kind: 'brokerNotConnected'; message: string } // 409 "not connected"
  | { kind: 'runUnavailable'; message: string } // 404
  | { kind: 'rateLimited'; message: string; retryAfter: number | null } // 429
  | { kind: 'validation'; message: string } // 400
  | { kind: 'generic'; message: string };

export type OrderPhase =
  | { kind: 'ready' }
  | { kind: 'authenticating' } // biometric gate in progress (LIVE only)
  | { kind: 'submitting' } // POST in flight
  | { kind: 'placed'; trade: TradeEvent }
  | { kind: 'failed'; failure: SubmissionFailure };

const EMPTY_INTENT: OrderIntent = {
  ticker: '',
  action: 'none',
  rating: '',
  notional: null,
  quantity: null,
  orderType: 'market',
  reason: '',
};

export interface OrderTicket {
  phase: OrderPhase;
  proposed: OrderIntent;
  side: Side;
  setSide: (side: Side) => void;
  orderType: string;
  notionalText: string;
  setNotionalText: (value: string) => void;
  quantityText: string;
  setQuantityText: (value: string) => void;
  /** Whether this submission moves real money (drives styling + the gate). */
  placesRealMoney: boolean;
  /** Whether the broker is in dry-run / simulated mode. */
  isDryRun: boolean;
  biometryKind: BiometryKind;
  isSubmitting: boolean;
  isPlaced: boolean;
  /** THE submission gate. Call only from the deliberate confirm action. */
  confirmAndSubmit: () => Promise<void>;
}

function trimQuantity(value: number): string {
  return value === Math.round(value) ? String(Math.round(value)) : String(value);
}

export function useOrderTicket(runId: string, trade: TradeEvent): OrderTicket {
  const proposed = trade.intent ?? EMPTY_INTENT;

  const isDryRun = trade.dryRun ?? true;
  const placesRealMoney = (trade.canPlaceRealOrders ?? false) && !isDryRun;

  const [phase, setPhase] = useState<OrderPhase>({ kind: 'ready' });
  const [side, setSide] = useState<Side>(proposed.action === 'sell' ? 'sell' : 'buy');
  const [orderType] = useState<string>(proposed.orderType || 'market');
  const [notionalText, setNotionalText] = useState<string>(
    proposed.notional != null ? proposed.notional.toFixed(2) : '',
  );
  const [quantityText, setQuantityText] = useState<string>(
    proposed.quantity != null ? trimQuantity(proposed.quantity) : '',
  );
  const [biometryKind, setBiometryKind] = useState<BiometryKind>('none');

  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  // Best-effort detection of the device's strongest auth, used only to label the
  // confirm action when an enrolled biometric is present. Missing biometrics
  // never block a LIVE order — the explicit confirm Alert is the required gate.
  useEffect(() => {
    void (async () => {
      const kind = await biometrics.availableKind();
      if (mounted.current) setBiometryKind(kind);
    })();
  }, []);

  /** Build the edit payload — ticker is never sent (server forces the run's). */
  const makeEdits = useCallback((): PlaceOrderRequest => {
    const edits: PlaceOrderRequest = {
      action: side,
      orderType: orderType.trim().toLowerCase(),
    };
    if (side === 'buy') {
      const notional = Number.parseFloat(notionalText.trim());
      edits.notional = Number.isFinite(notional) ? notional : undefined;
      edits.quantity = undefined;
    } else {
      const quantity = Number.parseFloat(quantityText.trim());
      edits.quantity = Number.isFinite(quantity) ? quantity : undefined;
      edits.notional = undefined;
    }
    return edits;
  }, [side, orderType, notionalText, quantityText]);

  const confirmAndSubmit = useCallback(async () => {
    try {
      if (placesRealMoney) {
        // Optional biometric second factor: re-check availability authoritatively
        // and only run `evaluate` when an enrolled biometric exists. When present
        // a failed/cancelled evaluation throws and the network call never runs;
        // when absent we proceed straight to submit (the explicit confirm Alert,
        // already passed by the caller, is the required gate).
        const kind = await biometrics.availableKind();
        if (kind === 'faceID' || kind === 'touchID') {
          setPhase({ kind: 'authenticating' });
          await biometrics.evaluate(
            `Authorize your LIVE ${side} order for ${proposed.ticker}.`,
          );
        }
      }
      setPhase({ kind: 'submitting' });
      const result = await brokerService.submitOrder(runId, makeEdits());
      if (mounted.current) setPhase({ kind: 'placed', trade: result });
    } catch (error) {
      if (mounted.current) setPhase({ kind: 'failed', failure: classifyFailure(error) });
    }
  }, [placesRealMoney, side, proposed.ticker, runId, makeEdits]);

  const isSubmitting = phase.kind === 'authenticating' || phase.kind === 'submitting';
  const isPlaced = phase.kind === 'placed';

  return useMemo(
    () => ({
      phase,
      proposed,
      side,
      setSide,
      orderType,
      notionalText,
      setNotionalText,
      quantityText,
      setQuantityText,
      placesRealMoney,
      isDryRun,
      biometryKind,
      isSubmitting,
      isPlaced,
      confirmAndSubmit,
    }),
    [
      phase,
      proposed,
      side,
      orderType,
      notionalText,
      quantityText,
      placesRealMoney,
      isDryRun,
      biometryKind,
      isSubmitting,
      isPlaced,
      confirmAndSubmit,
    ],
  );
}

/**
 * Classify a thrown error into an actionable `SubmissionFailure`. Branches on the
 * HTTP status carried by `ApiError`; the two `409` sub-cases share a status and
 * are disambiguated by the server's detail string ("not connected" vs other).
 */
function classifyFailure(error: unknown): SubmissionFailure {
  if (isBiometricGateError(error)) {
    return { kind: 'generic', message: error.message };
  }
  if (!isApiError(error)) {
    return { kind: 'generic', message: describeError(error) };
  }
  const apiError: ApiError = error;
  switch (apiError.kind) {
    case 'rateLimited': {
      const message =
        apiError.retryAfter != null
          ? `Order limit reached. Try again in about ${apiError.retryAfter}s.`
          : 'Order limit reached. Please try again later.';
      return { kind: 'rateLimited', message, retryAfter: apiError.retryAfter };
    }
    case 'http': {
      switch (apiError.status) {
        case 404:
          return {
            kind: 'runUnavailable',
            message: 'This run is no longer available. Start a new run to place an order.',
          };
        case 409:
          if (apiError.detail && apiError.detail.toLowerCase().includes('not connected')) {
            return {
              kind: 'brokerNotConnected',
              message: "Robinhood isn't connected. Connect your account to place this order.",
            };
          }
          return {
            kind: 'generic',
            message: apiError.detail ?? "This order can't be placed right now.",
          };
        case 400: {
          const guidance = 'Set a side (buy or sell) and a positive size, then try again.';
          return {
            kind: 'validation',
            message: apiError.detail ? `${apiError.detail}. ${guidance}` : guidance,
          };
        }
        default:
          return { kind: 'generic', message: apiError.detail ?? `Order failed (HTTP ${apiError.status}).` };
      }
    }
    case 'invalidUrl':
    case 'transport':
    case 'timeout':
    case 'decoding':
    case 'unauthorized':
    case 'cancelled':
      return { kind: 'generic', message: describeError(apiError) };
    default: {
      const _never: never = apiError;
      return { kind: 'generic', message: describeError(_never) };
    }
  }
}
