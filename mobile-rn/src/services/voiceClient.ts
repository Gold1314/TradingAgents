/**
 * LiveKit React Native wrapper for one in-progress voice call.
 *
 * Mirrors the iOS `VoiceCallClient.swift` so iOS and Android keep behavioural
 * parity: ASR partials/finals from the LiveKit Agents worker arrive as JSON
 * frames on the room's data channel, handoff suggestions arrive there too,
 * and audio flows over WebRTC. The class owns the LiveKit `Room` and the
 * `AudioSession` lifecycle; ``useVoiceSession`` projects its events onto
 * React state.
 *
 * Imports are static. An earlier revision lazy-loaded LiveKit via
 * `await import(...)` to keep the bundle launchable in Expo Go (where the
 * native WebRTC module is absent), but Metro's handling of dynamic imports
 * is fragile: it assigns numeric module IDs that can drift between the
 * running binary and a freshly restarted dev server, surfacing as runtime
 * "unknown module NNN" errors when the user taps Talk. The Expo Go fallback
 * we actually rely on is the server-side capability probe in
 * `GET /api/voice/config` (gating the Talk button itself), so loading the
 * SDK at module scope here is safe — a missing native module on Expo Go
 * fails the JS bundle eval, which is the same loud failure as before.
 */
import {
  Room,
  RoomEvent,
  ConnectionState,
  type RemoteParticipant,
} from 'livekit-client';
import { AudioSession, registerGlobals } from '@livekit/react-native';

// `registerGlobals()` installs WebRTC primitives (RTCPeerConnection,
// MediaStream, getUserMedia, …) onto `globalThis` so the underlying
// `livekit-client` package — which is web-first — can construct rooms.
// Must run before any `new Room()` call. Module-scope means it runs once.
registerGlobals();

/** Discriminated event surface emitted to the React hook. */
export type VoiceClientEvent =
  | { type: 'status'; text: string }
  | { type: 'connected' }
  | { type: 'disconnected'; reason: string | null }
  | { type: 'error'; message: string }
  | {
      type: 'transcript';
      role: 'user' | 'assistant' | 'reconcile';
      text: string;
      isFinal: boolean;
      /** Panel-only: the speaking persona for assistant finals (else null). */
      agentId: string | null;
    }
  | { type: 'handoff'; targetAgentId: string; quote: string }
  | { type: 'reconcileRequested'; objection: string }
  | { type: 'latency'; rttMs: number; turn: number }
  /** Panel-only: the active speaker changed — highlight this persona. */
  | { type: 'panelSpeaker'; agentId: string };

export type VoiceClientListener = (event: VoiceClientEvent) => void;

/**
 * Compatibility shim. With static imports the LiveKit SDK is part of the
 * JS bundle by definition — if the native module weren't linked, the
 * bundle would already have failed at startup. Returning a resolved
 * `true` keeps the existing call-sites (which `await` this) unchanged.
 */
export async function isVoiceTransportAvailable(): Promise<boolean> {
  return true;
}

export interface VoiceClientConfig {
  url: string;
  token: string;
  onEvent: VoiceClientListener;
}

/**
 * One LiveKit room scoped to a single agent voice call. The class is
 * intentionally small — it owns the SDK's stateful `Room` object and forwards
 * the few callbacks the UI cares about. All retry / reconnect logic is left
 * to the SDK; we only emit `status` updates so the UI can hint at it.
 */
export class VoiceClient {
  private room: Room | null = null;
  private muted = false;
  private disposed = false;

  constructor(private readonly config: VoiceClientConfig) {}

  get isMuted(): boolean {
    return this.muted;
  }

  async start(): Promise<void> {
    if (this.disposed) return;
    this.emit({ type: 'status', text: 'Connecting…' });
    try {
      await AudioSession.startAudioSession();
    } catch (err) {
      this.emit({
        type: 'error',
        message: `Audio session failed: ${describe(err)}`,
      });
      return;
    }

    const room = new Room();
    this.room = room;
    this.wireRoomEvents(room);

    try {
      await room.connect(this.config.url, this.config.token);
    } catch (err) {
      this.emit({ type: 'error', message: `Connect failed: ${describe(err)}` });
      await this.safeStopSession();
      return;
    }
    if (this.disposed) {
      await this.hangup();
      return;
    }
    this.emit({ type: 'connected' });
    try {
      await room.localParticipant.setMicrophoneEnabled(true);
    } catch (err) {
      this.emit({ type: 'error', message: `Mic failed: ${describe(err)}` });
    }
  }

  /** Toggle the local mic. Returns the new mute state. */
  async toggleMute(): Promise<boolean> {
    this.muted = !this.muted;
    if (this.room) {
      try {
        await this.room.localParticipant.setMicrophoneEnabled(!this.muted);
      } catch (err) {
        this.emit({ type: 'error', message: `Mute toggle failed: ${describe(err)}` });
      }
    }
    return this.muted;
  }

  /**
   * Panel-only: direct the next answer to a specific panelist. Publishes
   * `{kind:"panel.direct",agent_id}` on the reliable data channel from the
   * local participant. No-op if the room isn't connected yet.
   */
  async publishPanelDirect(agentId: string): Promise<void> {
    if (this.disposed || !this.room) return;
    const bytes = Uint8Array.from(
      new TextEncoder().encode(JSON.stringify({ kind: 'panel.direct', agent_id: agentId })),
    );
    try {
      await this.room.localParticipant.publishData(bytes, { reliable: true });
    } catch (err) {
      this.emit({ type: 'error', message: `Direct failed: ${describe(err)}` });
    }
  }

  /** Disconnect, release native audio session, and stop emitting events. */
  async hangup(): Promise<void> {
    if (this.disposed) return;
    this.disposed = true;
    if (this.room) {
      try {
        await this.room.disconnect();
      } catch {
        // disconnect failures are advisory only — emit a soft disconnect below
      }
      this.room = null;
    }
    await this.safeStopSession();
    this.emit({ type: 'disconnected', reason: 'user-hangup' });
  }

  private async safeStopSession(): Promise<void> {
    try {
      await AudioSession.stopAudioSession();
    } catch {
      // best-effort — we already disconnected the room
    }
  }

  private wireRoomEvents(room: Room): void {
    room.on(RoomEvent.ConnectionStateChanged, (state: ConnectionState) => {
      switch (state) {
        case ConnectionState.Connecting:
        case ConnectionState.Reconnecting:
          this.emit({ type: 'status', text: 'Reconnecting…' });
          break;
        case ConnectionState.Connected:
          this.emit({ type: 'connected' });
          break;
        case ConnectionState.Disconnected:
          this.emit({ type: 'disconnected', reason: null });
          break;
        default:
          // Forward-compat: enum may gain new variants. We deliberately do
          // not use a `never` assertion because `ConnectionState` is an
          // enum we don't own and adding new variants shouldn't break us.
          break;
      }
    });

    room.on(
      RoomEvent.DataReceived,
      (payload: Uint8Array, _participant?: RemoteParticipant) => {
        let json: Record<string, unknown> | null = null;
        try {
          json = JSON.parse(new TextDecoder('utf-8').decode(payload)) as Record<string, unknown>;
        } catch {
          return;
        }
        if (!json) return;
        const kind = json.kind;
        switch (kind) {
          case 'transcript.partial':
            this.emit({
              type: 'transcript',
              role: parseRole(json.role),
              text: typeof json.text === 'string' ? json.text : '',
              isFinal: false,
              agentId: typeof json.agent_id === 'string' ? json.agent_id : null,
            });
            break;
          case 'transcript.final':
            this.emit({
              type: 'transcript',
              role: parseRole(json.role),
              text: typeof json.text === 'string' ? json.text : '',
              isFinal: true,
              // Panel assistant finals carry the speaking persona so the UI can
              // attribute the row; solo finals omit it (→ null).
              agentId: typeof json.agent_id === 'string' ? json.agent_id : null,
            });
            break;
          case 'panel.speaker': {
            const speaker = typeof json.agent_id === 'string' ? json.agent_id : '';
            if (!speaker) break;
            this.emit({ type: 'panelSpeaker', agentId: speaker });
            break;
          }
          case 'handoff.suggested': {
            const target = typeof json.target_agent_id === 'string' ? json.target_agent_id : '';
            if (!target) break;
            this.emit({
              type: 'handoff',
              targetAgentId: target,
              quote: typeof json.quote === 'string' ? json.quote : '',
            });
            break;
          }
          case 'reconcile.requested':
            this.emit({
              type: 'reconcileRequested',
              objection: typeof json.objection === 'string' ? json.objection : '',
            });
            break;
          case 'usage': {
            const rtt = typeof json.rtt_ms === 'number' ? json.rtt_ms : null;
            if (rtt != null) {
              this.emit({
                type: 'latency',
                rttMs: rtt,
                turn: typeof json.turn === 'number' ? json.turn : 0,
              });
            }
            break;
          }
          default:
            // Unknown payload kinds are ignored for forward-compat.
            break;
        }
      },
    );
  }

  private emit(event: VoiceClientEvent): void {
    if (this.disposed && event.type !== 'disconnected') return;
    this.config.onEvent(event);
  }
}

function parseRole(value: unknown): 'user' | 'assistant' | 'reconcile' {
  switch (value) {
    case 'user':
      return 'user';
    case 'reconcile':
      return 'reconcile';
    default:
      return 'assistant';
  }
}

function describe(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err);
}
