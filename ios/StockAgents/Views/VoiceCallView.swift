import SwiftUI

/// Full-screen sheet shown when the user taps "Talk" on an agent card.
///
/// Renders:
/// * The persona avatar + voice name + connection status.
/// * A scrolling, role-tagged transcript (user / agent / PM-reconcile).
/// * A "bring in <agent>" handoff chip row populated from the data channel.
/// * A reconcile composer (Portfolio Manager only).
/// * Mute and Hangup pills at the bottom.
///
/// When the SwiftUI ``onAppear`` fires we call ``VoiceCallViewModel.start()``,
/// which triggers the mic permission prompt on first run (declared in
/// ``ios/StockAgents/Resources/Info.plist`` via ``NSMicrophoneUsageDescription``).
struct VoiceCallView: View {
    @StateObject var viewModel: VoiceCallViewModel
    @Environment(\.dismiss) private var dismiss

    /// Parent callback invoked when the user picks a handoff target. The
    /// parent (``RunSessionView``) closes this sheet and opens a new one for
    /// the next agent so each call lives in its own room.
    var onHandoffPicked: ((String) -> Void)?

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider().background(Theme.border)
            transcriptScroll
            handoffStrip
            if viewModel.agentID == "Portfolio Manager" {
                reconcileComposer
            }
            controls
        }
        .background(Theme.background)
        .task {
            viewModel.onHandoffPicked = onHandoffPicked
            await viewModel.start()
        }
        .onDisappear {
            // Swipe-to-dismiss bypasses the End-call button; ensure the room,
            // mic, and event stream are always torn down. `hangup()` is
            // idempotent, so this is safe alongside the explicit End-call path.
            Task { await viewModel.hangup() }
        }
    }

    // MARK: - Sections

    private var header: some View {
        HStack(spacing: 14) {
            avatar
            VStack(alignment: .leading, spacing: 2) {
                Text(viewModel.persona?.displayName ?? viewModel.agentID)
                    .font(.headline.weight(.semibold))
                    .foregroundStyle(Theme.textPrimary)
                HStack(spacing: 8) {
                    Text(statusLine)
                        .font(.caption)
                        .foregroundStyle(statusColor)
                    if let rtt = viewModel.lastTurnLatencyMs {
                        // Latency chip — Phase 6 telemetry surfacing the
                        // worker's ``usage`` data-channel event.
                        Text(latencyChipText(last: rtt, p95: viewModel.p95TurnLatencyMs))
                            .font(.caption2.monospacedDigit())
                            .foregroundStyle(Theme.textMuted)
                    }
                }
            }
            Spacer()
            Button(role: .destructive) {
                Task {
                    await viewModel.hangup()
                    dismiss()
                }
            } label: {
                Image(systemName: "xmark.circle.fill")
                    .font(.title2)
                    .foregroundStyle(Theme.danger)
            }
            .accessibilityLabel("End call")
        }
        .padding(.horizontal, 18)
        .padding(.vertical, 16)
        .background(Theme.surface)
    }

    private var avatar: some View {
        let meta = AgentNode.metadata(for: viewModel.agentID)
        return Text(meta.icon)
            .font(.title2)
            .frame(width: 50, height: 50)
            .background(
                Circle().fill(Color(hex: meta.colorHex).opacity(0.18))
            )
            .overlay(
                Circle().stroke(Color(hex: meta.colorHex).opacity(0.55), lineWidth: 1.2)
            )
            .overlay(alignment: .bottomTrailing) {
                Circle()
                    .fill(viewModel.phase == .connected ? .green : .orange)
                    .frame(width: 12, height: 12)
                    .overlay(Circle().stroke(Theme.background, lineWidth: 2))
                    .offset(x: 2, y: 2)
            }
    }

    private var transcriptScroll: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 10) {
                    ForEach(viewModel.transcript) { entry in
                        TranscriptRow(entry: entry).id(entry.id)
                    }
                    if viewModel.transcript.isEmpty {
                        Text(viewModel.persona?.shortIntro ?? "Connecting…")
                            .font(.footnote)
                            .foregroundStyle(Theme.textMuted)
                            .padding(.horizontal, 18)
                            .padding(.top, 16)
                    }
                }
                .padding(.vertical, 14)
            }
            .onChange(of: viewModel.transcript.count) { _, _ in
                if let last = viewModel.transcript.last {
                    withAnimation { proxy.scrollTo(last.id, anchor: .bottom) }
                }
            }
        }
    }

    @ViewBuilder
    private var handoffStrip: some View {
        if !viewModel.handoffs.isEmpty {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(viewModel.handoffs) { h in
                        Button {
                            Task {
                                await viewModel.hangup()
                                onHandoffPicked?(h.targetAgentID)
                                dismiss()
                            }
                        } label: {
                            HStack(spacing: 6) {
                                Image(systemName: "arrow.uturn.right")
                                Text("Talk to \(h.targetAgentID)")
                            }
                            .font(.caption.weight(.semibold))
                            .padding(.horizontal, 12)
                            .padding(.vertical, 7)
                            .background(
                                Capsule().fill(Theme.accent.opacity(0.15))
                            )
                            .overlay(
                                Capsule().stroke(Theme.accent.opacity(0.55), lineWidth: 1)
                            )
                            .foregroundStyle(Theme.accent)
                        }
                    }
                }
                .padding(.horizontal, 18)
            }
            .padding(.vertical, 8)
        }
    }

    @ViewBuilder
    private var reconcileComposer: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Ask the PM to reconcile")
                .font(.caption2.weight(.semibold))
                .foregroundStyle(Theme.textMuted)
                .textCase(.uppercase)
                .tracking(0.8)
            TextField(
                "What's your objection?",
                text: $viewModel.reconcileObjection,
                axis: .vertical
            )
            .lineLimit(2...4)
            .textFieldStyle(.roundedBorder)
            HStack {
                Spacer()
                if viewModel.reconcileInProgress {
                    ProgressView().scaleEffect(0.7)
                }
                Button("Reconcile") {
                    Task { await viewModel.submitReconcile() }
                }
                .disabled(
                    viewModel.reconcileObjection.trimmingCharacters(in: .whitespacesAndNewlines).count < 8
                    || viewModel.reconcileInProgress
                )
                .buttonStyle(.borderedProminent)
                .controlSize(.small)
                .tint(Theme.accent)
            }
            if let result = viewModel.reconcileResult {
                ReconcileResultCard(result: result)
            }
        }
        .padding(.horizontal, 18)
        .padding(.top, 6)
        .padding(.bottom, 4)
    }

    private var controls: some View {
        HStack(spacing: 14) {
            Button {
                Task { await viewModel.toggleMute() }
            } label: {
                Image(systemName: viewModel.isMuted ? "mic.slash.fill" : "mic.fill")
                    .font(.title3)
                    .frame(width: 56, height: 56)
                    .background(
                        Circle().fill(
                            viewModel.isMuted ? Theme.danger.opacity(0.15) : Theme.surfaceMuted
                        )
                    )
                    .overlay(
                        Circle().stroke(
                            viewModel.isMuted ? Theme.danger : Theme.border,
                            lineWidth: 1
                        )
                    )
                    .foregroundStyle(viewModel.isMuted ? Theme.danger : Theme.textPrimary)
            }
            .accessibilityLabel(viewModel.isMuted ? "Unmute mic" : "Mute mic")
            Spacer()
            Button {
                Task {
                    await viewModel.hangup()
                    dismiss()
                }
            } label: {
                HStack(spacing: 8) {
                    Image(systemName: "phone.down.fill")
                    Text("End call")
                }
                .font(.subheadline.weight(.semibold))
                .padding(.horizontal, 18)
                .padding(.vertical, 14)
                .background(Capsule().fill(Theme.danger.opacity(0.85)))
                .foregroundStyle(.white)
            }
        }
        .padding(.horizontal, 18)
        .padding(.vertical, 16)
        .background(Theme.surface)
    }

    // MARK: - Helpers

    private var statusLine: String {
        if case .failed(let msg) = viewModel.phase { return msg }
        return viewModel.status.isEmpty ? "Idle" : viewModel.status
    }

    private func latencyChipText(last: Int, p95: Int?) -> String {
        if let p95 {
            return "Δ \(last)ms · p95 \(p95)"
        }
        return "Δ \(last)ms"
    }

    private var statusColor: Color {
        switch viewModel.phase {
        case .connected:      return Theme.accent
        case .connecting:     return Theme.warning
        case .failed:         return Theme.danger
        case .ended, .idle:   return Theme.textMuted
        }
    }
}

private struct TranscriptRow: View {
    let entry: VoiceTranscriptEntry

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Text(label)
                .font(.system(size: 9, weight: .heavy))
                .tracking(0.6)
                .foregroundStyle(roleColor)
                .padding(.top, 2)
                .frame(width: 64, alignment: .leading)
            Text(entry.text)
                .font(.footnote)
                .foregroundStyle(textColor)
                .opacity(entry.isPartial ? 0.65 : 1)
                .textSelection(.enabled)
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 18)
    }

    private var label: String {
        switch entry.role {
        case .user:       return "YOU"
        case .assistant:  return "AGENT"
        case .reconcile:  return "PM · NEW"
        }
    }

    private var roleColor: Color {
        switch entry.role {
        case .user:       return Theme.textMuted
        case .assistant:  return Theme.accent
        case .reconcile:  return .green
        }
    }

    private var textColor: Color {
        switch entry.role {
        case .user:       return Theme.textPrimary.opacity(0.85)
        case .assistant:  return Theme.textPrimary
        case .reconcile:  return .green.opacity(0.9)
        }
    }
}

private struct ReconcileResultCard: View {
    let result: VoiceReconcileResponse

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                Image(systemName: "sparkles")
                Text(result.flipped ? "Verdict flipped: \(result.updatedDecision ?? "—")" : "Verdict reaffirmed")
                    .font(.caption.weight(.semibold))
            }
            .foregroundStyle(.green)
            Text(result.rationaleMarkdown)
                .font(.footnote)
                .foregroundStyle(Theme.textPrimary)
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 10).fill(.green.opacity(0.10)))
        .overlay(
            RoundedRectangle(cornerRadius: 10)
                .stroke(.green.opacity(0.35), lineWidth: 1)
        )
    }
}
