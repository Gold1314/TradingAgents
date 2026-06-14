import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { useRunSession } from '../state/runSessionStore';
import type { Usage } from '../models/events';
import { agentMetadata } from '../models/agentNode';
import { formatTokens, formatCost } from '../utils/format';
import { colors, fontSize, radius, spacing, withAlpha } from '../theme/theme';

/**
 * Run economics: totals tiles + a per-agent token/cost breakdown. Ports
 * `renderEconomics` from `index.html` (driven by `usage` / `usage_summary`).
 *
 * Layout: tiles wrap into a 2×2 grid on narrow screens (the 4-across layout
 * inherited from the web overflowed monetary values like "$1,234.56" on phones
 * < 400px wide). Tile values truncate or auto-shrink rather than overflow.
 */
export function Economics() {
  const usageByNode = useRunSession((s) => s.usageByNode);
  const totals = useRunSession((s) => s.usageTotals);
  const plannedNodes = useRunSession((s) => s.plannedNodes);

  const entries = orderedUsage(usageByNode, plannedNodes);
  if (entries.length === 0 && !totals) return null;

  const all = Object.values(usageByNode);
  const maxTokens = Math.max(1, ...all.map((u) => u.totalTokens));
  const totalTokens = totals?.totalTokens ?? all.reduce((s, u) => s + u.totalTokens, 0);
  const inputTokens = totals?.inputTokens ?? all.reduce((s, u) => s + u.inputTokens, 0);
  const outputTokens = totals?.outputTokens ?? all.reduce((s, u) => s + u.outputTokens, 0);
  const costs = all.map((u) => u.cost).filter((c): c is number => c != null);
  const cost = totals?.cost ?? (costs.length ? costs.reduce((s, c) => s + c, 0) : null);

  return (
    <View style={styles.card}>
      <Text style={styles.heading}>⚡ Run economics</Text>
      <View style={styles.tiles}>
        <Tile title="Tokens" value={formatTokens(totalTokens)} />
        <Tile title="Input" value={formatTokens(inputTokens)} />
        <Tile title="Output" value={formatTokens(outputTokens)} />
        <Tile title="Est. cost" value={formatCost(cost)} />
      </View>
      <View style={{ gap: spacing.sm }}>
        {entries.map((usage) => (
          <AgentRow key={usage.node} usage={usage} maxTokens={maxTokens} />
        ))}
      </View>
    </View>
  );
}

function orderedUsage(byNode: Record<string, Usage>, plannedNodes: string[]): Usage[] {
  const seen = new Set<string>();
  const result: Usage[] = [];
  for (const node of plannedNodes) {
    const usage = byNode[node];
    if (usage) {
      result.push(usage);
      seen.add(node);
    }
  }
  for (const [node, usage] of Object.entries(byNode)) {
    if (!seen.has(node)) result.push(usage);
  }
  return result;
}

function Tile({ title, value }: { title: string; value: string }) {
  return (
    <View
      style={styles.tile}
      accessible
      accessibilityLabel={`${title}: ${value}`}
    >
      <Text
        style={styles.tileValue}
        numberOfLines={1}
        adjustsFontSizeToFit
        minimumFontScale={0.7}
      >
        {value}
      </Text>
      <Text style={styles.tileTitle}>{title}</Text>
    </View>
  );
}

function AgentRow({ usage, maxTokens }: { usage: Usage; maxTokens: number }) {
  const meta = agentMetadata(usage.node);
  const fraction = usage.totalTokens / maxTokens;
  return (
    <View style={{ gap: spacing.xs }}>
      <View style={styles.rowHead}>
        <Text style={styles.rowName} numberOfLines={1}>
          {meta.icon} {usage.node}
        </Text>
        <Text style={styles.rowMeta} numberOfLines={1}>
          {formatTokens(usage.totalTokens)} · {formatCost(usage.cost)}
        </Text>
      </View>
      <View style={styles.barTrack}>
        <View
          style={[
            styles.barFill,
            {
              width: `${Math.max(2, fraction * 100)}%`,
              backgroundColor: withAlpha(meta.color, 0.7),
            },
          ]}
        />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: withAlpha(colors.surface, 0.5),
    borderRadius: radius.lg,
    padding: spacing.lg,
    gap: spacing.md,
  },
  heading: {
    color: colors.textMuted,
    fontSize: fontSize.sm,
    fontWeight: '700',
    textTransform: 'uppercase',
    letterSpacing: 0.4,
  },
  tiles: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.sm,
  },
  tile: {
    // 2×2 grid: each tile takes ~half the row, expanding to fill leftover space.
    flexBasis: '47%',
    flexGrow: 1,
    alignItems: 'center',
    paddingVertical: spacing.md,
    paddingHorizontal: spacing.sm,
    backgroundColor: colors.surfaceMuted,
    borderRadius: radius.sm,
    gap: 2,
  },
  tileValue: {
    color: colors.textPrimary,
    fontSize: fontSize.lg,
    fontWeight: '700',
    fontVariant: ['tabular-nums'],
  },
  tileTitle: {
    color: colors.textMuted,
    fontSize: fontSize.xs,
    textTransform: 'uppercase',
    letterSpacing: 0.4,
  },
  rowHead: { flexDirection: 'row', justifyContent: 'space-between', gap: spacing.sm },
  rowName: { color: colors.textPrimary, fontSize: fontSize.sm, flex: 1 },
  rowMeta: {
    color: colors.textMuted,
    fontSize: fontSize.xs,
    fontVariant: ['tabular-nums'],
  },
  barTrack: { height: 4, borderRadius: 2, backgroundColor: colors.surfaceMuted, overflow: 'hidden' },
  barFill: { height: 4, borderRadius: 2 },
});
