import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { brokerService } from '../services/brokerService';
import {
  biometrics,
  BiometricGateError,
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
  | { kind: 'validation'; message: string } // 400 / client-side size check
  | { kind: 'uncertain'; message: string } // timeout/transport — order may still have executed
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
  /**
   * The positive size for the active side (notional for buy, quantity for sell),
   * or null when the field is empty / non-numeric / non-positive. This is the
   * exact amount that will be sent — the confirm dialog must display it.
   */
  parsedAmount: number | null;
  /** Surface an inline "enter a valid amount" error; blocks the confirm dialog. */
  reportInvalidSize: () => void;
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

  // The exact positive size that will be sent. Empty / non-numeric / non-positive
  // resolves to null so the caller can block submission (mirrors the server's 400)
  // rather than sending `undefined` and letting the server silently fall back to
  // the stashed pending-order size while the UI shows "$0.00".
  const parsedAmount = useMemo<number | null>(() => {
    const text = side === 'buy' ? notionalText : quantityText;
    const value = Number.parseFloat(text.trim());
    return Number.isFinite(value) && value > 0 ? value : null;
  }, [side, notionalText, quantityText]);

  /** Build the edit payload — ticker is never sent (server forces the run's). */
  const makeEdits = useCallback((): PlaceOrderRequest => {
    const edits: PlaceOrderRequest = {
      action: side,
      orderType: orderType.trim().toLowerCase(),
    };
    // `parsedAmount` is guaranteed positive by `confirmAndSubmit`'s gate; carry it
    // through verbatim so what the server executes matches what the user confirmed.
    if (side === 'buy') {
      edits.notional = parsedAmount ?? undefined;
      edits.quantity = undefined;
    } else {
      edits.quantity = parsedAmount ?? undefined;
      edits.notional = undefined;
    }
    return edits;
  }, [side, orderType, parsedAmount]);

  /** Show an inline validation error and keep the confirm dialog closed. */
  const reportInvalidSize = useCallback(() => {
    setPhase({
      kind: 'failed',
      failure: { kind: 'validation', message: 'Enter a valid amount greater than 0.' },
    });
  }, []);

  const confirmAndSubmit = useCallback(async () => {
    try {
      // Defensive re-check: the network call must never proceed without a positive
      // size (the confirm dialog already blocked this, but the gate stays here too).
      if (parsedAmount == null) {
        reportInvalidSize();
        return;
      }
      if (placesRealMoney) {
        // Mandatory OS authentication for a real-money order (biometrics.ts
        // contract: the gate is NEVER skipped for a LIVE order). `evaluate` falls
        // back to the device passcode when no biometric is enrolled, so we run it
        // whenever ANY device authentication is available. Only a device offering
        // NO authentication at all ('none') can't be gated — there we block the
        // LIVE order rather than silently proceeding.
        const kind = await biometrics.availableKind();
        if (kind === 'none') {
          throw new BiometricGateError(
            'unavailable',
            'Device authentication is required for live orders. Set up Face ID / Touch ID or a device passcode in Settings, then try again.',
          );
        }
        setPhase({ kind: 'authenticating' });
        await biometrics.evaluate(`Authorize your LIVE ${side} order for ${proposed.ticker}.`);
      }
      setPhase({ kind: 'submitting' });
      const result = await brokerService.submitOrder(runId, makeEdits());
      if (mounted.current) setPhase({ kind: 'placed', trade: result });
    } catch (error) {
      if (mounted.current) setPhase({ kind: 'failed', failure: classifyFailure(error) });
    }
  }, [parsedAmount, reportInvalidSize, placesRealMoney, side, proposed.ticker, runId, makeEdits]);

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
      parsedAmount,
      reportInvalidSize,
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
      parsedAmount,
      reportInvalidSize,
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
    case 'transport':
    case 'timeout':
      // The POST may have reached the broker before the connection dropped/timed
      // out, so the order might already be live. Warn rather than imply it failed.
      return {
        kind: 'uncertain',
        message:
          'Your order may still have been placed. Check your Robinhood account before retrying to avoid placing it twice.',
      };
    case 'invalidUrl':
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
