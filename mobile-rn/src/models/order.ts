/**
 * Broker-agnostic order types. Modeled for contract completeness; the order
 * ticket + biometric gate are a later milestone (v1). Mirrors the Swift
 * `OrderIntent` / `PlaceOrderRequest` and `tradingagents/brokers/intents.py`.
 */
export interface OrderIntent {
  ticker: string;
  /** "buy" | "sell" | "none" */
  action: string;
  rating: string;
  /** USD sizing (mutually exclusive with `quantity`). */
  notional: number | null;
  /** Share count sizing. */
  quantity: number | null;
  orderType: string;
  reason: string;
}

export function parseOrderIntent(input: unknown): OrderIntent | null {
  if (!input || typeof input !== 'object') return null;
  const raw = input as Record<string, unknown>;
  const num = (v: unknown): number | null =>
    typeof v === 'number' && Number.isFinite(v) ? v : null;
  const str = (v: unknown, fallback = ''): string => (typeof v === 'string' ? v : fallback);
  return {
    ticker: str(raw.ticker),
    action: str(raw.action, 'none'),
    rating: str(raw.rating),
    notional: num(raw.notional),
    quantity: num(raw.quantity),
    orderType: str(raw.order_type, 'market'),
    reason: str(raw.reason),
  };
}

export function isActionable(intent: OrderIntent): boolean {
  return intent.action === 'buy' || intent.action === 'sell';
}

/**
 * Body of `POST /api/v2/runs/{run_id}/orders` (and the legacy
 * `POST /api/robinhood/orders/{run_id}`). All fields optional; the ticker is
 * never client-supplied — the server forces it to the run's own.
 */
export interface PlaceOrderRequest {
  action?: string;
  quantity?: number;
  notional?: number;
  /** Serialized as `order_type` on the wire (see apiClient). */
  orderType?: string;
}

/** Per-user Robinhood connection status (`GET /api/v2/robinhood/status`); v1. */
export interface RobinhoodStatus {
  enabled: boolean | null;
  connected: boolean | null;
  tradeMode: string | null;
  dryRun: boolean | null;
  canPlaceRealOrders: boolean | null;
}
