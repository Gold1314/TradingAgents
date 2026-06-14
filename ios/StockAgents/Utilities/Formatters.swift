import Foundation

/// Number/token/cost formatting, mirroring `fmtTok` / `fmtCost` / `fmtUsd` in
/// `web/static/index.html`.
enum Formatters {
    /// Compact token count: 1234 -> "1.2K", 1_500_000 -> "1.5M".
    static func tokens(_ value: Int) -> String {
        let magnitude = Double(value)
        if magnitude >= 1_000_000 {
            return String(format: "%.1fM", magnitude / 1_000_000)
        }
        if magnitude >= 1_000 {
            return String(format: "%.1fK", magnitude / 1_000)
        }
        return String(value)
    }

    /// Estimated cost in USD, or "—" when the model is unpriced (cost == nil).
    static func cost(_ value: Double?) -> String {
        guard let value else { return "—" }
        if value < 0.01 {
            return String(format: "$%.4f", value)
        }
        return String(format: "$%.2f", value)
    }

    /// Whole-dollar / 2dp currency for account + order amounts.
    static func usd(_ value: Double?) -> String {
        guard let value else { return "—" }
        return String(format: "$%.2f", value)
    }
}
