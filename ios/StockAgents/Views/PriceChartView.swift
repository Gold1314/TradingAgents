import SwiftUI
import Charts

/// Candlestick + indicator chart for the Market Analyst card, built on Apple's
/// `Charts` (iOS 16+). Renders candles (wick + body), SMA/EMA/Bollinger overlays,
/// and an RSI subchart from `GET /api/chart` (`web/charts.py`). This is the
/// native parity for TradingView Lightweight Charts in the web app.
struct PriceChartView: View {
    let payload: ChartPayload

    /// Human labels + colors for the overlay indicator keys we draw.
    private static let overlayStyle: [(key: String, label: String, color: Color)] = [
        ("close_50_sma", "SMA 50", Color(hex: "#38bdf8")),
        ("close_200_sma", "SMA 200", Color(hex: "#a78bfa")),
        ("close_10_ema", "EMA 10", Color(hex: "#fbbf24")),
        ("boll_ub", "Boll Upper", Color(hex: "#94a3b8")),
        ("boll_lb", "Boll Lower", Color(hex: "#94a3b8")),
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(payload.symbol)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(Theme.textPrimary)
                Spacer()
                Text("\(payload.from) → \(payload.to)")
                    .font(.caption2)
                    .foregroundStyle(Theme.textMuted)
            }

            priceChart
                .frame(height: 220)

            legend

            if payload.indicators[ChartPayload.rsiIndicator] != nil {
                Text("RSI")
                    .font(.caption2)
                    .foregroundStyle(Theme.textMuted)
                rsiChart
                    .frame(height: 80)
            }
        }
    }

    private var priceChart: some View {
        Chart {
            ForEach(payload.candles) { candle in
                if let date = candle.date {
                    RuleMark(
                        x: .value("Date", date),
                        yStart: .value("Low", candle.low),
                        yEnd: .value("High", candle.high)
                    )
                    .foregroundStyle(candle.isUp ? Theme.upColor : Theme.downColor)
                    .lineStyle(StrokeStyle(lineWidth: 1))

                    RectangleMark(
                        x: .value("Date", date),
                        yStart: .value("Open", candle.open),
                        yEnd: .value("Close", candle.close),
                        width: .fixed(3)
                    )
                    .foregroundStyle(candle.isUp ? Theme.upColor : Theme.downColor)
                }
            }

            ForEach(Self.overlayStyle, id: \.key) { style in
                ForEach(points(for: style.key)) { point in
                    if let date = point.date {
                        LineMark(
                            x: .value("Date", date),
                            y: .value(style.label, point.value),
                            series: .value("Series", style.label)
                        )
                        .foregroundStyle(style.color)
                        .lineStyle(StrokeStyle(lineWidth: 1.2))
                        .interpolationMethod(.catmullRom)
                    }
                }
            }
        }
        .chartYScale(domain: .automatic(includesZero: false))
        .chartLegend(.hidden)
        .chartXAxis {
            AxisMarks(values: .automatic(desiredCount: 4)) {
                AxisGridLine().foregroundStyle(Theme.border.opacity(0.4))
                AxisValueLabel(format: .dateTime.month().day())
                    .foregroundStyle(Theme.textMuted)
            }
        }
        .chartYAxis {
            AxisMarks(position: .trailing) {
                AxisGridLine().foregroundStyle(Theme.border.opacity(0.4))
                AxisValueLabel().foregroundStyle(Theme.textMuted)
            }
        }
    }

    private var rsiChart: some View {
        Chart {
            RuleMark(y: .value("Overbought", 70)).foregroundStyle(Theme.downColor.opacity(0.4))
                .lineStyle(StrokeStyle(lineWidth: 1, dash: [4]))
            RuleMark(y: .value("Oversold", 30)).foregroundStyle(Theme.upColor.opacity(0.4))
                .lineStyle(StrokeStyle(lineWidth: 1, dash: [4]))
            ForEach(points(for: ChartPayload.rsiIndicator)) { point in
                if let date = point.date {
                    LineMark(
                        x: .value("Date", date),
                        y: .value("RSI", point.value)
                    )
                    .foregroundStyle(Color(hex: "#22d3ee"))
                }
            }
        }
        .chartYScale(domain: 0...100)
        .chartXAxis(.hidden)
        .chartYAxis {
            AxisMarks(position: .trailing, values: [0, 30, 70, 100]) {
                AxisValueLabel().foregroundStyle(Theme.textMuted)
            }
        }
    }

    private var legend: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 12) {
                ForEach(Self.overlayStyle.filter { payload.indicators[$0.key] != nil }, id: \.key) { style in
                    HStack(spacing: 4) {
                        Circle().fill(style.color).frame(width: 8, height: 8)
                        Text(style.label).font(.caption2).foregroundStyle(Theme.textMuted)
                    }
                }
            }
        }
    }

    private func points(for key: String) -> [ChartPayload.IndicatorPoint] {
        payload.indicators[key] ?? []
    }
}
