import React, { useState } from 'react';
import { View, Text, ActivityIndicator, StyleSheet } from 'react-native';
import type { AgentCardState } from '../models/agentNode';
import { agentMetadata, CHART_HOST_NODE } from '../models/agentNode';
import type { Usage, AgentStatus } from '../models/events';
import type { ChartPayload } from '../models/chart';
import { MarkdownBody } from './MarkdownBody';
import { PriceChart } from './PriceChart';
import { Icon } from './Icon';
import { TouchTarget } from './primitives';
import { formatTokens, formatCost } from '../utils/format';
import { colors, fontSize, radius, spacing, withAlpha } from '../theme/theme';

/**
 * A single agent card: themed avatar, name + blurb, status, expandable markdown
 * body, and (for the Market Analyst) the price chart. Token/cost usage moves
 * into the expanded body so the collapsed header reads as a single status row,
 * not a fight between agent name and tabular numbers. Ports `feedCard` /
 * `updateCard` and the Swift `AgentCardView`.
 */
export function AgentCard({
  card,
  usage,
  chart,
  chartError,
}: {
  card: AgentCardState;
  usage?: Usage;
  chart?: ChartPayload | null;
  chartError?: string | null;
}) {
  const [expanded, setExpanded] = useState(false);
  const meta = agentMetadata(card.node);
  const isChartHost = card.node === CHART_HOST_NODE;
  const isDone = card.status === 'done';

  return (
    <View
      style={[
        styles.card,
        { borderColor: isDone ? withAlpha(meta.color, 0.5) : colors.border },
      ]}
    >
      <TouchTarget
        onPress={() => setExpanded((v) => !v)}
        feedback="opacity"
        accessibilityRole="button"
        accessibilityState={{ expanded }}
        accessibilityLabel={`${meta.name}, ${card.status}`}
        style={styles.header}
      >
        <View
          style={[
            styles.avatar,
            {
              borderColor: withAlpha(meta.color, 0.6),
              backgroundColor: withAlpha(meta.color, 0.18),
            },
          ]}
        >
          <Text style={styles.avatarIcon}>{meta.icon}</Text>
        </View>
        <View style={styles.titleBox}>
          <Text style={styles.name} numberOfLines={1}>
            {meta.name}
          </Text>
          <Text style={styles.blurb} numberOfLines={1}>
            {meta.blurb}
          </Text>
        </View>
        <StatusBadge status={card.status} accent={meta.color} />
        <Icon
          name={expanded ? 'chevron-up' : 'chevron-down'}
          size={14}
          color={colors.textMuted}
        />
      </TouchTarget>

      {expanded && (
        <View style={styles.body}>
          {isChartHost && <ChartSection chart={chart} chartError={chartError} />}
          {card.content ? (
            <MarkdownBody source={card.content} />
          ) : card.status === 'running' ? (
            <View style={styles.runningRow}>
              <ActivityIndicator size="small" color={colors.textMuted} />
              <Text style={styles.muted}>Working…</Text>
            </View>
          ) : null}
          {usage && isDone && <UsageBar usage={usage} />}
        </View>
      )}
    </View>
  );
}

function StatusBadge({ status, accent }: { status: AgentStatus; accent: string }) {
  switch (status) {
    case 'running':
      return (
        <View style={styles.runningBadge}>
          <ActivityIndicator size="small" color={colors.warning} />
          <Text style={[styles.badgeText, { color: colors.warning }]}>running</Text>
        </View>
      );
    case 'done':
      return <Icon name="check" size={16} color={accent} />;
    default: {
      const _exhaustive: never = status;
      return _exhaustive;
    }
  }
}

function UsageBar({ usage }: { usage: Usage }) {
  return (
    <View style={styles.usageBar}>
      <UsageStat label="Tokens" value={formatTokens(usage.totalTokens)} />
      <UsageStat label="Input" value={formatTokens(usage.inputTokens)} />
      <UsageStat label="Output" value={formatTokens(usage.outputTokens)} />
      <UsageStat label="Cost" value={formatCost(usage.cost)} />
    </View>
  );
}

function UsageStat({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.usageStat}>
      <Text style={styles.usageValue} numberOfLines={1} adjustsFontSizeToFit minimumFontScale={0.7}>
        {value}
      </Text>
      <Text style={styles.usageLabel}>{label}</Text>
    </View>
  );
}

function ChartSection({
  chart,
  chartError,
}: {
  chart?: ChartPayload | null;
  chartError?: string | null;
}) {
  if (chart) return <PriceChart payload={chart} />;
  if (chartError) return <Text style={styles.muted}>Chart unavailable: {chartError}</Text>;
  return (
    <View style={styles.runningRow}>
      <ActivityIndicator size="small" color={colors.textMuted} />
      <Text style={styles.muted}>Loading chart…</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: withAlpha(colors.surface, 0.5),
    borderRadius: radius.lg,
    borderWidth: 1,
    padding: spacing.lg,
  },
  header: { flexDirection: 'row', alignItems: 'center', gap: spacing.md },
  avatar: {
    width: 40,
    height: 40,
    borderRadius: 20,
    borderWidth: 1,
    alignItems: 'center',
    justifyContent: 'center',
  },
  avatarIcon: { fontSize: 18 },
  titleBox: { flex: 1 },
  name: { color: colors.textPrimary, fontSize: fontSize.md, fontWeight: '600' },
  blurb: { color: colors.textMuted, fontSize: fontSize.sm },
  body: { marginTop: spacing.md, gap: spacing.md },
  runningRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  runningBadge: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs },
  badgeText: { fontSize: fontSize.xs, fontWeight: '600' },
  muted: { color: colors.textMuted, fontSize: fontSize.sm },
  usageBar: {
    flexDirection: 'row',
    backgroundColor: colors.surfaceMuted,
    borderRadius: radius.sm,
    padding: spacing.sm,
    gap: spacing.xs,
  },
  usageStat: {
    flex: 1,
    alignItems: 'center',
    gap: 2,
  },
  usageValue: {
    color: colors.textPrimary,
    fontSize: fontSize.sm,
    fontWeight: '600',
    fontVariant: ['tabular-nums'],
  },
  usageLabel: {
    color: colors.textMuted,
    fontSize: fontSize.xs,
    textTransform: 'uppercase',
    letterSpacing: 0.4,
  },
});
