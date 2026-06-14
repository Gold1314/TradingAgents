/**
 * Robinhood server-mediated OAuth + per-user connection status models for the
 * brokerage (v1) milestone. Mirrors the Swift `RobinhoodStatus.swift`
 * (`RobinhoodAuthorizeResponse` + `RobinhoodStatus`) and decodes the payloads of
 * `GET /api/v2/robinhood/authorize` and `GET /api/v2/robinhood/status`.
 *
 * SECURITY: neither payload ever carries a brokerage token (iOS plan §5.2). The
 * phone only observes the public authorization URL, an opaque connect-session
 * id, and the non-secret trade-safety gates that drive the LIVE-vs-simulated
 * styling on the order ticket (§5.3).
 *
 * Self-contained for the brokerage worker: parsing is defensive (every field
 * beyond `connected` is optional) so a partial / feature-off payload still
 * decodes, exactly like the Swift `Decodable` with optionals.
 */

/** Response of `GET /api/v2/robinhood/authorize`. */
export interface RobinhoodAuthorize {
  /** Public OAuth authorization URL to open in the system auth session. */
  authorizationUrl: string;
  /** Opaque server-side correlation id for the in-flight connect attempt. */
  connectSession: string;
}

/**
 * Response of `GET /api/v2/robinhood/status` — the per-user broker snapshot from
 * `RobinhoodBroker.status()`. Tokens are NEVER part of this payload.
 */
export interface RobinhoodStatus {
  connected: boolean;
  available: boolean | null;
  enabled: boolean | null;
  hasSavedCredentials: boolean | null;
  error: string | null;

  // Trade-safety gates (non-secret; mirror BrokerConfig.public_status()).
  tradeMode: string | null;
  dryRun: boolean | null;
  canPlaceRealOrders: boolean | null;
  maxOrderNotional: number | null;
  defaultOrderNotional: number | null;
  orderType: string | null;
}

// MARK: - Defensive coercion helpers

type Raw = Record<string, unknown>;

function asObject(value: unknown): Raw {
  return value && typeof value === 'object' ? (value as Raw) : {};
}
function asStringOrNull(value: unknown): string | null {
  return typeof value === 'string' ? value : null;
}
function asNumberOrNull(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}
function asBoolOrNull(value: unknown): boolean | null {
  return typeof value === 'boolean' ? value : null;
}

/** Parse `{authorization_url, connect_session}`; returns null when unusable. */
export function parseRobinhoodAuthorize(input: unknown): RobinhoodAuthorize | null {
  const raw = asObject(input);
  const authorizationUrl = asStringOrNull(raw.authorization_url);
  if (!authorizationUrl) return null;
  return {
    authorizationUrl,
    connectSession: asStringOrNull(raw.connect_session) ?? '',
  };
}

/** Parse the `/status` payload. `connected` defaults to false when absent. */
export function parseRobinhoodStatus(input: unknown): RobinhoodStatus {
  const raw = asObject(input);
  return {
    connected: raw.connected === true,
    available: asBoolOrNull(raw.available),
    enabled: asBoolOrNull(raw.enabled),
    hasSavedCredentials: asBoolOrNull(raw.has_saved_credentials),
    error: asStringOrNull(raw.error),
    tradeMode: asStringOrNull(raw.trade_mode),
    dryRun: asBoolOrNull(raw.dry_run),
    canPlaceRealOrders: asBoolOrNull(raw.can_place_real_orders),
    maxOrderNotional: asNumberOrNull(raw.max_order_notional),
    defaultOrderNotional: asNumberOrNull(raw.default_order_notional),
    orderType: asStringOrNull(raw.order_type),
  };
}

/**
 * True only when a real-money order can actually be sent: the broker can place
 * real orders AND it is not in dry-run. Anything else is "simulated".
 */
export function placesRealMoney(status: RobinhoodStatus | null): boolean {
  if (!status) return false;
  return (status.canPlaceRealOrders ?? false) && !(status.dryRun ?? true);
}
