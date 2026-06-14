import Foundation

/// Response of `GET /api/chart` (`build_chart_payload` in `web/charts.py`).
/// Candles + volume + indicator series, shaped for the price chart in the
/// Market Analyst card. `time` strings are "YYYY-MM-DD".
struct ChartPayload: Decodable, Equatable {
    let symbol: String
    let from: String
    let to: String
    let candles: [Candle]
    let volume: [VolumeBar]
    /// Indicator name (e.g. "close_50_sma", "boll_ub", "rsi") -> [{time, value}].
    let indicators: [String: [IndicatorPoint]]

    struct Candle: Decodable, Equatable, Identifiable {
        let time: String
        let open: Double
        let high: Double
        let low: Double
        let close: Double

        var id: String { time }
        var date: Date? { ChartPayload.dateFormatter.date(from: time) }
        var isUp: Bool { close >= open }
    }

    struct VolumeBar: Decodable, Equatable, Identifiable {
        let time: String
        let value: Double
        /// Server-provided rgba string; the chart derives up/down color itself.
        let color: String?

        var id: String { time }
        var date: Date? { ChartPayload.dateFormatter.date(from: time) }
    }

    struct IndicatorPoint: Decodable, Equatable, Identifiable {
        let time: String
        let value: Double

        var id: String { time }
        var date: Date? { ChartPayload.dateFormatter.date(from: time) }
    }

    /// Indicator keys we overlay on the candle chart (subset of `CHART_INDICATORS`).
    static let overlayIndicators = ["close_50_sma", "close_200_sma", "close_10_ema", "boll_ub", "boll_lb"]
    /// Indicator shown in the RSI subchart.
    static let rsiIndicator = "rsi"

    static let dateFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(identifier: "UTC")
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter
    }()
}
