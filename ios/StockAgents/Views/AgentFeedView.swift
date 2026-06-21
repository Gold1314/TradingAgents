import SwiftUI

/// The live agent feed: one expandable, markdown-rendered card per agent, in
/// planned pipeline order. Ports `feedCard` / `updateCard` from `index.html`.
/// The price chart is hosted inside the Market Analyst card (web parity).
struct AgentFeedView: View {
    @ObservedObject var viewModel: RunSessionViewModel

    /// Run id required to mint a voice session. When `nil`, the "Talk"
    /// button stays hidden (also gated on `voiceReady`).
    var runID: String?

    /// Whether the server has the voice feature configured. Set by the
    /// parent screen after probing `/api/voice/config` once.
    var voiceReady: Bool = false

    /// Parent callback invoked when the user taps "Talk" on an agent card.
    /// The host (`RunSessionView`) opens a `VoiceCallView` sheet.
    var onTalkRequested: ((String) -> Void)?

    var body: some View {
        VStack(spacing: 12) {
            if let identity = viewModel.identity, !identity.isEmpty {
                identityCard(identity)
            }
            ForEach(viewModel.orderedAgents) { card in
                AgentCardView(
                    card: card,
                    usage: viewModel.usageByNode[card.node],
                    chart: card.node == AgentNode.chartHostNode ? viewModel.chart : nil,
                    chartError: card.node == AgentNode.chartHostNode ? viewModel.chartError : nil,
                    canTalk: voiceReady && runID != nil,
                    onTalk: { onTalkRequested?(card.node) }
                )
            }
        }
    }

    private func identityCard(_ identity: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Label("Instrument identity", systemImage: "tag")
                .font(.caption.weight(.semibold))
                .foregroundStyle(Theme.textMuted)
            Text(identity)
                .font(.footnote)
                .foregroundStyle(Theme.textPrimary)
                .textSelection(.enabled)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 12).fill(Theme.surfaceMuted))
    }
}

/// A single agent card: themed avatar, name + blurb, status, token/cost chip,
/// expandable markdown body, and (for the Market Analyst) the price chart.
struct AgentCardView: View {
    let card: AgentCardState
    let usage: UsageEvent?
    let chart: ChartPayload?
    let chartError: String?

    /// True when the parent screen has probed `/api/voice/config` and the
    /// server reports voice is configured. Combined with `card.status == .done`
    /// to gate the "Talk" button.
    var canTalk: Bool = false

    /// Tap handler for the "Talk" button. The parent opens a sheet hosting
    /// `VoiceCallView` for this agent.
    var onTalk: (() -> Void)?

    @State private var isExpanded = false

    private var meta: AgentNode { card.metadata }
    private var accent: Color { Color(hex: meta.colorHex) }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button {
                isExpanded.toggle()
            } label: {
                header
            }
            .buttonStyle(.plain)

            if isExpanded {
                VStack(alignment: .leading, spacing: 12) {
                    if card.node == AgentNode.chartHostNode {
                        chartSection
                    }
                    if let content = card.content, !content.isEmpty {
                        MarkdownText(source: content)
                    } else if card.status == .running {
                        runningPlaceholder
                    }
                }
                .padding(.top, 12)
            }
        }
        .padding(14)
        .background(RoundedRectangle(cornerRadius: 14).fill(Theme.surface.opacity(0.5)))
        .overlay(
            RoundedRectangle(cornerRadius: 14)
                .stroke(card.status == .done ? accent.opacity(0.5) : Theme.border, lineWidth: 1)
        )
    }

    private var header: some View {
        HStack(spacing: 12) {
            avatar
            VStack(alignment: .leading, spacing: 2) {
                Text(meta.name)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(Theme.textPrimary)
                Text(meta.blurb)
                    .font(.caption)
                    .foregroundStyle(Theme.textMuted)
                    .lineLimit(1)
            }
            Spacer()
            if canTalk && card.status == .done {
                talkButton
            }
            statusBadge
            Image(systemName: isExpanded ? "chevron.up" : "chevron.down")
                .font(.caption)
                .foregroundStyle(Theme.textMuted)
        }
    }

    /// "Talk" affordance that opens the voice call sheet. Hidden until the
    /// agent's report is `done` and the server has voice configured — there's
    /// nothing to discuss before the analysis is in.
    private var talkButton: some View {
        Button {
            onTalk?()
        } label: {
            HStack(spacing: 4) {
                Image(systemName: "waveform.circle.fill")
                Text("Talk")
            }
            .font(.caption2.weight(.semibold))
            .padding(.horizontal, 9)
            .padding(.vertical, 5)
            .background(
                Capsule().fill(Theme.accent.opacity(0.18))
            )
            .overlay(
                Capsule().stroke(Theme.accent.opacity(0.6), lineWidth: 1)
            )
            .foregroundStyle(Theme.accent)
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Talk to \(meta.name)")
    }

    private var avatar: some View {
        Text(meta.icon)
            .font(.title3)
            .frame(width: 40, height: 40)
            .background(
                Circle().fill(
                    RadialGradient(
                        colors: [accent.opacity(0.38), accent.opacity(0.10)],
                        center: .topLeading,
                        startRadius: 0,
                        endRadius: 28
                    )
                )
            )
            .overlay(Circle().stroke(accent.opacity(0.6), lineWidth: 1))
    }

    @ViewBuilder
    private var statusBadge: some View {
        switch card.status {
        case .running:
            HStack(spacing: 4) {
                ProgressView().scaleEffect(0.6)
                Text("running").font(.caption2)
            }
            .foregroundStyle(Theme.warning)
        case .done:
            if let usage {
                VStack(alignment: .trailing, spacing: 1) {
                    Text(Formatters.tokens(usage.totalTokens))
                        .font(.caption2.monospacedDigit())
                    Text(Formatters.cost(usage.cost))
                        .font(.caption2.monospacedDigit())
                }
                .foregroundStyle(Theme.textMuted)
            } else {
                Image(systemName: "checkmark.circle.fill")
                    .foregroundStyle(accent)
            }
        }
    }

    private var runningPlaceholder: some View {
        HStack(spacing: 8) {
            ProgressView().scaleEffect(0.7)
            Text("Working…").font(.footnote).foregroundStyle(Theme.textMuted)
        }
    }

    @ViewBuilder
    private var chartSection: some View {
        if let chart {
            PriceChartView(payload: chart)
        } else if let chartError {
            Text("Chart unavailable: \(chartError)")
                .font(.caption)
                .foregroundStyle(Theme.textMuted)
        } else {
            HStack(spacing: 8) {
                ProgressView().scaleEffect(0.7)
                Text("Loading chart…").font(.caption).foregroundStyle(Theme.textMuted)
            }
        }
    }
}
