import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { useRunSession, selectOrderedAgents } from '../state/runSessionStore';
import { CHART_HOST_NODE } from '../models/agentNode';
import { AgentCard } from './AgentCard';
import { colors, fontSize, radius, spacing } from '../theme/theme';

/**
 * The live agent feed: an optional instrument-identity card, then one expandable
 * markdown card per agent in planned pipeline order. The price chart is hosted
 * inside the Market Analyst card (web parity). Ports `AgentFeedView`.
 */
export function AgentFeed() {
  const orderedAgents = useRunSession(selectOrderedAgents);
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
