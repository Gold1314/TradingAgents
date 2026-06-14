import Foundation

/// Response of `GET /api/config` (`get_config()` in `web/server.py`). Drives the
/// run form's defaults. `GET /api/config` is the public bootstrap/health call —
/// no auth required (see plan §3.4.1).
struct RunConfig: Codable, Equatable {
    let provider: String?
    let deepModel: String?
    let quickModel: String?
    let maxDebateRounds: Int?
    let maxRiskRounds: Int?
    /// Wire keys for the four selectable analysts: ["market","social","news","fundamentals"].
    let analysts: [String]
    /// Wire key -> display name, e.g. "market" -> "Market Analyst".
    let analystDisplay: [String: String]
    let supabaseConfigured: Bool?
    let adminAvailable: Bool?
    let cacheWindowMinutes: Int?
    /// Robinhood public status block (trading is a later milestone; decoded but unused in MVP).
    let robinhood: RobinhoodPublicStatus?

    enum CodingKeys: String, CodingKey {
        case provider
        case deepModel = "deep_model"
        case quickModel = "quick_model"
        case maxDebateRounds = "max_debate_rounds"
        case maxRiskRounds = "max_risk_rounds"
        case analysts
        case analystDisplay = "analyst_display"
        case supabaseConfigured = "supabase_configured"
        case adminAvailable = "admin_available"
        case cacheWindowMinutes = "cache_window_minutes"
        case robinhood
    }

    /// Analysts in a stable display order matching `ANALYST_DISPLAY` insertion
    /// order on the server (market, social, news, fundamentals).
    static let analystOrder = ["market", "social", "news", "fundamentals"]
}

/// `robinhood.public_status()` payload. Decoded for completeness of the contract;
/// the brokerage/trading UI is deferred to a later milestone (see README).
struct RobinhoodPublicStatus: Codable, Equatable {
    let enabled: Bool?
    let tradeMode: String?
    let dryRun: Bool?
    let canPlaceRealOrders: Bool?
    let groundingEnabled: Bool?

    enum CodingKeys: String, CodingKey {
        case enabled
        case tradeMode = "trade_mode"
        case dryRun = "dry_run"
        case canPlaceRealOrders = "can_place_real_orders"
        case groundingEnabled = "grounding_enabled"
    }
}
