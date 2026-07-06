import Foundation

/// Wire types for the additive ``/api/voice/*`` API
/// (see ``web/voice/router.py`` for the backend contract).
///
/// Each agent card now sports a "Talk" button gated on the agent reaching
/// ``done``. Tapping it ``POST``s here, the server returns a LiveKit room
/// JWT + URL, and the iOS client joins the room via the Swift SDK.

struct VoicePersonaPayload: Decodable, Equatable, Identifiable {
    let agentID: String
    let displayName: String
    let voiceName: String?
    let shortIntro: String?
    let handoffTargets: [String]
    let toolGrants: [String]

    var id: String { agentID }

    enum CodingKeys: String, CodingKey {
        case agentID = "agent_id"
        case displayName = "display_name"
        case voiceName = "voice_name"
        case shortIntro = "short_intro"
        case handoffTargets = "handoff_targets"
        case toolGrants = "tool_grants"
    }

    /// Custom decoder so one persona model serves BOTH sources:
    /// * `POST /api/voice/panels` personas (carry `tool_grants` + `handoff_targets`)
    /// * `GET /api/voice/personas` list rows (no `tool_grants`)
    /// The two absent-on-one-endpoint arrays default to empty rather than failing.
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        agentID = try c.decode(String.self, forKey: .agentID)
        displayName = try c.decode(String.self, forKey: .displayName)
        voiceName = try c.decodeIfPresent(String.self, forKey: .voiceName)
        shortIntro = try c.decodeIfPresent(String.self, forKey: .shortIntro)
        handoffTargets = try c.decodeIfPresent([String].self, forKey: .handoffTargets) ?? []
        toolGrants = try c.decodeIfPresent([String].self, forKey: .toolGrants) ?? []
    }
}

/// Backend response to ``POST /api/voice/sessions``.
struct VoiceSessionStartResponse: Decodable, Equatable {
    let sessionID: String
    let url: String        // LiveKit websocket URL (wss://...)
    let token: String      // Signed room JWT
    let room: String
    let persona: VoicePersonaPayload
    let expiresIn: Int
    let sessionMaxSeconds: Int

    enum CodingKeys: String, CodingKey {
        case sessionID = "session_id"
        case url, token, room, persona
        case expiresIn = "expires_in"
        case sessionMaxSeconds = "session_max_seconds"
    }
}

/// Backend capability probe for the Talk button visibility.
struct VoiceConfig: Decodable, Equatable {
    let enabled: Bool
    let ready: Bool
    let reason: String?
    let ttsProvider: String?
    let sessionMaxSeconds: Int?
    let dailyMinutesPerUser: Int?
    let personasCount: Int?

    enum CodingKeys: String, CodingKey {
        case enabled, ready, reason
        case ttsProvider = "tts_provider"
        case sessionMaxSeconds = "session_max_seconds"
        case dailyMinutesPerUser = "daily_minutes_per_user"
        case personasCount = "personas_count"
    }
}

/// One row in the live transcript shown on screen.
struct VoiceTranscriptEntry: Identifiable, Equatable {
    enum Role: String, Equatable { case user, assistant, reconcile }

    let id = UUID()
    var role: Role
    var text: String
    /// True while the ASR/TTS engine is still emitting partials for this row.
    /// We coalesce partials into a single row keyed on role.
    var isPartial: Bool
    /// PANEL mode only: the persona (display name) who spoke this assistant row,
    /// carried on `transcript.final` messages. `nil` for solo calls and for user
    /// rows. Defaulted so the solo-path memberwise initializer is unaffected.
    var agentID: String? = nil
}

/// Handoff target the agent suggested during the call.
struct VoiceHandoff: Identifiable, Equatable {
    let id = UUID()
    let targetAgentID: String
    let quote: String
}

/// Backend response to ``POST /api/voice/sessions/{id}/reconcile``.
struct VoiceReconcileResponse: Decodable, Equatable {
    let sessionID: String
    let runID: String
    let originalDecision: String?
    let updatedDecision: String?
    let rationaleMarkdown: String
    let flipped: Bool

    enum CodingKeys: String, CodingKey {
        case sessionID = "session_id"
        case runID = "run_id"
        case originalDecision = "original_decision"
        case updatedDecision = "updated_decision"
        case rationaleMarkdown = "rationale_markdown"
        case flipped
    }
}

/// Body for ``POST /api/voice/sessions``.
struct VoiceSessionRequest: Encodable {
    let runID: String
    let agentID: String

    enum CodingKeys: String, CodingKey {
        case runID = "run_id"
        case agentID = "agent_id"
    }
}

/// Body for ``POST /api/voice/sessions/{id}/reconcile``.
struct VoiceReconcileRequest: Encodable {
    let objection: String
}
