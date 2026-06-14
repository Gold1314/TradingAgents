import Foundation

/// Body of `POST /api/runs` — mirrors the `RunRequest` Pydantic model in
/// `web/server.py`. `ticker` and `trade_date` are required; the rest fall back
/// to server defaults when nil. `force` bypasses the 60-minute cache.
struct RunRequest: Codable, Equatable {
    var ticker: String
    var tradeDate: String
    var analysts: [String]
    var assetType: String
    var provider: String?
    var deepModel: String?
    var quickModel: String?
    var maxDebateRounds: Int?
    var maxRiskRounds: Int?
    var force: Bool

    init(
        ticker: String,
        tradeDate: String,
        analysts: [String] = ["market", "social", "news", "fundamentals"],
        assetType: String = "stock",
        provider: String? = nil,
        deepModel: String? = nil,
        quickModel: String? = nil,
        maxDebateRounds: Int? = nil,
        maxRiskRounds: Int? = nil,
        force: Bool = false
    ) {
        self.ticker = ticker
        self.tradeDate = tradeDate
        self.analysts = analysts
        self.assetType = assetType
        self.provider = provider
        self.deepModel = deepModel
        self.quickModel = quickModel
        self.maxDebateRounds = maxDebateRounds
        self.maxRiskRounds = maxRiskRounds
        self.force = force
    }

    enum CodingKeys: String, CodingKey {
        case ticker
        case tradeDate = "trade_date"
        case analysts
        case assetType = "asset_type"
        case provider
        case deepModel = "deep_model"
        case quickModel = "quick_model"
        case maxDebateRounds = "max_debate_rounds"
        case maxRiskRounds = "max_risk_rounds"
        case force
    }
}

/// Asset types accepted by the backend (matches the web form `<select>`).
enum AssetType: String, CaseIterable, Identifiable {
    case stock
    case crypto
    case forex
    case commodity

    var id: String { rawValue }
    var label: String { rawValue.capitalized }
}
