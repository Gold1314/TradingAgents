import Foundation
#if canImport(LiveKit)
import LiveKit
#endif

/// LiveKit Swift SDK wrapper for one in-progress voice call.
///
/// The class is split out from ``VoiceCallViewModel`` so the view-model stays
/// SwiftUI-friendly (``@MainActor`` ``@Published`` state) while the transport
/// machinery runs as a plain Swift actor. The two communicate via an
/// `AsyncStream` of ``VoiceCallClient.Event`` values — the view-model
/// subscribes once at startCall time and tears down on hangup.
///
/// The ``LiveKit`` import is gated behind ``canImport`` so the project still
/// compiles on hosts that haven't yet added the Swift Package Manager
/// dependency declared in ``ios/project.yml``. With LiveKit absent, ``start``
/// publishes an immediate ``error`` event and the UI shows a clear message
/// rather than crashing.
actor VoiceCallClient {
    /// Events emitted to ``VoiceCallViewModel`` via ``events``.
    enum Event: Equatable {
        case status(String)
        case connected
        case disconnected(reason: String?)
        case error(String)
        /// `agentID` is non-nil only in PANEL mode on `transcript.final` — the
        /// persona who spoke this row. Solo calls and partials leave it nil.
        case transcript(role: VoiceTranscriptEntry.Role, text: String, isFinal: Bool, agentID: String?)
        case handoff(target: String, quote: String)
        case reconcileRequested(objection: String)
        /// Per-turn round-trip ms from the worker's ``usage`` event (Phase 6).
        case latency(rttMillis: Int, turn: Int)
        /// PANEL mode: the persona about to speak this turn (`panel.speaker`).
        case panelSpeaker(agentID: String)
    }

    private(set) var sessionID: String?
    private(set) var isMuted = false
    private let url: String
    private let token: String

    #if canImport(LiveKit)
    private var room: Room?
    private var dataDelegate: DataDelegate?
    #endif

    private var continuation: AsyncStream<Event>.Continuation?
    let events: AsyncStream<Event>

    /// Designated initializer — works for both solo sessions and panels, since
    /// the LiveKit connection only needs the room URL + JWT.
    init(sessionID: String, url: String, token: String) {
        self.sessionID = sessionID
        self.url = url
        self.token = token
        var localCont: AsyncStream<Event>.Continuation!
        self.events = AsyncStream { cont in localCont = cont }
        self.continuation = localCont
    }

    /// Convenience for the solo path.
    convenience init(session: VoiceSessionStartResponse) {
        self.init(sessionID: session.sessionID, url: session.url, token: session.token)
    }

    /// Convenience for the panel path — the room is joined identically.
    convenience init(panel: PanelSessionResponse) {
        self.init(sessionID: panel.sessionID, url: panel.url, token: panel.token)
    }

    /// Start the call. Connects to LiveKit, publishes the microphone, and
    /// subscribes to remote audio + the data channel. All progress reports
    /// (status text, transcript, handoffs) flow through ``events``.
    func start() async {
        #if canImport(LiveKit)
        emit(.status("Connecting…"))
        let room = Room()
        self.room = room
        let delegate = DataDelegate { [weak self] event in
            Task { await self?.emit(event) }
        }
        self.dataDelegate = delegate
        room.add(delegate: delegate)

        do {
            try await room.connect(
                url: url,
                token: token,
                connectOptions: ConnectOptions(autoSubscribe: true)
            )
        } catch {
            emit(.error("Connect failed: \(error.localizedDescription)"))
            return
        }
        emit(.connected)
        do {
            try await room.localParticipant.setMicrophone(enabled: true)
        } catch {
            emit(.error("Mic failed: \(error.localizedDescription)"))
        }
        #else
        emit(.error("LiveKit SDK is not linked. Add it via ios/project.yml SPM deps."))
        #endif
    }

    /// Toggle the local microphone. Returns the new mute state.
    @discardableResult
    func toggleMute() async -> Bool {
        isMuted.toggle()
        #if canImport(LiveKit)
        do {
            try await room?.localParticipant.setMicrophone(enabled: !isMuted)
        } catch {
            emit(.error("Mute toggle failed: \(error.localizedDescription)"))
        }
        #endif
        return isMuted
    }

    /// PANEL mode: direct the NEXT user turn to a specific panelist by
    /// publishing `{ "kind": "panel.direct", "agent_id": <id> }` on the room's
    /// reliable data channel. No-op on the solo path (the worker ignores it).
    ///
    /// LiveKit Swift SDK data-publish API (v2.x): the local participant exposes
    /// `publish(data:options:)` where `DataPublishOptions(reliable: true)`
    /// requests the reliable (ordered, retransmitted) channel. Isolated here so
    /// the one unverified SDK signature lives in a single place — see the report.
    func directPanel(agentID: String) async {
        let payload: [String: Any] = ["kind": "panel.direct", "agent_id": agentID]
        guard let data = try? JSONSerialization.data(withJSONObject: payload) else { return }
        #if canImport(LiveKit)
        do {
            try await room?.localParticipant.publish(
                data: data,
                options: DataPublishOptions(reliable: true)
            )
        } catch {
            emit(.error("Direct failed: \(error.localizedDescription)"))
        }
        #endif
    }

    /// Disconnect and release the room. Idempotent.
    func hangup() async {
        #if canImport(LiveKit)
        await room?.disconnect()
        room = nil
        dataDelegate = nil
        #endif
        emit(.disconnected(reason: "user-hangup"))
        continuation?.finish()
    }

    // MARK: - Internal

    private func emit(_ event: Event) {
        continuation?.yield(event)
    }
}

#if canImport(LiveKit)
/// Bridges LiveKit's room delegate callbacks onto a single closure. We split
/// this off the actor because LiveKit's `RoomDelegate` callbacks may fire on
/// the SDK's own queue, and re-entering the actor from there would deadlock.
private final class DataDelegate: NSObject, RoomDelegate, Sendable {
    private let onEvent: @Sendable (VoiceCallClient.Event) -> Void

    init(onEvent: @escaping @Sendable (VoiceCallClient.Event) -> Void) {
        self.onEvent = onEvent
    }

    func room(_ room: Room, didUpdateConnectionState state: ConnectionState, oldState: ConnectionState) {
        switch state {
        case .connecting, .reconnecting: onEvent(.status("Reconnecting…"))
        case .connected: onEvent(.connected)
        case .disconnected: onEvent(.disconnected(reason: nil))
        @unknown default: onEvent(.status("Unknown connection state"))
        }
    }

    func room(_ room: Room, participant: RemoteParticipant?, didReceiveData data: Data, forTopic topic: String) {
        decode(data: data)
    }

    /// Older SDK signature for data — keep both so we don't depend on one
    /// specific minor version.
    func room(_ room: Room, didReceiveData data: Data, from participant: RemoteParticipant) {
        decode(data: data)
    }

    private func decode(data: Data) {
        guard
            let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let kind = json["kind"] as? String
        else { return }
        switch kind {
        case "handoff.suggested":
            let target = json["target_agent_id"] as? String ?? ""
            let quote = json["quote"] as? String ?? ""
            if !target.isEmpty {
                onEvent(.handoff(target: target, quote: quote))
            }
        case "transcript.partial":
            onEvent(.transcript(
                role: parseRole(json["role"]),
                text: json["text"] as? String ?? "",
                isFinal: false,
                agentID: nil
            ))
        case "transcript.final":
            // In PANEL mode, assistant finals carry `agent_id` = the persona who
            // spoke. Attribute the transcript row to that persona.
            onEvent(.transcript(
                role: parseRole(json["role"]),
                text: json["text"] as? String ?? "",
                isFinal: true,
                agentID: json["agent_id"] as? String
            ))
        case "panel.speaker":
            // NEW: whoever is about to speak this turn → highlight as active.
            let agentID = json["agent_id"] as? String ?? ""
            if !agentID.isEmpty {
                onEvent(.panelSpeaker(agentID: agentID))
            }
        case "reconcile.requested":
            onEvent(.reconcileRequested(objection: json["objection"] as? String ?? ""))
        case "usage":
            if let rtt = json["rtt_ms"] as? Int {
                let turn = (json["turn"] as? Int) ?? 0
                onEvent(.latency(rttMillis: rtt, turn: turn))
            }
        default:
            // Unknown payload kind — ignore (forward-compat).
            break
        }
    }

    private func parseRole(_ value: Any?) -> VoiceTranscriptEntry.Role {
        switch value as? String {
        case "user": return .user
        case "reconcile": return .reconcile
        default: return .assistant
        }
    }
}
#endif
