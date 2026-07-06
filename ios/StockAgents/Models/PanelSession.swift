import Foundation

/// Wire types for the additive multi-agent PANEL voice API
/// (see `web/voice/router.py` — `POST /api/voice/panels` and
/// `GET /api/voice/personas`).
///
/// A panel is a moderated round-table: 2..N personas share one LiveKit room,
/// one speaks per turn, and the user can auto-route or direct a specific
/// panelist. The LiveKit connection is identical to the solo path — only the
/// room contents (multiple personas + `panel.*` data-channel messages) differ.

/// Body for `POST /api/voice/panels`.
struct PanelSessionRequest: Encodable {
    let runID: String
    let agentIDs: [String]

    enum CodingKeys: String, CodingKey {
        case runID = "run_id"
        case agentIDs = "agent_ids"
    }
}

/// Backend response to `POST /api/voice/panels`.
///
/// `personas` IS the in-call roster (array order = panel order). Connect to the
/// LiveKit room with `url` + `token` exactly like ``VoiceSessionStartResponse``.
struct PanelSessionResponse: Decodable, Equatable {
    let sessionID: String
    let url: String        // LiveKit websocket URL (wss://...)
    let token: String      // Signed room JWT
    let room: String
    let mode: String       // "panel"
    let leadAgentID: String
    let personas: [VoicePersonaPayload]
    let expiresIn: Int
    let sessionMaxSeconds: Int

    enum CodingKeys: String, CodingKey {
        case sessionID = "session_id"
        case url, token, room, mode
        case leadAgentID = "lead_agent_id"
        case personas
        case expiresIn = "expires_in"
        case sessionMaxSeconds = "session_max_seconds"
    }
}

/// Backend response to `GET /api/voice/personas` — the full 12-agent roster the
/// panel picker draws from. Reuses ``VoicePersonaPayload`` (its tolerant decoder
/// makes the absent `tool_grants` default to empty).
struct VoicePersonaListResponse: Decodable, Equatable {
    let personas: [VoicePersonaPayload]
}
