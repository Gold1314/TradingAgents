import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { apiClient } from '../services/apiClient';
import { VoiceClient, type VoiceClientEvent } from '../services/voiceClient';
import type { PanelSession, VoicePersonaPayload, VoiceTranscriptEntry } from '../models/voice';
import { describeError } from '../services/apiError';
import type { VoicePhase } from './useVoiceSession';

export interface PanelSessionState {
  phase: VoicePhase;
  status: string;
  errorMessage: string | null;
  sessionId: string | null;
  /** In-call roster in panel order (from the start response). */
  personas: VoicePersonaPayload[];
  leadAgentId: string | null;
  /** `agent_id` of the persona currently speaking, or `null`. */
  activeSpeaker: string | null;
  /** `agent_id` the user tapped to answer next, until they start speaking. */
  directedAgentId: string | null;
  /** Live transcript; assistant rows carry the speaking persona's `agentId`. */
  transcript: VoiceTranscriptEntry[];
  muted: boolean;
  lastTurnLatencyMs: number | null;
}

export interface PanelSessionActions {
  start: () => Promise<void>;
  hangup: () => Promise<void>;
  toggleMute: () => Promise<void>;
  /** Direct the next answer to a specific panelist (publishes `panel.direct`). */
  direct: (agentId: string) => Promise<void>;
}

/**
 * State-machine for one moderated *panel* voice call. Mirrors
 * `useVoiceSession`'s lifecycle (mint → connect → live → ended/failed) but
 * over `POST /api/voice/panels`, reusing the same `VoiceClient` transport.
 *
 * Panel additions over the solo hook:
 *  - `personas` — the in-call roster, from the start response.
 *  - `activeSpeaker` — driven by `panel.speaker` data frames (highlight).
 *  - transcript attribution — assistant finals carry `agentId`.
 *  - `direct(agentId)` — publishes `panel.direct` so the moderator routes the
 *    next answer to the tapped panelist.
 */
export function usePanelSession(
  runId: string,
  agentIds: string[],
): PanelSessionState & PanelSessionActions {
  const [phase, setPhase] = useState<VoicePhase>('idle');
  const [status, setStatus] = useState<string>('Idle');
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [personas, setPersonas] = useState<VoicePersonaPayload[]>([]);
  const [leadAgentId, setLeadAgentId] = useState<string | null>(null);
  const [activeSpeaker, setActiveSpeaker] = useState<string | null>(null);
  const [directedAgentId, setDirectedAgentId] = useState<string | null>(null);
  const [transcript, setTranscript] = useState<VoiceTranscriptEntry[]>([]);
  const [muted, setMuted] = useState(false);
  const [lastTurnLatencyMs, setLastTurnLatencyMs] = useState<number | null>(null);

  const clientRef = useRef<VoiceClient | null>(null);
  const idCounterRef = useRef(0);
  // Latest speaker so partials (which may omit agent_id) inherit attribution.
  const activeSpeakerRef = useRef<string | null>(null);

  const allocId = useCallback(() => {
    idCounterRef.current += 1;
    return `panel-${idCounterRef.current}`;
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
        case 'transcript': {
          const attributed =
            event.role === 'assistant'
              ? event.agentId ?? activeSpeakerRef.current
              : null;
          setTranscript((prev) => mergeTranscript(prev, event, attributed, allocId));
          break;
        }
        case 'panelSpeaker':
          activeSpeakerRef.current = event.agentId;
          setActiveSpeaker(event.agentId);
          // The tapped panelist has the floor — clear the "next" marker.
          setDirectedAgentId((prev) => (prev === event.agentId ? null : prev));
          break;
        case 'latency':
          setLastTurnLatencyMs(event.rttMs);
          break;
        case 'handoff':
        case 'reconcileRequested':
          // Not surfaced in panel calls (handoff is ignored per the contract).
          break;
        default: {
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
    setStatus('Preparing panel…');
    let started: PanelSession;
    try {
      started = await apiClient.startPanelSession(runId, agentIds);
    } catch (err) {
      setErrorMessage(describeError(err));
      setPhase('failed');
      setStatus('Error');
      return;
    }
    setSessionId(started.sessionId);
    setPersonas(started.personas);
    setLeadAgentId(started.leadAgentId);
    setPhase('connecting');
    setStatus('Connecting…');

    const client = new VoiceClient({
      url: started.url,
      token: started.token,
      onEvent: handleEvent,
    });
    clientRef.current = client;
    await client.start();
  }, [phase, runId, agentIds, handleEvent]);

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

  const direct = useCallback(async (agentId: string) => {
    const client = clientRef.current;
    if (!client) return;
    setDirectedAgentId(agentId);
    await client.publishPanelDirect(agentId);
  }, []);

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
      sessionId,
      personas,
      leadAgentId,
      activeSpeaker,
      directedAgentId,
      transcript,
      muted,
      lastTurnLatencyMs,
      start,
      hangup,
      toggleMute,
      direct,
    }),
    [
      phase,
      status,
      errorMessage,
      sessionId,
      personas,
      leadAgentId,
      activeSpeaker,
      directedAgentId,
      transcript,
      muted,
      lastTurnLatencyMs,
      start,
      hangup,
      toggleMute,
      direct,
    ],
  );
}

/**
 * Coalesce partial transcript chunks into the latest row for the same role,
 * carrying the panel `agentId` attribution. Final chunks seal the row.
 */
function mergeTranscript(
  prev: VoiceTranscriptEntry[],
  event: Extract<VoiceClientEvent, { type: 'transcript' }>,
  agentId: string | null,
  allocId: () => string,
): VoiceTranscriptEntry[] {
  const last = prev.length > 0 ? prev[prev.length - 1] : null;
  if (last && last.role === event.role && last.isPartial) {
    const next = prev.slice(0, -1);
    next.push({
      ...last,
      text: event.text,
      isPartial: !event.isFinal,
      agentId: agentId ?? last.agentId ?? null,
    });
    return next;
  }
  return [
    ...prev,
    {
      id: allocId(),
      role: event.role,
      text: event.text,
      isPartial: !event.isFinal,
      agentId,
    },
  ];
}
