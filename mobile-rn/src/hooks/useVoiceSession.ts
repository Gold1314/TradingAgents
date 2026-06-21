import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { apiClient } from '../services/apiClient';
import {
  VoiceClient,
  type VoiceClientEvent,
} from '../services/voiceClient';
import type {
  VoiceHandoff,
  VoicePersonaPayload,
  VoiceReconcileResponse,
  VoiceSessionStartResponse,
  VoiceTranscriptEntry,
} from '../models/voice';
import { describeError } from '../services/apiError';

/**
 * Phase the in-call screen is in. Determines which controls are enabled and
 * which copy is shown in the status pill.
 */
export type VoicePhase =
  | 'idle'
  | 'minting'
  | 'connecting'
  | 'live'
  | 'ended'
  | 'failed';

export interface VoiceSessionState {
  phase: VoicePhase;
  status: string;
  errorMessage: string | null;
  persona: VoicePersonaPayload | null;
  sessionId: string | null;
  /** Live transcript, partial rows coalesced per role. */
  transcript: VoiceTranscriptEntry[];
  handoffs: VoiceHandoff[];
  reconcile: VoiceReconcileResponse | null;
  reconcileBusy: boolean;
  reconcilePrefill: string;
  muted: boolean;
  /** Rolling latency telemetry (Phase 6). `null` until first sample. */
  lastTurnLatencyMs: number | null;
  p95TurnLatencyMs: number | null;
}

export interface VoiceSessionActions {
  start: () => Promise<void>;
  hangup: () => Promise<void>;
  toggleMute: () => Promise<void>;
  requestReconcile: (objection: string) => Promise<void>;
  setReconcilePrefill: (text: string) => void;
  dismissReconcile: () => void;
}

/**
 * State-machine for one agent's voice call (port of iOS `VoiceCallViewModel`).
 *
 * Lifecycle:
 *  1. `start()` mints a LiveKit JWT via `POST /api/voice/sessions`.
 *  2. The hook lazily wires a `VoiceClient` (LiveKit RN SDK) and emits status,
 *     transcript, handoff, and reconcile-request events back into state.
 *  3. `hangup()` tears down the transport and stops the audio session.
 *
 * `requestReconcile(objection)` calls `POST /api/voice/sessions/{id}/reconcile`
 * which re-invokes the Portfolio Manager with the user's objection as new
 * context (no full pipeline rerun) and returns the updated rationale.
 */
export function useVoiceSession(runId: string, agentId: string): VoiceSessionState & VoiceSessionActions {
  const [phase, setPhase] = useState<VoicePhase>('idle');
  const [status, setStatus] = useState<string>('Idle');
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [persona, setPersona] = useState<VoicePersonaPayload | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [transcript, setTranscript] = useState<VoiceTranscriptEntry[]>([]);
  const [handoffs, setHandoffs] = useState<VoiceHandoff[]>([]);
  const [reconcile, setReconcile] = useState<VoiceReconcileResponse | null>(null);
  const [reconcileBusy, setReconcileBusy] = useState(false);
  const [reconcilePrefill, setReconcilePrefill] = useState('');
  const [muted, setMuted] = useState(false);
  const [lastTurnLatencyMs, setLastTurnLatencyMs] = useState<number | null>(null);
  const [p95TurnLatencyMs, setP95TurnLatencyMs] = useState<number | null>(null);
  const latencyBufferRef = useRef<number[]>([]);

  const clientRef = useRef<VoiceClient | null>(null);
  const idCounterRef = useRef(0);

  const allocId = useCallback(() => {
    idCounterRef.current += 1;
    return `voice-${idCounterRef.current}`;
  }, []);

  const handleEvent = useCallback(
    (event: VoiceClientEvent) => {
      switch (event.type) {
        case 'status':
          setStatus(event.text);
          break;
        case 'connected':
          setPhase('live');
          setStatus('Connected');
          break;
        case 'disconnected':
          setPhase((prev) => (prev === 'failed' ? prev : 'ended'));
          setStatus(event.reason ?? 'Disconnected');
          break;
        case 'error':
          setErrorMessage(event.message);
          setPhase('failed');
          setStatus('Error');
          break;
        case 'transcript':
          setTranscript((prev) => mergeTranscript(prev, event, allocId));
          break;
        case 'handoff':
          setHandoffs((prev) => [
            ...prev,
            { id: allocId(), targetAgentId: event.targetAgentId, quote: event.quote },
          ]);
          break;
        case 'reconcileRequested':
          setReconcilePrefill(event.objection);
          break;
        case 'latency': {
          const buf = latencyBufferRef.current;
          buf.push(event.rttMs);
          if (buf.length > 32) buf.shift();
          setLastTurnLatencyMs(event.rttMs);
          setP95TurnLatencyMs(percentile(buf, 95));
          break;
        }
        default: {
          // Exhaustiveness guard — adding a new variant must force a fix here.
          const _never: never = event;
          return _never;
        }
      }
    },
    [allocId],
  );

  const start = useCallback(async () => {
    if (phase !== 'idle' && phase !== 'failed' && phase !== 'ended') return;
    setPhase('minting');
    setErrorMessage(null);
    setStatus('Preparing call…');
    let started: VoiceSessionStartResponse;
    try {
      started = await apiClient.startVoiceSession(runId, agentId);
    } catch (err) {
      setErrorMessage(describeError(err));
      setPhase('failed');
      setStatus('Error');
      return;
    }
    setSessionId(started.sessionId);
    setPersona(started.persona);
    setPhase('connecting');
    setStatus('Connecting…');

    const client = new VoiceClient({
      url: started.url,
      token: started.token,
      onEvent: handleEvent,
    });
    clientRef.current = client;
    await client.start();
  }, [phase, runId, agentId, handleEvent]);

  const hangup = useCallback(async () => {
    const client = clientRef.current;
    clientRef.current = null;
    if (client) {
      await client.hangup();
    }
    setMuted(false);
  }, []);

  const toggleMute = useCallback(async () => {
    const client = clientRef.current;
    if (!client) return;
    const next = await client.toggleMute();
    setMuted(next);
  }, []);

  const requestReconcile = useCallback(
    async (objection: string) => {
      if (!sessionId) {
        setErrorMessage('No active session to reconcile.');
        return;
      }
      setReconcileBusy(true);
      try {
        const result = await apiClient.reconcilePortfolioManager(sessionId, objection);
        setReconcile(result);
        setTranscript((prev) => [
          ...prev,
          {
            id: allocId(),
            role: 'reconcile',
            text: result.rationaleMarkdown,
            isPartial: false,
          },
        ]);
      } catch (err) {
        setErrorMessage(describeError(err));
      } finally {
        setReconcileBusy(false);
      }
    },
    [sessionId, allocId],
  );

  const dismissReconcile = useCallback(() => setReconcile(null), []);

  // Tear down on unmount so a back-swipe never leaks an open WebRTC session.
  useEffect(() => {
    return () => {
      void clientRef.current?.hangup();
      clientRef.current = null;
    };
  }, []);

  return useMemo(
    () => ({
      phase,
      status,
      errorMessage,
      persona,
      sessionId,
      transcript,
      handoffs,
      reconcile,
      reconcileBusy,
      reconcilePrefill,
      muted,
      lastTurnLatencyMs,
      p95TurnLatencyMs,
      start,
      hangup,
      toggleMute,
      requestReconcile,
      setReconcilePrefill,
      dismissReconcile,
    }),
    [
      phase,
      status,
      errorMessage,
      persona,
      sessionId,
      transcript,
      handoffs,
      reconcile,
      reconcileBusy,
      reconcilePrefill,
      muted,
      lastTurnLatencyMs,
      p95TurnLatencyMs,
      start,
      hangup,
      toggleMute,
      requestReconcile,
      dismissReconcile,
    ],
  );
}

function percentile(samples: number[], p: number): number {
  if (samples.length === 0) return 0;
  const sorted = samples.slice().sort((a, b) => a - b);
  const idx = Math.min(sorted.length - 1, Math.floor((p / 100) * sorted.length));
  return sorted[idx] ?? 0;
}

/**
 * Coalesce partial transcript chunks into the latest row for the same role.
 * Final chunks always seal the row so subsequent partials open a new one.
 */
function mergeTranscript(
  prev: VoiceTranscriptEntry[],
  event: Extract<VoiceClientEvent, { type: 'transcript' }>,
  allocId: () => string,
): VoiceTranscriptEntry[] {
  if (prev.length === 0) {
    return [
      {
        id: allocId(),
        role: event.role,
        text: event.text,
        isPartial: !event.isFinal,
      },
    ];
  }
  const last = prev[prev.length - 1];
  if (last.role === event.role && last.isPartial) {
    const next = prev.slice(0, -1);
    next.push({ ...last, text: event.text, isPartial: !event.isFinal });
    return next;
  }
  return [
    ...prev,
    {
      id: allocId(),
      role: event.role,
      text: event.text,
      isPartial: !event.isFinal,
    },
  ];
}
