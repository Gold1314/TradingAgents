import Foundation
import SwiftUI

/// State machine for a moderated multi-agent PANEL voice call. Mirrors
/// ``VoiceCallViewModel`` but joins a room shared by 2..N personas: exactly one
/// speaks per turn, the worker tells us who via `panel.speaker`, and the user
/// can direct the next answer to a specific panelist via `panel.direct`.
///
/// Lifecycle:
///   1. ``init(runID:agentIDs:api:)`` captures the run + roster the user picked.
///   2. ``start()`` mints the panel room JWT and connects the SAME
///      ``VoiceCallClient`` used by the solo path.
///   3. The UI renders ``personas``, ``activeSpeaker``, and the per-persona
///      attributed ``transcript``.
///   4. ``hangup()`` disconnects; the view dismisses. Idempotent.
@MainActor
final class PanelCallViewModel: ObservableObject {
    enum Phase: Equatable {
        case idle
        case connecting
        case connected
        case ended
        case failed(String)
    }

    @Published private(set) var phase: Phase = .idle
    @Published private(set) var status: String = ""
    @Published private(set) var transcript: [VoiceTranscriptEntry] = []
    @Published private(set) var isMuted = false
    /// The in-call roster (order = panel order) from the backend response.
    @Published private(set) var personas: [VoicePersonaPayload] = []
    /// The lead / moderator persona (`lead_agent_id`).
    @Published private(set) var leadAgentID: String?
    /// The persona currently speaking (updated from `panel.speaker`).
    @Published private(set) var activeSpeaker: String?
    /// The persona the user tapped to answer next (optimistic; cleared when a
    /// new speaker takes the floor).
    @Published private(set) var directedAgent: String?
    /// Rolling latency telemetry (Phase 6 `usage` events).
    @Published private(set) var lastTurnLatencyMs: Int?
    @Published private(set) var p95TurnLatencyMs: Int?
    private var latencySamples: [Int] = []

    let runID: String
    let agentIDs: [String]
    private let api: APIClient

    private var client: VoiceCallClient?
    private var startedSession: PanelSessionResponse?
    private var eventTask: Task<Void, Never>?
    /// Guards ``hangup()`` so the End-call button and the view's `onDisappear`
    /// can't double-disconnect the room.
    private var didHangUp = false

    init(runID: String, agentIDs: [String], api: APIClient) {
        self.runID = runID
        self.agentIDs = agentIDs
        self.api = api
    }

    deinit {
        eventTask?.cancel()
    }

    /// Look up a persona's display name for a transcript row, falling back to the
    /// raw id (which is already a display name in this backend).
    func displayName(for agentID: String) -> String {
        personas.first(where: { $0.agentID == agentID })?.displayName ?? agentID
    }

    /// Begin the panel. Idempotent — calling twice does nothing.
    func start() async {
        guard phase == .idle else { return }
        phase = .connecting
        status = "Requesting panel…"
        do {
            let session = try await api.startPanelSession(runID: runID, agentIDs: agentIDs)
            startedSession = session
            personas = session.personas
            leadAgentID = session.leadAgentID
            activeSpeaker = session.leadAgentID
            let client = VoiceCallClient(panel: session)
            self.client = client
            let eventStream = await client.events
            eventTask = Task { [weak self] in
                await self?.pump(stream: eventStream)
            }
            await client.start()
        } catch {
            phase = .failed((error as? APIError)?.errorDescription ?? error.localizedDescription)
            status = "Couldn't start panel"
        }
    }

    /// Toggle the local microphone.
    func toggleMute() async {
        guard let client else { return }
        isMuted = await client.toggleMute()
    }

    /// Direct the NEXT user turn to a specific panelist. Publishes `panel.direct`
    /// on the data channel; the worker routes accordingly.
    func direct(agentID: String) async {
        guard let client else { return }
        directedAgent = agentID
        await client.directPanel(agentID: agentID)
    }

    /// Hang up and dismiss. Idempotent — safe from both the End-call button and
    /// the view's `onDisappear` so the room/mic teardown always runs exactly once.
    func hangup() async {
        guard !didHangUp else { return }
        didHangUp = true
        await client?.hangup()
        client = nil
        phase = .ended
        status = "Call ended"
        eventTask?.cancel()
    }

    // MARK: - Private

    private func pump(stream: AsyncStream<VoiceCallClient.Event>) async {
        for await event in stream {
            await apply(event)
        }
    }

    private func apply(_ event: VoiceCallClient.Event) async {
        switch event {
        case .status(let text):
            status = text
        case .connected:
            phase = .connected
            status = "Connected — the panel is listening"
        case .disconnected(let reason):
            phase = .ended
            status = reason ?? "Call ended"
        case .error(let message):
            phase = .failed(message)
            status = message
        case .transcript(let role, let text, let isFinal, let agentID):
            appendTranscript(role: role, text: text, isFinal: isFinal, agentID: agentID)
        case .panelSpeaker(let agentID):
            activeSpeaker = agentID
            // A new speaker has the floor — clear any pending direct hint once it
            // matches (the worker honored the request) or a different agent speaks.
            if directedAgent == agentID { directedAgent = nil }
        case .handoff:
            break // no redial in a panel; harmless to ignore
        case .reconcileRequested:
            break // panel has no PM-reconcile composer
        case .latency(let rtt, _):
            lastTurnLatencyMs = rtt
            latencySamples.append(rtt)
            if latencySamples.count > 32 {
                latencySamples.removeFirst(latencySamples.count - 32)
            }
            p95TurnLatencyMs = percentile(latencySamples, 95)
        }
    }

    private func percentile(_ samples: [Int], _ p: Int) -> Int? {
        guard !samples.isEmpty else { return nil }
        let sorted = samples.sorted()
        let idx = min(sorted.count - 1, (p * sorted.count) / 100)
        return sorted[idx]
    }

    /// Coalesce partials into a single row keyed on role. Assistant finals carry
    /// the speaking persona's id; we pin it onto the row for attribution.
    private func appendTranscript(role: VoiceTranscriptEntry.Role, text: String, isFinal: Bool, agentID: String?) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty { return }
        if let idx = transcript.lastIndex(where: { $0.role == role && $0.isPartial }) {
            transcript[idx].text = trimmed
            if isFinal {
                transcript[idx].isPartial = false
                if let agentID { transcript[idx].agentID = agentID }
            }
        } else {
            transcript.append(
                VoiceTranscriptEntry(
                    role: role,
                    text: trimmed,
                    isPartial: !isFinal,
                    agentID: agentID
                )
            )
        }
    }
}
