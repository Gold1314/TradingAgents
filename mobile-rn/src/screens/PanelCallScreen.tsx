import React, { useEffect } from 'react';
import { View, Text, ScrollView, StyleSheet } from 'react-native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import type { RootStackParamList } from '../navigation/RootNavigator';
import { usePanelSession } from '../hooks/usePanelSession';
import type { VoicePersonaPayload, VoiceTranscriptEntry } from '../models/voice';
import type { VoicePhase } from '../hooks/useVoiceSession';
import { agentMetadata } from '../models/agentNode';
import { PulseDot, StatusDot, TouchTarget, Banner } from '../components/primitives';
import { colors, fontSize, radius, spacing, withAlpha } from '../theme/theme';

type Props = NativeStackScreenProps<RootStackParamList, 'PanelCall'>;

/**
 * Full-screen moderated *panel* call surface. Mirrors `VoiceCallScreen` but for
 * a roster of personas taking turns:
 *
 *  - a horizontal roster of panelists (avatar + name) highlighting the active
 *    speaker; tapping a panelist directs the next answer to them ("next"),
 *  - a transcript attributing each assistant line to its speaking persona,
 *  - mute + End call controls.
 */
export function PanelCallScreen({ route, navigation }: Props) {
  const { runId, agentIds } = route.params;
  const session = usePanelSession(runId, agentIds);

  useEffect(() => {
    void session.start();
    // start() is stable for the run/roster pair; re-run only on identity change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, JSON.stringify(agentIds)]);

  useEffect(() => {
    navigation.setOptions({ title: 'Panel call', headerBackTitle: 'Back' });
  }, [navigation]);

  const onHangup = async () => {
    await session.hangup();
    navigation.goBack();
  };

  return (
    <View style={styles.screen}>
      <ScrollView contentContainerStyle={styles.scroll}>
        <StatusPill phase={session.phase} status={session.status} muted={session.muted} />

        {session.errorMessage && <Banner text={session.errorMessage} tone="danger" icon />}

        <View style={styles.section}>
          <Text style={styles.sectionLabel}>Panel</Text>
          <Roster
            personas={session.personas}
            activeSpeaker={session.activeSpeaker}
            directedAgentId={session.directedAgentId}
            leadAgentId={session.leadAgentId}
            onDirect={(id) => session.direct(id)}
            enabled={session.phase === 'live'}
          />
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionLabel}>Transcript</Text>
          <TranscriptList entries={session.transcript} personas={session.personas} />
        </View>
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

function StatusPill({
  phase,
  status,
  muted,
}: {
  phase: VoicePhase;
  status: string;
  muted: boolean;
}) {
  return (
    <View style={styles.statusPill}>
      {phase === 'live' ? (
        <PulseDot color={muted ? colors.warning : colors.accent} />
      ) : (
        <StatusDot color={statusColor(phase)} />
      )}
      <Text style={styles.statusText}>{statusText(phase, status, muted)}</Text>
    </View>
  );
}

function Roster({
  personas,
  activeSpeaker,
  directedAgentId,
  leadAgentId,
  onDirect,
  enabled,
}: {
  personas: VoicePersonaPayload[];
  activeSpeaker: string | null;
  directedAgentId: string | null;
  leadAgentId: string | null;
  onDirect: (agentId: string) => void;
  enabled: boolean;
}) {
  if (personas.length === 0) {
    return <Text style={styles.muted}>Assembling the panel…</Text>;
  }
  return (
    <ScrollView
      horizontal
      showsHorizontalScrollIndicator={false}
      contentContainerStyle={styles.rosterRow}
    >
      {personas.map((p) => {
        const meta = agentMetadata(p.displayName);
        const isActive = activeSpeaker === p.agentId;
        const isNext = directedAgentId === p.agentId;
        const isLead = leadAgentId === p.agentId;
        return (
          <TouchTarget
            key={p.agentId}
            onPress={() => onDirect(p.agentId)}
            disabled={!enabled}
            feedback="scale"
            accessibilityLabel={`Direct the next answer to ${p.displayName}`}
            style={[
              styles.panelist,
              { borderColor: isActive ? meta.color : withAlpha(meta.color, 0.35) },
              isActive && { backgroundColor: withAlpha(meta.color, 0.2) },
            ]}
          >
            <View
              style={[
                styles.avatar,
                {
                  borderColor: withAlpha(meta.color, 0.6),
                  backgroundColor: withAlpha(meta.color, 0.18),
                },
                isActive && styles.avatarActive,
              ]}
            >
              <Text style={styles.avatarIcon}>{meta.icon}</Text>
            </View>
            <Text style={styles.panelistName} numberOfLines={1}>
              {p.displayName}
            </Text>
            {isActive ? (
              <Text style={[styles.panelistTag, { color: meta.color }]}>Speaking</Text>
            ) : isNext ? (
              <Text style={[styles.panelistTag, { color: colors.warning }]}>Next</Text>
            ) : isLead ? (
              <Text style={styles.panelistLead}>Lead</Text>
            ) : (
              <Text style={styles.panelistHint}>Tap to ask</Text>
            )}
          </TouchTarget>
        );
      })}
    </ScrollView>
  );
}

function TranscriptList({
  entries,
  personas,
}: {
  entries: VoiceTranscriptEntry[];
  personas: VoicePersonaPayload[];
}) {
  if (entries.length === 0) {
    return (
      <Text style={styles.muted}>
        Say something to open the floor. Each panelist's reply appears here,
        attributed to who spoke.
      </Text>
    );
  }
  return (
    <View style={{ gap: spacing.sm }}>
      {entries.map((entry) => (
        <TranscriptRow key={entry.id} entry={entry} personas={personas} />
      ))}
    </View>
  );
}

function TranscriptRow({
  entry,
  personas,
}: {
  entry: VoiceTranscriptEntry;
  personas: VoicePersonaPayload[];
}) {
  if (entry.role === 'user') {
    return (
      <View style={styles.transcriptRow}>
        <Text style={[styles.transcriptLabel, { color: colors.accent }]}>YOU</Text>
        <Text style={[styles.transcriptText, entry.isPartial && styles.transcriptPartial]}>
          {entry.text || '…'}
        </Text>
      </View>
    );
  }
  const persona = entry.agentId
    ? personas.find((p) => p.agentId === entry.agentId)
    : undefined;
  const displayName = persona?.displayName ?? entry.agentId ?? 'Panel';
  const meta = agentMetadata(displayName);
  return (
    <View style={styles.transcriptRow}>
      <Text style={[styles.transcriptLabel, { color: meta.color }]}>
        {`${meta.icon} ${displayName.toUpperCase()}`}
      </Text>
      <Text style={[styles.transcriptText, entry.isPartial && styles.transcriptPartial]}>
        {entry.text || '…'}
      </Text>
    </View>
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
        accessibilityLabel="End call"
      >
        <Text style={[styles.controlText, styles.hangupText]}>End call</Text>
      </TouchTarget>
    </View>
  );
}

function statusText(phase: VoicePhase, status: string, muted: boolean): string {
  if (muted && phase === 'live') return 'Live · muted';
  switch (phase) {
    case 'idle':
      return 'Ready';
    case 'minting':
      return 'Preparing panel…';
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

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.background },
  scroll: { padding: spacing.lg, gap: spacing.lg, paddingBottom: spacing.xl * 4 },
  statusPill: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    padding: spacing.md,
    borderRadius: radius.md,
    backgroundColor: withAlpha(colors.surface, 0.5),
  },
  statusText: { color: colors.textPrimary, fontSize: fontSize.sm, fontWeight: '500' },
  section: { gap: spacing.sm },
  sectionLabel: {
    color: colors.textMuted,
    fontSize: fontSize.xs,
    fontWeight: '700',
    textTransform: 'uppercase',
    letterSpacing: 0.4,
  },
  rosterRow: { flexDirection: 'row', gap: spacing.md, paddingVertical: spacing.xs },
  panelist: {
    width: 96,
    padding: spacing.md,
    borderRadius: radius.lg,
    borderWidth: 1,
    backgroundColor: withAlpha(colors.surface, 0.5),
    alignItems: 'center',
    gap: spacing.xs,
  },
  avatar: {
    width: 44,
    height: 44,
    borderRadius: 22,
    borderWidth: 1,
    alignItems: 'center',
    justifyContent: 'center',
  },
  avatarActive: { borderWidth: 2 },
  avatarIcon: { fontSize: 20 },
  panelistName: {
    color: colors.textPrimary,
    fontSize: fontSize.xs,
    fontWeight: '600',
    textAlign: 'center',
  },
  panelistTag: { fontSize: fontSize.xs, fontWeight: '700' },
  panelistLead: { color: colors.textMuted, fontSize: fontSize.xs, fontWeight: '600' },
  panelistHint: { color: colors.textMuted, fontSize: fontSize.xs },
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
