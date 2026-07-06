import SwiftUI

/// Full-screen sheet for a moderated multi-agent PANEL voice call. Mirrors
/// ``VoiceCallView`` but adds a roster strip of panelists (active speaker
/// highlighted, tap to direct the next answer) and attributes each transcript
/// row to the persona who said it.
///
/// `start()` is invoked from `.task` (triggers the mic-permission prompt on
/// first run), and `onDisappear` calls the idempotent `hangup()` so a
/// swipe-dismiss still tears the room down.
struct PanelCallView: View {
    @StateObject var viewModel: PanelCallViewModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider().background(Theme.border)
            rosterStrip
            Divider().background(Theme.border)
            transcriptScroll
            controls
        }
        .background(Theme.background)
        .task {
            await viewModel.start()
        }
        .onDisappear {
            // Swipe-to-dismiss bypasses End call; hangup() is idempotent so this
            // is safe alongside the explicit End-call path.
            Task { await viewModel.hangup() }
        }
    }

    // MARK: - Header

    private var header: some View {
        HStack(spacing: 14) {
            Image(systemName: "person.3.sequence.fill")
                .font(.title2)
                .foregroundStyle(Theme.accent)
                .frame(width: 50, height: 50)
                .background(Circle().fill(Theme.accent.opacity(0.15)))
                .overlay(Circle().stroke(Theme.accent.opacity(0.55), lineWidth: 1.2))
            VStack(alignment: .leading, spacing: 2) {
                Text("Round table")
                    .font(.headline.weight(.semibold))
                    .foregroundStyle(Theme.textPrimary)
                HStack(spacing: 8) {
                    Text(statusLine)
                        .font(.caption)
                        .foregroundStyle(statusColor)
                    if let rtt = viewModel.lastTurnLatencyMs {
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

    // MARK: - Roster

    private var rosterStrip: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 12) {
                ForEach(viewModel.personas) { persona in
                    PanelistChip(
                        persona: persona,
                        isSpeaking: viewModel.activeSpeaker == persona.agentID,
                        isDirected: viewModel.directedAgent == persona.agentID,
                        isLead: viewModel.leadAgentID == persona.agentID
                    ) {
                        Task { await viewModel.direct(agentID: persona.agentID) }
                    }
                }
            }
            .padding(.horizontal, 18)
            .padding(.vertical, 12)
        }
        .background(Theme.surface.opacity(0.4))
    }

    // MARK: - Transcript

    private var transcriptScroll: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 12) {
                    ForEach(viewModel.transcript) { entry in
                        PanelTranscriptRow(
                            entry: entry,
                            displayName: entry.agentID.map(viewModel.displayName)
                        )
                        .id(entry.id)
                    }
                    if viewModel.transcript.isEmpty {
                        Text(hint)
                            .font(.footnote)
                            .foregroundStyle(Theme.textMuted)
                            .padding(.horizontal, 18)
                            .padding(.top, 16)
                    }
                }
                .padding(.vertical, 14)
            }
            .onChange(of: viewModel.transcript.count) { _ in
                if let last = viewModel.transcript.last {
                    withAnimation { proxy.scrollTo(last.id, anchor: .bottom) }
                }
            }
        }
    }

    private var hint: String {
        let names = viewModel.personas.map(\.displayName).joined(separator: ", ")
        if names.isEmpty { return "Connecting…" }
        return "Panel: \(names). Speak any time, or tap a panelist to direct your next question to them."
    }

    // MARK: - Controls

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
        if let p95 { return "Δ \(last)ms · p95 \(p95)" }
        return "Δ \(last)ms"
    }

    private var statusColor: Color {
        switch viewModel.phase {
        case .connected:    return Theme.accent
        case .connecting:   return Theme.warning
        case .failed:       return Theme.danger
        case .ended, .idle: return Theme.textMuted
        }
    }
}

/// One panelist avatar in the roster strip. Highlights the active speaker with a
/// green ring, marks a "directed" (next-to-answer) pick with an accent border,
/// and tags the lead persona.
private struct PanelistChip: View {
    let persona: VoicePersonaPayload
    let isSpeaking: Bool
    let isDirected: Bool
    let isLead: Bool
    let onTap: () -> Void

    private var meta: AgentNode { AgentNode.metadata(for: persona.agentID) }
    private var accent: Color { Color(hex: meta.colorHex) }

    var body: some View {
        Button(action: onTap) {
            VStack(spacing: 5) {
                Text(meta.icon)
                    .font(.title2)
                    .frame(width: 52, height: 52)
                    .background(Circle().fill(accent.opacity(0.18)))
                    .overlay(
                        Circle().stroke(ringColor, lineWidth: isSpeaking ? 2.5 : 1.4)
                    )
                    .overlay(alignment: .bottomTrailing) {
                        if isSpeaking {
                            Image(systemName: "waveform")
                                .font(.system(size: 10, weight: .bold))
                                .foregroundStyle(.white)
                                .padding(4)
                                .background(Circle().fill(.green))
                                .overlay(Circle().stroke(Theme.background, lineWidth: 2))
                                .offset(x: 3, y: 3)
                        }
                    }
                Text(persona.displayName)
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(isSpeaking ? Theme.textPrimary : Theme.textMuted)
                    .lineLimit(1)
                    .frame(maxWidth: 74)
                if isLead {
                    Text("LEAD")
                        .font(.system(size: 8, weight: .heavy))
                        .tracking(0.6)
                        .foregroundStyle(Theme.accent)
                } else if isDirected {
                    Text("NEXT")
                        .font(.system(size: 8, weight: .heavy))
                        .tracking(0.6)
                        .foregroundStyle(Theme.warning)
                } else {
                    Text(" ")
                        .font(.system(size: 8, weight: .heavy))
                }
            }
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Direct next question to \(persona.displayName)")
    }

    private var ringColor: Color {
        if isSpeaking { return .green }
        if isDirected { return Theme.warning }
        return accent.opacity(0.55)
    }
}

/// A transcript row attributed to its speaker: the panelist avatar + name for
/// assistant rows, "YOU" for the user.
private struct PanelTranscriptRow: View {
    let entry: VoiceTranscriptEntry
    /// Resolved persona display name for assistant rows (nil for user rows).
    let displayName: String?

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            speakerTag
            Text(entry.text)
                .font(.footnote)
                .foregroundStyle(entry.role == .user ? Theme.textPrimary.opacity(0.85) : Theme.textPrimary)
                .opacity(entry.isPartial ? 0.65 : 1)
                .textSelection(.enabled)
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 18)
    }

    @ViewBuilder
    private var speakerTag: some View {
        if entry.role == .user {
            Text("YOU")
                .font(.system(size: 9, weight: .heavy))
                .tracking(0.6)
                .foregroundStyle(Theme.textMuted)
                .padding(.top, 2)
                .frame(width: 72, alignment: .leading)
        } else {
            let name = displayName ?? "Panel"
            let meta = AgentNode.metadata(for: entry.agentID ?? name)
            HStack(spacing: 5) {
                Text(meta.icon).font(.caption)
                Text(name)
                    .font(.system(size: 9, weight: .heavy))
                    .tracking(0.4)
                    .foregroundStyle(Color(hex: meta.colorHex))
                    .lineLimit(1)
            }
            .padding(.top, 2)
            .frame(width: 72, alignment: .leading)
        }
    }
}

/// Multi-select sheet for starting a panel. Lists the run's finished agents; the
/// user picks 2..N, then taps Start. Presented from ``RunSessionView``.
struct PanelSetupSheet: View {
    /// Finished agents available for the panel, in pipeline order (display names).
    let finishedAgents: [String]
    /// Invoked with the chosen roster (>= 2) when the user taps Start.
    let onStart: ([String]) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var selected: Set<String> = []

    var body: some View {
        NavigationStack {
            List {
                Section {
                    ForEach(finishedAgents, id: \.self) { agent in
                        let meta = AgentNode.metadata(for: agent)
                        Button {
                            toggle(agent)
                        } label: {
                            HStack(spacing: 12) {
                                Text(meta.icon)
                                    .font(.title3)
                                    .frame(width: 36, height: 36)
                                    .background(Circle().fill(Color(hex: meta.colorHex).opacity(0.18)))
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
                                Image(systemName: selected.contains(agent) ? "checkmark.circle.fill" : "circle")
                                    .foregroundStyle(selected.contains(agent) ? Theme.accent : Theme.textMuted)
                            }
                        }
                        .buttonStyle(.plain)
                        .listRowBackground(Theme.surface.opacity(0.5))
                    }
                } header: {
                    Text("Pick 2 or more agents for the round table")
                } footer: {
                    if finishedAgents.count < 2 {
                        Text("At least two agents must have finished before you can start a panel.")
                    }
                }
            }
            .scrollContentBackground(.hidden)
            .background(Theme.background.ignoresSafeArea())
            .navigationTitle("Round table")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Start") {
                        onStart(orderedSelection)
                    }
                    .disabled(selected.count < 2)
                }
            }
        }
    }

    /// Preserve pipeline order (panel order) from the finished-agents list.
    private var orderedSelection: [String] {
        finishedAgents.filter { selected.contains($0) }
    }

    private func toggle(_ agent: String) {
        if selected.contains(agent) {
            selected.remove(agent)
        } else {
            selected.insert(agent)
        }
    }
}
