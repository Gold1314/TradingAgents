import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { useShallow } from 'zustand/react/shallow';
import { useRunSession, selectOrderedAgents } from '../state/runSessionStore';
import { CHART_HOST_NODE } from '../models/agentNode';
import { AgentCard } from './AgentCard';
import { colors, fontSize, radius, spacing } from '../theme/theme';

/**
 * The live agent feed: an optional instrument-identity card, then one expandable
 * markdown card per agent in planned pipeline order. The price chart is hosted
 * inside the Market Analyst card (web parity). Ports `AgentFeedView`.
 *
 * `voiceReady` + `onTalkRequested` are optional; when both are set, every
 * `done` card surfaces a "Talk to {agent}" affordance that the parent
 * screen turns into navigation to `VoiceCallScreen`.
 */
export function AgentFeed({
  voiceReady = false,
  onTalkRequested,
  panelReady = false,
  onGroupCallRequested,
}: {
  voiceReady?: boolean;
  onTalkRequested?: (agent: string) => void;
  /**
   * Whether the multi-agent panel/group-call feature is enabled. Gated by
   * `panel_enabled` on `GET /api/voice/config`. Independent of `voiceReady`
   * — single-agent voice can be on while panel is still rolling out.
   */
  panelReady?: boolean;
  /**
   * Tapped from the per-card "Group call" affordance. The screen owner
   * receives the tapped agent so the picker can open with that one
   * pre-checked.
   */
  onGroupCallRequested?: (agent: string) => void;
} = {}) {
  // `useShallow` is required because `selectOrderedAgents` returns a fresh
  // array per call (`.map().filter()`). Without it, Zustand v5's
  // `useSyncExternalStore` integration detects an unstable snapshot during
  // active SSE streaming and bails into an infinite re-render loop.
  const orderedAgents = useRunSession(useShallow(selectOrderedAgents));
  const usageByNode = useRunSession((s) => s.usageByNode);
  const identity = useRunSession((s) => s.identity);
  const chart = useRunSession((s) => s.chart);
  const chartError = useRunSession((s) => s.chartError);

  return (
    <View style={{ gap: spacing.md }}>
      {identity ? (
        <View style={styles.identityCard}>
          <Text style={styles.identityLabel}>🏷 Instrument identity</Text>
          <Text style={styles.identityText} selectable>
            {identity}
          </Text>
        </View>
      ) : null}

      {orderedAgents.map((card) => (
        <AgentCard
          key={card.node}
          card={card}
          usage={usageByNode[card.node]}
          chart={card.node === CHART_HOST_NODE ? chart : undefined}
          chartError={card.node === CHART_HOST_NODE ? chartError : undefined}
          canTalk={voiceReady}
          onTalkRequested={onTalkRequested}
          canGroupCall={panelReady}
          onGroupCallRequested={onGroupCallRequested}
        />
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  identityCard: {
    backgroundColor: colors.surfaceMuted,
    borderRadius: radius.md,
    padding: spacing.md,
    gap: spacing.xs,
  },
  identityLabel: {
    color: colors.textMuted,
    fontSize: fontSize.sm,
    fontWeight: '700',
    textTransform: 'uppercase',
    letterSpacing: 0.4,
  },
  identityText: { color: colors.textPrimary, fontSize: fontSize.sm },
});
