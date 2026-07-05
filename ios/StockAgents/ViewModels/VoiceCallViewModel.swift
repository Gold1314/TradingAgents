import Foundation
import SwiftUI

/// State machine for a single voice call. Owns one ``VoiceCallClient`` and
/// projects its event stream onto SwiftUI-friendly ``@Published`` state.
///
/// Lifecycle:
///   1. ``init(runID:agentID:api:)`` captures the run+agent context.
///   2. ``start()`` calls the backend to mint a room JWT and connects.
///   3. The UI displays ``status``, ``transcript``, ``handoffs``.
///   4. ``hangup()`` disconnects; the view dismisses.
@MainActor
final class VoiceCallViewModel: ObservableObject {
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
    @Published private(set) var handoffs: [VoiceHandoff] = []
    @Published private(set) var isMuted = false
    @Published private(set) var persona: VoicePersonaPayload?
    @Published private(set) var reconcileResult: VoiceReconcileResponse?
    @Published private(set) var reconcileInProgress = false
    @Published var reconcileObjection: String = ""
    /// Rolling worst-case turn latency in ms (Phase 6 latency tracing).
    /// ``nil`` until the worker has emitted at least one ``usage`` event.
    @Published private(set) var lastTurnLatencyMs: Int?
    @Published private(set) var p95TurnLatencyMs: Int?
    private var latencySamples: [Int] = []

    let runID: String
    let agentID: String
    private let api: APIClient

    /// Called by the view when the user accepts a handoff chip. Set from the
    /// host so the parent can spin up a *new* `VoiceCallViewModel` for the
    /// target agent (the backend's room model is one-agent-per-room).
    var onHandoffPicked: ((String) -> Void)?

    private var client: VoiceCallClient?
    private var startedSession: VoiceSessionStartResponse?
    private var eventTask: Task<Void, Never>?
    /// Guards ``hangup()`` so the explicit End-call button and the view's
    /// `onDisappear` can't double-disconnect the room.
    private var didHangUp = false

    init(runID: String, agentID: String, api: APIClient) {
        self.runID = runID
        self.agentID = agentID
        self.api = api
    }

    deinit {
        eventTask?.cancel()
    }

    /// Begin the call. Idempotent — calling twice does nothing.
    func start() async {
        guard phase == .idle else { return }
        phase = .connecting
        status = "Requesting session…"
        do {
            let session = try await api.startVoiceSession(runID: runID, agentID: agentID)
            startedSession = session
            persona = session.persona
            let client = VoiceCallClient(session: session)
            self.client = client
            let eventStream = await client.events
            eventTask = Task { [weak self] in
                await self?.pump(stream: eventStream)
            }
            await client.start()
        } catch {
            phase = .failed((error as? APIError)?.userMessage ?? error.localizedDescription)
            status = "Couldn't start voice"
        }
    }

    /// Toggle the local microphone.
    func toggleMute() async {
        guard let client else { return }
        isMuted = await client.toggleMute()
    }

    /// Hang up and dismiss. Idempotent — safe to call multiple times and from
    /// both the End-call button and the view's `onDisappear` (swipe-dismiss), so
    /// the room/mic teardown always runs and never runs twice.
    func hangup() async {
        guard !didHangUp else { return }
        didHangUp = true
        await client?.hangup()
        client = nil
        phase = .ended
        status = "Call ended"
        eventTask?.cancel()
    }

    /// Submit the user's objection to the PM and surface the updated rationale.
    /// Only meaningful when ``agentID == "Portfolio Manager"`` — the backend
    /// returns 409 otherwise (mapped to a ``failed`` phase but not a hangup).
    func submitReconcile() async {
        guard let session = startedSession else { return }
        let text = reconcileObjection.trimmingCharacters(in: .whitespacesAndNewlines)
        guard text.count >= 8 else { return }
        reconcileInProgress = true
        defer { reconcileInProgress = false }
        do {
            let result = try await api.reconcilePortfolioManager(
                sessionID: session.sessionID,
                objection: text
            )
            reconcileResult = result
            transcript.append(
                VoiceTranscriptEntry(
                    role: .reconcile,
                    text: result.rationaleMarkdown,
                    isPartial: false
                )
            )
        } catch {
            transcript.append(
                VoiceTranscriptEntry(
                    role: .reconcile,
                    text: "Reconcile failed: \((error as? APIError)?.userMessage ?? error.localizedDescription)",
                    isPartial: false
                )
            )
        }
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
            status = "Connected — speak any time"
        case .disconnected(let reason):
            phase = .ended
            status = reason ?? "Call ended"
        case .error(let message):
            phase = .failed(message)
            status = message
        case .transcript(let role, let text, let isFinal):
            appendTranscript(role: role, text: text, isFinal: isFinal)
        case .handoff(let target, let quote):
            if !handoffs.contains(where: { $0.targetAgentID == target }) {
                handoffs.append(VoiceHandoff(targetAgentID: target, quote: quote))
            }
        case .reconcileRequested(let objection):
            if reconcileObjection.isEmpty {
                reconcileObjection = objection
            }
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

    private func appendTranscript(role: VoiceTranscriptEntry.Role, text: String, isFinal: Bool) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty { return }
        if let idx = transcript.lastIndex(where: { $0.role == role && $0.isPartial }) {
            if isFinal {
                transcript[idx].text = trimmed
                transcript[idx].isPartial = false
            } else {
                transcript[idx].text = trimmed
            }
        } else {
            transcript.append(
                VoiceTranscriptEntry(role: role, text: trimmed, isPartial: !isFinal)
            )
        }
    }
}

private extension APIError {
    var userMessage: String {
        switch self {
        case .unauthorized:           return "Sign in expired — please sign in again."
        case .rateLimited(let after): return after.map { "Rate limited (retry in \($0)s)" } ?? "Rate limited"
        case .http(_, let detail):    return detail ?? "Request failed"
        case .invalidURL:             return "Invalid URL"
        case .transport(let msg):     return msg
        case .decoding:               return "Decoding failed"
        case .cancelled:              return "Cancelled"
        }
    }
}
