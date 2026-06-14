import SwiftUI

/// Run economics: totals tiles + a per-agent token/cost breakdown. Ports
/// `renderEconomics` from `index.html` (driven by `usage` / `usage_summary`).
struct EconomicsView: View {
    let usageByNode: [String: UsageEvent]
    let totals: UsageSummaryEvent.UsageTotals?
    let plannedNodes: [String]

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("Run economics", systemImage: "bolt.fill")
                .font(.caption.weight(.semibold))
                .foregroundStyle(Theme.textMuted)

            totalsRow

            VStack(spacing: 8) {
                ForEach(orderedUsage, id: \.node) { usage in
                    perAgentRow(usage)
                }
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 14).fill(Theme.surface.opacity(0.5)))
    }

    /// Per-node usage in pipeline order, with any extra buckets (e.g. "Ungrouped")
    /// appended at the end.
    private var orderedUsage: [UsageEvent] {
        var seen = Set<String>()
        var result: [UsageEvent] = []
        for node in plannedNodes {
            if let usage = usageByNode[node] {
                result.append(usage)
                seen.insert(node)
            }
        }
        for (node, usage) in usageByNode where !seen.contains(node) {
            result.append(usage)
        }
        return result
    }

    private var maxTokens: Int {
        max(1, usageByNode.values.map(\.totalTokens).max() ?? 1)
    }

    private var totalsRow: some View {
        HStack(spacing: 10) {
            tile(title: "Tokens", value: Formatters.tokens(totals?.totalTokens ?? computedTotalTokens))
            tile(title: "Input", value: Formatters.tokens(totals?.inputTokens ?? usageByNode.values.reduce(0) { $0 + $1.inputTokens }))
            tile(title: "Output", value: Formatters.tokens(totals?.outputTokens ?? usageByNode.values.reduce(0) { $0 + $1.outputTokens }))
            tile(title: "Est. cost", value: Formatters.cost(totals?.cost ?? computedCost))
        }
    }

    private var computedTotalTokens: Int {
        usageByNode.values.reduce(0) { $0 + $1.totalTokens }
    }

    private var computedCost: Double? {
        let costs = usageByNode.values.compactMap(\.cost)
        return costs.isEmpty ? nil : costs.reduce(0, +)
    }

    private func tile(title: String, value: String) -> some View {
        VStack(spacing: 2) {
            Text(value).font(.subheadline.weight(.bold).monospacedDigit()).foregroundStyle(Theme.textPrimary)
            Text(title).font(.caption2).foregroundStyle(Theme.textMuted)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 8)
        .background(RoundedRectangle(cornerRadius: 10).fill(Theme.surfaceMuted))
    }

    private func perAgentRow(_ usage: UsageEvent) -> some View {
        let meta = AgentNode.metadata(for: usage.node)
        let fraction = Double(usage.totalTokens) / Double(maxTokens)
        return VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text("\(meta.icon) \(usage.node)")
                    .font(.caption)
                    .foregroundStyle(Theme.textPrimary)
                    .lineLimit(1)
                Spacer()
                Text("\(Formatters.tokens(usage.totalTokens)) · \(Formatters.cost(usage.cost))")
                    .font(.caption2.monospacedDigit())
                    .foregroundStyle(Theme.textMuted)
            }
            GeometryReader { geo in
                Capsule()
                    .fill(Color(hex: meta.colorHex).opacity(0.7))
                    .frame(width: max(2, geo.size.width * fraction), height: 4)
            }
            .frame(height: 4)
        }
    }
}
