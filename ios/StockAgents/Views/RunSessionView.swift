import SwiftUI

/// The live run screen: status header, optional cache banner, stage stepper,
/// verdict hero, run economics, and the streaming agent feed (with the price
/// chart hosted inside the Market Analyst card). Container for the MVP.
struct RunSessionView: View {
    @EnvironmentObject private var env: AppEnvironment
    @ObservedObject var viewModel: RunSessionViewModel
    let onNewRun: () -> Void

    @Environment(\.scenePhase) private var scenePhase

    @State private var showOrderTicket = false
    @State private var voiceReady = false
    @State private var voiceAgentID: String?
    @State private var showPanelPicker = false
    @State private var panelSelection: PanelSelection?

    var body: some View {
        ScrollView {
            VStack(spacing: 16) {
                header

                if viewModel.isCached {
                    cacheBanner
                }

                if case .failed(let message) = viewModel.phase {
                    errorBanner(message)
                }

                StageStepperView(viewModel: viewModel)

                VerdictView(
                    decision: viewModel.decision,
                    verdict: viewModel.verdict,
                    rationale: viewModel.rationale,
                    isStreaming: viewModel.phase == .streaming || viewModel.phase == .connecting
                )

                if !viewModel.usageByNode.isEmpty || viewModel.usageTotals != nil {
                    EconomicsView(
                        usageByNode: viewModel.usageByNode,
                        totals: viewModel.usageTotals,
                        plannedNodes: viewModel.plannedNodes
                    )
                }

                orderTicketEntry

                AgentFeedView(
                    viewModel: viewModel,
                    runID: viewModel.runID,
                    voiceReady: voiceReady,
                    onTalkRequested: { agent in voiceAgentID = agent }
                )
            }
            .padding()
        }
        .background(Theme.background.ignoresSafeArea())
        .navigationTitle(navigationTitle)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            // "Round table" — start a moderated multi-agent PANEL. Shown only
            // once voice is configured and at least two agents have finished
            // (the panel needs 2..N finished reports; the server 409s otherwise).
            if voiceReady, viewModel.runID != nil, finishedAgents.count >= 2 {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button {
                        showPanelPicker = true
                    } label: {
                        Image(systemName: "person.3.sequence.fill")
                    }
                    .accessibilityLabel("Start a panel")
                }
            }
            ToolbarItem(placement: .navigationBarTrailing) {
                Button("New run", action: onNewRun)
            }
        }
        .task {
            // One-shot capability probe — feature flag for the Talk button.
            // Failures degrade silently to "voice unavailable" so the button
            // simply doesn't appear; the rest of the run UI is unaffected.
            voiceReady = (try? await env.api.fetchVoiceConfig().ready) ?? false
        }
        .onDisappear { viewModel.stop() }
        .onChange(of: scenePhase) { newPhase in
            // Returning to the foreground: the SSE socket may have been suspended
            // and the stream dropped while backgrounded. Re-sync so a long run
            // that hit its reconnect budget mid-background recovers.
            if newPhase == .active {
                Task { await viewModel.resyncOnForeground() }
            }
        }
        .sheet(isPresented: $showOrderTicket) {
            if let runID = viewModel.runID, let trade = viewModel.lastTrade {
                OrderTicketView(
                    viewModel: env.makeOrderTicketViewModel(runID: runID, trade: trade),
                    makeBrokerConnectViewModel: env.makeBrokerConnectViewModel
                )
            }
        }
        .sheet(item: voiceSheetBinding) { selection in
            VoiceCallView(
                viewModel: VoiceCallViewModel(
                    runID: viewModel.runID ?? "",
                    agentID: selection.agentID,
                    api: env.api
                ),
                onHandoffPicked: { nextAgent in
                    // Close and reopen for the next persona. SwiftUI requires
                    // a one-cycle gap so the existing sheet fully dismisses
                    // before the next presentation; ``DispatchQueue.main``
                    // suffices.
                    voiceAgentID = nil
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) {
                        voiceAgentID = nextAgent
                    }
                }
            )
        }
        .sheet(isPresented: $showPanelPicker) {
            PanelSetupSheet(finishedAgents: finishedAgents) { agentIDs in
                // Dismiss the picker, then present the call after a one-cycle
                // gap so the first sheet fully tears down before the second.
                showPanelPicker = false
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) {
                    panelSelection = PanelSelection(agentIDs: agentIDs)
                }
            }
        }
        .sheet(item: $panelSelection) { selection in
            PanelCallView(
                viewModel: PanelCallViewModel(
                    runID: viewModel.runID ?? "",
                    agentIDs: selection.agentIDs,
                    api: env.api
                )
            )
        }
    }

    /// Finished agents (report `done`) in pipeline order — the roster the panel
    /// picker offers. Reuses the same `done`-gating the solo Talk buttons apply.
    private var finishedAgents: [String] {
        viewModel.orderedAgents.filter { $0.status == .done }.map { $0.node }
    }

    /// Reshape `voiceAgentID` into a `Binding<Identifiable?>` so SwiftUI's
    /// `sheet(item:)` API can drive presentation. We use a lightweight
    /// `Identifiable` wrapper rather than relying on `String: Identifiable`
    /// (which Swift doesn't provide out of the box) and keep all the state
    /// in this view so the parent navigator stays unaware of voice.
    private var voiceSheetBinding: Binding<VoiceAgentSelection?> {
        Binding(
            get: { voiceAgentID.map(VoiceAgentSelection.init) },
            set: { voiceAgentID = $0?.agentID }
        )
    }

    /// Appears once a finished run proposes an actionable buy/sell. Opens the
    /// biometric-gated order ticket (plan §5.3). Hidden while the run streams.
    @ViewBuilder
    private var orderTicketEntry: some View {
        if viewModel.phase == .finished,
           let trade = viewModel.lastTrade,
           let intent = trade.intent,
           intent.isActionable {
            let live = (trade.canPlaceRealOrders ?? false) && !(trade.dryRun ?? true)
            Button {
                showOrderTicket = true
            } label: {
                HStack(spacing: 10) {
                    Image(systemName: live ? "exclamationmark.octagon.fill" : "cart.fill")
                    VStack(alignment: .leading, spacing: 2) {
                        Text("\(intent.action.capitalized) \(intent.ticker)")
                            .font(.subheadline.weight(.semibold))
                        Text(live ? "Review LIVE order — Face ID required" : "Review proposed order")
                            .font(.caption)
                            .foregroundStyle(Theme.textMuted)
                    }
                    Spacer()
                    Image(systemName: "chevron.right").foregroundStyle(Theme.textMuted)
                }
                .padding(14)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(
                    RoundedRectangle(cornerRadius: 12)
                        .fill((live ? Theme.danger : Theme.accent).opacity(0.14))
                )
                .overlay(
                    RoundedRectangle(cornerRadius: 12)
                        .stroke(live ? Theme.danger : Theme.accent, lineWidth: 1)
                )
            }
            .buttonStyle(.plain)
            .foregroundStyle(Theme.textPrimary)
        }
    }

    private var navigationTitle: String {
        guard let request = viewModel.request else { return "Run" }
        return "\(request.ticker) · \(request.tradeDate)"
    }

    private var header: some View {
        HStack(spacing: 10) {
            statusDot
            Text(statusText)
                .font(.subheadline.weight(.medium))
                .foregroundStyle(Theme.textPrimary)
            Spacer()
            if viewModel.phase == .streaming || viewModel.phase == .connecting {
                ElapsedTimerView()
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 12).fill(Theme.surface.opacity(0.5)))
    }

    private var statusDot: some View {
        Circle()
            .fill(statusColor)
            .frame(width: 10, height: 10)
    }

    private var statusColor: Color {
        switch viewModel.phase {
        case .idle, .connecting: return Theme.warning
        case .streaming: return Theme.accent
        case .finished: return Theme.accent
        case .failed: return Theme.danger
        }
    }

    private var statusText: String {
        switch viewModel.phase {
        case .idle: return "Idle"
        case .connecting: return "Connecting…"
        case .streaming: return viewModel.statusMessage.isEmpty ? "Running…" : viewModel.statusMessage
        case .finished: return viewModel.statusMessage.isEmpty ? "Complete" : viewModel.statusMessage
        case .failed: return "Failed"
        }
    }

    private var cacheBanner: some View {
        Label {
            Text("Served from the 60-minute cache" + (viewModel.cacheCreatedAt.map { " (\($0))" } ?? "") + ".")
                .font(.footnote)
        } icon: {
            Image(systemName: "clock.arrow.circlepath")
        }
        .foregroundStyle(Theme.warning)
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 10).fill(Theme.warning.opacity(0.12)))
    }

    private func errorBanner(_ message: String) -> some View {
        Label(message, systemImage: "exclamationmark.triangle.fill")
            .font(.footnote)
            .foregroundStyle(Theme.danger)
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 10).fill(Theme.danger.opacity(0.12)))
    }
}

/// Lightweight Identifiable wrapper so `voiceAgentID: String?` can drive
/// SwiftUI's `sheet(item:)`. Reset to nil from the parent dismisses the sheet.
private struct VoiceAgentSelection: Identifiable {
    let agentID: String
    var id: String { agentID }
}

/// Identifiable wrapper so the chosen panel roster can drive `sheet(item:)`.
private struct PanelSelection: Identifiable {
    let agentIDs: [String]
    var id: String { agentIDs.joined(separator: "|") }
}

/// Tiny elapsed-time chip that ticks once a second while a run streams.
private struct ElapsedTimerView: View {
    private let start = Date()

    var body: some View {
        TimelineView(.periodic(from: start, by: 1)) { context in
            let elapsed = Int(context.date.timeIntervalSince(start))
            Text(String(format: "%d:%02d", elapsed / 60, elapsed % 60))
                .font(.subheadline.monospacedDigit())
                .foregroundStyle(Theme.textMuted)
        }
    }
}
