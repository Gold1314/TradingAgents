import Foundation

/// The five-tier Portfolio Manager rating, ordered bearish → bullish. Ported
/// from `RATING_POS` / `RATING_COLOR` in `web/static/index.html` and
/// `RATING_ORDER` in `tradingagents/brokers/intents.py`.
enum Verdict: String, CaseIterable, Equatable {
    case sell = "Sell"
    case underweight = "Underweight"
    case hold = "Hold"
    case overweight = "Overweight"
    case buy = "Buy"

    /// Tolerant parse of the backend `decision` string (case-insensitive).
    /// Unknown values map to `nil` so callers can show the raw word neutrally.
    init?(decision: String) {
        let trimmed = decision.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let match = Verdict.allCases.first(where: {
            $0.rawValue.caseInsensitiveCompare(trimmed) == .orderedSame
        }) else { return nil }
        self = match
    }

    /// Marker position on the Sell→Buy gauge, 0–100 (`RATING_POS`).
    var gaugePosition: Double {
        switch self {
        case .sell: return 0
        case .underweight: return 25
        case .hold: return 50
        case .overweight: return 75
        case .buy: return 100
        }
    }

    /// Accent color hex (`RATING_COLOR`).
    var colorHex: String {
        switch self {
        case .sell: return "#f43f5e"
        case .underweight: return "#fb923c"
        case .hold: return "#94a3b8"
        case .overweight: return "#4ade80"
        case .buy: return "#10b981"
        }
    }
}
