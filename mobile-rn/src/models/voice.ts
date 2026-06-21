/**
 * Wire types for the additive `/api/voice/*` API (see `web/voice/router.py`).
 *
 * Mirrors the iOS `VoiceSession.swift` and the web `voice-client.js` payloads.
 * The voice layer is purely additive: a Talk button appears on every agent
 * card once the agent reaches `done`, and tapping it `POST`s here to mint a
 * LiveKit room JWT.
 */

export interface VoicePersonaPayload {
  agentId: string;
  displayName: string;
  voiceName: string | null;
  shortIntro: string | null;
  handoffTargets: string[];
  toolGrants: string[];
}

/** Multi-agent panel info attached to a panel-mode session start response. */
export interface VoicePanelInfo {
  agentIds: string[];
  personas: VoicePersonaPayload[];
}

/** Response to `POST /api/voice/sessions`. */
export interface VoiceSessionStartResponse {
  sessionId: string;
  url: string;
  token: string;
  room: string;
  persona: VoicePersonaPayload;
  expiresIn: number;
  sessionMaxSeconds: number;
  /**
   * When the session is a 2-3 persona group call, the full roster + every
   * panelist's persona payload. `null` for the legacy single-agent path so
   * older call-sites can ignore it.
   */
  panel: VoicePanelInfo | null;
}

/** Capability probe (`GET /api/voice/config`) that drives Talk-button visibility. */
export interface VoiceConfig {
  enabled: boolean;
  ready: boolean;
  reason: string | null;
  ttsProvider: string | null;
  sessionMaxSeconds: number | null;
  dailyMinutesPerUser: number | null;
  personasCount: number | null;
  /**
   * Whether the multi-agent panel/group-call mode is enabled on this build.
   * Independent of `enabled` — single-agent voice can be on while the panel
   * rollout is still off. Drives the visibility of the "Group call" button.
   */
  panelEnabled: boolean;
  /** Server-enforced cap on the number of personas one panel can host. */
  panelMaxSize: number;
}

/** One row in the live in-call transcript. Partial rows are coalesced. */
export interface VoiceTranscriptEntry {
  id: string;
  role: 'user' | 'assistant' | 'reconcile';
  text: string;
  isPartial: boolean;
  /**
   * For panel-mode assistant turns, the persona who spoke this utterance.
   * `null` for user/reconcile rows and for single-agent assistant rows
   * (where it's redundant — the persona is the session's only agent).
   */
  speakerAgentId: string | null;
}

/** Handoff target the agent has suggested during a call. */
export interface VoiceHandoff {
  id: string;
  targetAgentId: string;
  quote: string;
}

/** Response to `POST /api/voice/sessions/{id}/reconcile`. */
export interface VoiceReconcileResponse {
  sessionId: string;
  runId: string;
  originalDecision: string | null;
  updatedDecision: string | null;
  rationaleMarkdown: string;
  flipped: boolean;
}

type Json = Record<string, unknown>;

function asString(v: unknown): string | null {
  return typeof v === 'string' ? v : null;
}

function asNumber(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

function asStringArray(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : [];
}

export function parseVoicePersona(raw: unknown): VoicePersonaPayload {
  const obj = (raw ?? {}) as Json;
  return {
    agentId: asString(obj.agent_id) ?? '',
    displayName: asString(obj.display_name) ?? '',
    voiceName: asString(obj.voice_name),
    shortIntro: asString(obj.short_intro),
    handoffTargets: asStringArray(obj.handoff_targets),
    toolGrants: asStringArray(obj.tool_grants),
  };
}

export function parseVoiceSessionStartResponse(raw: unknown): VoiceSessionStartResponse {
  const obj = (raw ?? {}) as Json;
  return {
    sessionId: asString(obj.session_id) ?? '',
    url: asString(obj.url) ?? '',
    token: asString(obj.token) ?? '',
    room: asString(obj.room) ?? '',
    persona: parseVoicePersona(obj.persona),
    expiresIn: asNumber(obj.expires_in) ?? 0,
    sessionMaxSeconds: asNumber(obj.session_max_seconds) ?? 0,
    panel: parseVoicePanel(obj.panel),
  };
}

function parseVoicePanel(raw: unknown): VoicePanelInfo | null {
  if (raw == null) return null;
  const obj = raw as Json;
  const agentIds = asStringArray(obj.agent_ids);
  if (agentIds.length === 0) return null;
  const rawPersonas = Array.isArray(obj.personas) ? obj.personas : [];
  return {
    agentIds,
    personas: rawPersonas.map((p) => parseVoicePersona(p)),
  };
}

export function parseVoiceConfig(raw: unknown): VoiceConfig {
  const obj = (raw ?? {}) as Json;
  return {
    enabled: Boolean(obj.enabled),
    ready: Boolean(obj.ready),
    reason: asString(obj.reason),
    ttsProvider: asString(obj.tts_provider),
    sessionMaxSeconds: asNumber(obj.session_max_seconds),
    dailyMinutesPerUser: asNumber(obj.daily_minutes_per_user),
    personasCount: asNumber(obj.personas_count),
    panelEnabled: Boolean(obj.panel_enabled),
    panelMaxSize: asNumber(obj.panel_max_size) ?? 3,
  };
}

export function parseVoiceReconcileResponse(raw: unknown): VoiceReconcileResponse {
  const obj = (raw ?? {}) as Json;
  return {
    sessionId: asString(obj.session_id) ?? '',
    runId: asString(obj.run_id) ?? '',
    originalDecision: asString(obj.original_decision),
    updatedDecision: asString(obj.updated_decision),
    rationaleMarkdown: asString(obj.rationale_markdown) ?? '',
    flipped: Boolean(obj.flipped),
  };
}
