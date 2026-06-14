import SwiftUI

/// Verdict hero: the large rating word, a Sell→Buy gradient gauge with a marker,
/// and an expandable PM rationale. Ports `showVerdict` + the gauge from
/// `index.html` (`RATING_POS` / `RATING_COLOR`).
struct VerdictView: View {
    let decision: String?
    let verdict: Verdict?
    let rationale: String?
    let isStreaming: Bool

    @State private var showRationale = false

    private static let gaugeColors = [
        Color(hex: "#f43f5e"), // Sell
        Color(hex: "#fb923c"), // Underweight
        Color(hex: "#94a3b8"), // Hold
        Color(hex: "#4ade80"), // Overweight
        Color(hex: "#10b981"), // Buy
    ]

    var body: some View {
        VStack(spacing: 14) {
            Text(decision ?? (isStreaming ? "…" : "—"))
                .font(.system(size: 38, weight: .heavy, design: .rounded))
                .foregroundStyle(verdict.map { Color(hex: $0.colorHex) } ?? Theme.textMuted)

            gauge

            HStack {
                ForEach(Verdict.allCases, id: \.self) { tier in
                    Text(tier.rawValue)
                        .font(.caption2)
                        .foregroundStyle(verdict == tier ? Color(hex: tier.colorHex) : Theme.textMuted)
                        .frame(maxWidth: .infinity)
                }
            }

            if let rationale, !rationale.isEmpty {
                DisclosureGroup("Portfolio Manager rationale", isExpanded: $showRationale) {
                    MarkdownText(source: rationale)
                        .padding(.top, 6)
                }
                .font(.subheadline.weight(.medium))
                .foregroundStyle(Theme.textPrimary)
            } else if isStreaming {
                Text("Agents are deliberating…")
                    .font(.footnote)
                    .foregroundStyle(Theme.textMuted)
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity)
        .background(RoundedRectangle(cornerRadius: 16).fill(Theme.surface.opacity(0.5)))
    }

    private var gauge: some View {
        GeometryReader { geo in
            let width = geo.size.width
            ZStack(alignment: .leading) {
                Capsule()
                    .fill(LinearGradient(colors: Self.gaugeColors, startPoint: .leading, endPoint: .trailing))
                    .frame(height: 10)

                if let verdict {
                    let x = width * CGFloat(verdict.gaugePosition / 100.0)
                    Circle()
                        .fill(Color.white)
                        .frame(width: 18, height: 18)
                        .overlay(Circle().stroke(Color(hex: verdict.colorHex), lineWidth: 3))
                        .position(x: min(max(9, x), width - 9), y: 5)
                }
            }
        }
        .frame(height: 18)
    }
}
