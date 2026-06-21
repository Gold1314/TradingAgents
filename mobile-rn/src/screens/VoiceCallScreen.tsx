import React, { useEffect, useMemo, useState } from 'react';
import {
  View,
  Text,
  ScrollView,
  StyleSheet,
  ActivityIndicator,
  TextInput,
  Alert,
} from 'react-native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import type { RootStackParamList } from '../navigation/RootNavigator';
import {
  useVoiceSession,
  type VoicePhase,
  type VoiceSessionTarget,
} from '../hooks/useVoiceSession';
import type {
  VoicePersonaPayload,
  VoiceTranscriptEntry,
  VoiceHandoff,
} from '../models/voice';
import { agentMetadata } from '../models/agentNode';
import { Card, PulseDot, StatusDot, TouchTarget, Banner } from '../components/primitives';
import { MarkdownBody } from '../components/MarkdownBody';
import { colors, fontSize, radius, spacing, withAlpha } from '../theme/theme';

type Props = NativeStackScreenProps<RootStackParamList, 'VoiceCall'>;

/**
 * Full-screen voice call surface ported from the iOS `VoiceCallView`.
 *
 * The screen owns three sections:
 *
 * - **Persona header** with the agent's name, voice tag, and a live status dot
 *   (pulsing while the call is live).
 * - **Live transcript** of ASR partials/finals from the LiveKit Agents worker,
 *   merged in order with user turns, plus reconcile blocks rendered as markdown.
 * - **Composer + controls**: mute/hangup, handoff chips, and an "ask the PM to
 *   reconcile" composer that re-invokes the Portfolio Manager with the user's
 *   objection as new context (no full pipeline rerun).
 */
export function VoiceCallScreen({ route, navigation }: Props) {
  const { runId } = route.params;
  // Discriminate the route shape into the hook's union. We prefer
  // ``agentIds`` when set so the panel path wins on any ambiguous payload
  // (this shouldn't ever fire in practice but guards against typos).
  const target: VoiceSessionTarget = useMemo(() => {
    if ('agentIds' in route.params && route.params.agentIds) {
      return { mode: 'panel', agentIds: route.params.agentIds };
    }
    return { mode: 'single', agentId: route.params.agentId };
  }, [route.params]);
  const targetKey = target.mode === 'panel' ? target.agentIds.join(',') : target.agentId;
  const session = useVoiceSession(runId, target);
  const isPanel = target.mode === 'panel';
  // Whose name + theme is in the header. Single-agent uses the persona;
  // panel uses the lead persona for the accent color but renders a roster
  // of avatars instead of a single big name.
  const headerName = session.persona?.displayName ?? (
    target.mode === 'single' ? target.agentId : 'Group call'
  );
  const meta = agentMetadata(session.persona?.displayName ?? headerName);

  useEffect(() => {
    void session.start();
    // start() is a stable closure for the run/target pair — re-running on
    // identity change is the desired behaviour.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, targetKey]);

  useEffect(() => {
    navigation.setOptions({
      title: isPanel ? 'Group call' : headerName,
      headerBackTitle: 'Back',
    });
  }, [navigation, isPanel, headerName]);

  const onHangup = async () => {
    await session.hangup();
    navigation.goBack();
  };

  const onHandoffTap = (h: VoiceHandoff) => {
    Alert.alert(
      `Talk to ${h.targetAgentId}?`,
      h.quote || 'The current agent suggested a handoff.',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Switch',
          onPress: async () => {
            await session.hangup();
            navigation.replace('VoiceCall', { runId, agentId: h.targetAgentId });
          },
        },
      ],
    );
  };

  // Per the plan §8: the "ask the PM to reconcile" composer only renders when
  // the Portfolio Manager is in this session. In single-agent mode that's
  // when the lone agent IS the PM; in panel mode any panel that includes
  // the PM unlocks reconcile.
  const showReconcile = isPanel
    ? target.agentIds.includes('Portfolio Manager')
    : target.agentId === 'Portfolio Manager';

  return (
    <View style={styles.screen}>
      <ScrollView contentContainerStyle={styles.scroll}>
        {isPanel && session.panel ? (
          <PanelHeader
            personas={session.panel.personas}
            activeSpeakerAgentId={session.activeSpeakerAgentId}
            status={session.status}
            phase={session.phase}
            muted={session.muted}
            latencyMs={session.lastTurnLatencyMs}
            p95Ms={session.p95TurnLatencyMs}
          />
        ) : (
          <PersonaHeader
            name={session.persona?.displayName ?? meta.name}
            voiceName={session.persona?.voiceName ?? null}
            accent={meta.color}
            status={session.status}
            phase={session.phase}
            muted={session.muted}
            latencyMs={session.lastTurnLatencyMs}
            p95Ms={session.p95TurnLatencyMs}
          />
        )}

        {session.errorMessage && <Banner text={session.errorMessage} tone="danger" icon />}

        {session.handoffs.length > 0 && (
          <View style={styles.section}>
            <Text style={styles.sectionLabel}>Suggested handoffs</Text>
            <View style={styles.handoffRow}>
              {session.handoffs.map((h) => (
                <TouchTarget
                  key={h.id}
                  onPress={() => onHandoffTap(h)}
                  feedback="scale"
                  style={styles.handoffChip}
                  accessibilityLabel={`Hand off to ${h.targetAgentId}`}
                >
                  <Text style={styles.handoffChipText}>{`→ ${h.targetAgentId}`}</Text>
                </TouchTarget>
              ))}
            </View>
          </View>
        )}

        <View style={styles.section}>
          <Text style={styles.sectionLabel}>Transcript</Text>
          <TranscriptList entries={session.transcript} />
        </View>

        {showReconcile && (
          <ReconcileCard
            prefill={session.reconcilePrefill}
            busy={session.reconcileBusy}
            onChange={session.setReconcilePrefill}
            onSubmit={(text) => session.requestReconcile(text)}
          />
        )}

        {session.reconcile && (
          <Card>
            <Text style={styles.reconcileBadge}>
              {session.reconcile.flipped
                ? `Updated decision: ${session.reconcile.updatedDecision ?? '—'}`
                : `Decision held: ${session.reconcile.originalDecision ?? '—'}`}
            </Text>
            <MarkdownBody source={session.reconcile.rationaleMarkdown} />
          </Card>
        )}
      </ScrollView>

      <CallControls
        phase={session.phase}
        muted={session.muted}
        onMute={session.toggleMute}
        onHangup={onHangup}
      />
    </View>
  );
}

function PanelHeader({
  personas,
  activeSpeakerAgentId,
  status,
  phase,
  muted,
  latencyMs,
  p95Ms,
}: {
  personas: VoicePersonaPayload[];
  activeSpeakerAgentId: string | null;
  status: string;
  phase: VoicePhase;
  muted: boolean;
  latencyMs: number | null;
  p95Ms: number | null;
}) {
  return (
    <View style={[styles.header, { borderColor: withAlpha(colors.accent, 0.5) }]}>
      <Text style={styles.panelHeaderTitle}>Group call</Text>
      <View style={styles.panelAvatarRow}>
        {personas.map((p) => {
          const m = agentMetadata(p.displayName ?? p.agentId);
          const isActive = activeSpeakerAgentId === p.agentId;
          return (
            <View key={p.agentId} style={styles.panelAvatarColumn}>
              <View
                style={[
                  styles.panelAvatar,
                  {
                    borderColor: withAlpha(m.color, isActive ? 1 : 0.4),
                    backgroundColor: withAlpha(m.color, isActive ? 0.32 : 0.14),
                  },
                ]}
              >
                <Text style={styles.panelAvatarIcon}>{m.icon}</Text>
                {isActive && (
                  <View style={[styles.panelAvatarPulse, { borderColor: m.color }]} />
                )}
              </View>
              <Text
                style={[
                  styles.panelAvatarLabel,
                  { color: isActive ? m.color : colors.textMuted },
                ]}
                numberOfLines={1}
              >
                {m.name}
              </Text>
              {p.voiceName && (
                <Text style={styles.panelAvatarVoice} numberOfLines={1}>
                  {p.voiceName}
                </Text>
              )}
            </View>
          );
        })}
      </View>
      <View style={styles.headerStatusRow}>
        {phase === 'live' ? (
          <PulseDot color={muted ? colors.warning : colors.accent} />
        ) : (
          <StatusDot color={statusColor(phase)} />
        )}
        <Text style={styles.headerStatusText}>{statusText(phase, status, muted)}</Text>
        {latencyMs != null && (
          <Text style={styles.headerLatency}>
            {`Δ ${latencyMs}ms${p95Ms != null ? ` · p95 ${p95Ms}` : ''}`}
          </Text>
        )}
      </View>
    </View>
  );
}

function PersonaHeader({
  name,
  voiceName,
  accent,
  status,
  phase,
  muted,
  latencyMs,
  p95Ms,
}: {
  name: string;
  voiceName: string | null;
  accent: string;
  status: string;
  phase: VoicePhase;
  muted: boolean;
  latencyMs: number | null;
  p95Ms: number | null;
}) {
  return (
    <View style={[styles.header, { borderColor: withAlpha(accent, 0.5) }]}>
      <Text style={[styles.headerName, { color: accent }]}>{name}</Text>
      {voiceName && <Text style={styles.headerSub}>Voice: {voiceName}</Text>}
      <View style={styles.headerStatusRow}>
        {phase === 'live' ? (
          <PulseDot color={muted ? colors.warning : colors.accent} />
        ) : (
          <StatusDot color={statusColor(phase)} />
        )}
        <Text style={styles.headerStatusText}>{statusText(phase, status, muted)}</Text>
        {latencyMs != null && (
          <Text style={styles.headerLatency}>
            {`Δ ${latencyMs}ms${p95Ms != null ? ` · p95 ${p95Ms}` : ''}`}
          </Text>
        )}
      </View>
    </View>
  );
}

function statusText(phase: VoicePhase, status: string, muted: boolean): string {
  if (muted && phase === 'live') return 'Live · muted';
  switch (phase) {
    case 'idle':
      return 'Ready';
    case 'minting':
      return 'Preparing call…';
    case 'connecting':
      return 'Connecting…';
    case 'live':
      return status || 'Live';
    case 'ended':
      return 'Call ended';
    case 'failed':
      return status || 'Error';
    default: {
      const _never: never = phase;
      return _never;
    }
  }
}

function statusColor(phase: VoicePhase): string {
  switch (phase) {
    case 'failed':
      return colors.danger;
    case 'live':
      return colors.accent;
    case 'idle':
    case 'ended':
      return colors.textMuted;
    case 'minting':
    case 'connecting':
      return colors.warning;
    default: {
      const _never: never = phase;
      return _never;
    }
  }
}

function TranscriptList({ entries }: { entries: VoiceTranscriptEntry[] }) {
  if (entries.length === 0) {
    return (
      <Text style={styles.muted}>
        Say something to get started. The agent's reply will appear here.
      </Text>
    );
  }
  return (
    <View style={{ gap: spacing.sm }}>
      {entries.map((entry) => (
        <TranscriptRow key={entry.id} entry={entry} />
      ))}
    </View>
  );
}

function TranscriptRow({ entry }: { entry: VoiceTranscriptEntry }) {
  let label: string;
  let labelColor: string;
  switch (entry.role) {
    case 'user':
      label = 'YOU';
      labelColor = colors.accent;
      break;
    case 'reconcile':
      label = 'PM RECONCILE';
      labelColor = colors.warning;
      break;
    case 'assistant':
      if (entry.speakerAgentId) {
        const speakerMeta = agentMetadata(entry.speakerAgentId);
        label = speakerMeta.name.toUpperCase();
        labelColor = speakerMeta.color;
      } else {
        label = 'AGENT';
        labelColor = colors.textMuted;
      }
      break;
    default: {
      const _exhaustive: never = entry.role;
      return _exhaustive;
    }
  }
  if (entry.role === 'reconcile') {
    return (
      <View style={styles.transcriptRow}>
        <Text style={[styles.transcriptLabel, { color: labelColor }]}>{label}</Text>
        <MarkdownBody source={entry.text} />
      </View>
    );
  }
  return (
    <View style={styles.transcriptRow}>
      <Text style={[styles.transcriptLabel, { color: labelColor }]}>{label}</Text>
      <Text style={[styles.transcriptText, entry.isPartial && styles.transcriptPartial]}>
        {entry.text || '…'}
      </Text>
    </View>
  );
}

function ReconcileCard({
  prefill,
  busy,
  onChange,
  onSubmit,
}: {
  prefill: string;
  busy: boolean;
  onChange: (text: string) => void;
  onSubmit: (text: string) => void;
}) {
  const [draft, setDraft] = useState(prefill);
  useEffect(() => setDraft(prefill), [prefill]);
  return (
    <Card>
      <Text style={styles.reconcileTitle}>Ask the PM to reconcile</Text>
      <Text style={styles.muted}>
        Voice an objection and the Portfolio Manager will re-evaluate this run's decision
        without rerunning the entire pipeline.
      </Text>
      <TextInput
        style={styles.reconcileInput}
        value={draft}
        onChangeText={(t) => {
          setDraft(t);
          onChange(t);
        }}
        placeholder="e.g. but the news risk wasn't priced in…"
        placeholderTextColor={colors.textMuted}
        multiline
        editable={!busy}
      />
      <TouchTarget
        onPress={() => onSubmit(draft)}
        disabled={busy || draft.trim().length === 0}
        style={styles.reconcileSubmit}
        accessibilityLabel="Submit reconcile request"
      >
        {busy ? (
          <ActivityIndicator color={colors.background} />
        ) : (
          <Text style={styles.reconcileSubmitText}>Request reconcile</Text>
        )}
      </TouchTarget>
    </Card>
  );
}

function CallControls({
  phase,
  muted,
  onMute,
  onHangup,
}: {
  phase: VoicePhase;
  muted: boolean;
  onMute: () => void;
  onHangup: () => void;
}) {
  const canControl = phase === 'live';
  return (
    <View style={styles.controls}>
      <TouchTarget
        onPress={onMute}
        disabled={!canControl}
        style={[styles.controlButton, muted && styles.controlButtonOn]}
        accessibilityLabel={muted ? 'Unmute microphone' : 'Mute microphone'}
      >
        <Text style={styles.controlText}>{muted ? 'Unmute' : 'Mute'}</Text>
      </TouchTarget>
      <TouchTarget
        onPress={onHangup}
        style={[styles.controlButton, styles.hangupButton]}
        accessibilityLabel="Hang up"
      >
        <Text style={[styles.controlText, styles.hangupText]}>Hang up</Text>
      </TouchTarget>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.background },
  scroll: { padding: spacing.lg, gap: spacing.lg, paddingBottom: spacing.xl * 4 },
  header: {
    backgroundColor: withAlpha(colors.surface, 0.5),
    borderWidth: 1,
    borderRadius: radius.lg,
    padding: spacing.lg,
    gap: spacing.xs,
  },
  headerName: { fontSize: fontSize.xl, fontWeight: '700' },
  headerSub: { color: colors.textMuted, fontSize: fontSize.sm },
  panelHeaderTitle: {
    color: colors.textPrimary,
    fontSize: fontSize.lg,
    fontWeight: '700',
    marginBottom: spacing.sm,
  },
  panelAvatarRow: {
    flexDirection: 'row',
    justifyContent: 'space-around',
    alignItems: 'flex-start',
    gap: spacing.md,
  },
  panelAvatarColumn: {
    alignItems: 'center',
    gap: spacing.xs,
    flex: 1,
  },
  panelAvatar: {
    width: 56,
    height: 56,
    borderRadius: 28,
    borderWidth: 2,
    alignItems: 'center',
    justifyContent: 'center',
    overflow: 'hidden',
  },
  panelAvatarIcon: { fontSize: 26 },
  panelAvatarPulse: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    borderRadius: 28,
    borderWidth: 2,
    opacity: 0.7,
  },
  panelAvatarLabel: {
    fontSize: fontSize.xs,
    fontWeight: '700',
    textTransform: 'uppercase',
    letterSpacing: 0.4,
  },
  panelAvatarVoice: {
    color: colors.textMuted,
    fontSize: fontSize.xs,
  },
  headerStatusRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    marginTop: spacing.xs,
  },
  headerStatusText: { color: colors.textPrimary, fontSize: fontSize.sm, fontWeight: '500' },
  headerLatency: {
    color: colors.textMuted,
    fontSize: fontSize.xs,
    marginLeft: 'auto',
    fontVariant: ['tabular-nums'],
  },
  section: { gap: spacing.sm },
  sectionLabel: {
    color: colors.textMuted,
    fontSize: fontSize.xs,
    fontWeight: '700',
    textTransform: 'uppercase',
    letterSpacing: 0.4,
  },
  handoffRow: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  handoffChip: {
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    borderRadius: radius.xl,
    backgroundColor: withAlpha(colors.accent, 0.18),
    borderWidth: 1,
    borderColor: withAlpha(colors.accent, 0.5),
  },
  handoffChipText: { color: colors.accent, fontSize: fontSize.sm, fontWeight: '600' },
  transcriptRow: {
    padding: spacing.md,
    borderRadius: radius.md,
    backgroundColor: withAlpha(colors.surface, 0.6),
    gap: spacing.xs,
  },
  transcriptLabel: {
    fontSize: fontSize.xs,
    fontWeight: '700',
    textTransform: 'uppercase',
    letterSpacing: 0.4,
  },
  transcriptText: { color: colors.textPrimary, fontSize: fontSize.md, lineHeight: 22 },
  transcriptPartial: { color: colors.textMuted, fontStyle: 'italic' },
  muted: { color: colors.textMuted, fontSize: fontSize.sm },
  reconcileTitle: { color: colors.textPrimary, fontSize: fontSize.lg, fontWeight: '700' },
  reconcileInput: {
    minHeight: 80,
    borderRadius: radius.md,
    borderColor: colors.border,
    borderWidth: 1,
    padding: spacing.md,
    color: colors.textPrimary,
    backgroundColor: colors.surfaceMuted,
    textAlignVertical: 'top',
  },
  reconcileSubmit: {
    backgroundColor: colors.accent,
    borderRadius: radius.md,
    padding: spacing.md,
    alignItems: 'center',
  },
  reconcileSubmitText: { color: colors.background, fontWeight: '700', fontSize: fontSize.md },
  reconcileBadge: {
    color: colors.warning,
    fontSize: fontSize.sm,
    fontWeight: '700',
    marginBottom: spacing.sm,
  },
  controls: {
    flexDirection: 'row',
    gap: spacing.md,
    padding: spacing.lg,
    borderTopWidth: 1,
    borderTopColor: colors.border,
    backgroundColor: colors.background,
  },
  controlButton: {
    flex: 1,
    paddingVertical: spacing.md,
    borderRadius: radius.md,
    alignItems: 'center',
    backgroundColor: withAlpha(colors.surface, 0.6),
    borderWidth: 1,
    borderColor: colors.border,
  },
  controlButtonOn: { backgroundColor: withAlpha(colors.warning, 0.25), borderColor: colors.warning },
  controlText: { color: colors.textPrimary, fontSize: fontSize.md, fontWeight: '600' },
  hangupButton: { backgroundColor: colors.danger, borderColor: colors.danger },
  hangupText: { color: colors.background },
});
