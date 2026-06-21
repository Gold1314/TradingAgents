import React, { useEffect, useState } from 'react';
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
import { useVoiceSession, type VoicePhase } from '../hooks/useVoiceSession';
import type { VoiceTranscriptEntry, VoiceHandoff } from '../models/voice';
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
  const { runId, agentId } = route.params;
  const session = useVoiceSession(runId, agentId);
  const meta = agentMetadata(session.persona?.displayName ?? agentId);

  useEffect(() => {
    void session.start();
    // start() is a stable closure for the run/agent pair — re-running on
    // identity change is the desired behaviour.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, agentId]);

  useEffect(() => {
    navigation.setOptions({
      title: session.persona?.displayName ?? meta.name,
      headerBackTitle: 'Back',
    });
  }, [navigation, session.persona, meta.name]);

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

  return (
    <View style={styles.screen}>
      <ScrollView contentContainerStyle={styles.scroll}>
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

        {agentId === 'Portfolio Manager' && (
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
  const label = entry.role === 'user' ? 'YOU' : entry.role === 'reconcile' ? 'PM RECONCILE' : 'AGENT';
  const labelColor =
    entry.role === 'user'
      ? colors.accent
      : entry.role === 'reconcile'
        ? colors.warning
        : colors.textMuted;
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
