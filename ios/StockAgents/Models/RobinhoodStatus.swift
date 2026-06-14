import Foundation

/// Response of `GET /api/v2/robinhood/authorize` (server-mediated OAuth, plan
/// §5.1.3). The app opens `authorizationURL` in an `ASWebAuthenticationSession`;
/// `connectSession` is the server-side correlation id for the in-flight attempt.
///
/// The phone NEVER sees `code`/`state` or any brokerage token — the PKCE
/// verifier/state and the token exchange stay entirely server-side. This payload
/// only carries the public authorization URL and an opaque session id.
struct RobinhoodAuthorizeResponse: Decodable, Equatable {
    let authorizationURL: String
    let connectSession: String

    enum CodingKeys: String, CodingKey {
        case authorizationURL = "authorization_url"
        case connectSession = "connect_session"
    }
}

/// Response of `GET /api/v2/robinhood/status` — the per-user broker snapshot from
/// `RobinhoodBroker.status()` (`tradingagents/brokers/robinhood_mcp.py`), which is
/// `BrokerConfig.public_status()` plus the live connection fields.
///
/// Tokens are NEVER part of this payload (plan §5.2). The phone only observes the
/// connection state and the non-secret trade-safety gates, which drive the
/// LIVE-vs-simulated styling on the order ticket (plan §5.3). All fields beyond
/// `connected` are optional so a partial/feature-off payload still decodes.
struct RobinhoodStatus: Decodable, Equatable {
    let connected: Bool
    let available: Bool?
    let enabled: Bool?
    let hasSavedCredentials: Bool?
    let error: String?

    // Trade-safety gates (non-secret; mirror BrokerConfig.public_status()).
    let tradeMode: String?
    let dryRun: Bool?
    let canPlaceRealOrders: Bool?
    let maxOrderNotional: Double?
    let defaultOrderNotional: Double?
    let orderType: String?

    enum CodingKeys: String, CodingKey {
        case connected
        case available
        case enabled
        case hasSavedCredentials = "has_saved_credentials"
        case error
        case tradeMode = "trade_mode"
        case dryRun = "dry_run"
        case canPlaceRealOrders = "can_place_real_orders"
        case maxOrderNotional = "max_order_notional"
        case defaultOrderNotional = "default_order_notional"
        case orderType = "order_type"
    }

    /// True only when a real-money order can actually be sent: the broker can
    /// place real orders AND it is not in dry-run. Anything else is "simulated".
    var placesRealMoney: Bool {
        (canPlaceRealOrders ?? false) && !(dryRun ?? true)
    }
}
