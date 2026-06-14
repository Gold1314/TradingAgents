import React from 'react';
import { View, Text, ScrollView, StyleSheet } from 'react-native';
import { useRunSession } from '../state/runSessionStore';
import { STAGE_ORDER, STAGE_META, agentMetadata, type Stage } from '../models/agentNode';
import { colors, fontSize, radius, spacing, withAlpha } from '../theme/theme';

/**
 * The 5-stage stepper with per-stage progress, ported from `buildStepper` /
 * `refreshStepper` in `index.html` and the Swift `StageStepperView`.
 */
export function StageStepper() {
  const plannedNodes = useRunSession((s) => s.plannedNodes);
  const completedNodes = useRunSession((s) => s.completedNodes);
  const activeNode = useRunSession((s) => s.activeNode);

  const stages = STAGE_ORDER.filter((stage) =>
    plannedNodes.some((n) => agentMetadata(n).stage === stage),
  );
  if (stages.length === 0) return null;

  return (
    <ScrollView horizontal showsHorizontalScrollIndicator={false}>
      <View style={styles.row}>
        {stages.map((stage) => (
          <StageCard
            key={stage}
            stage={stage}
            plannedNodes={plannedNodes}
            completedNodes={completedNodes}
            activeNode={activeNode}
          />
        ))}
      </View>
    </ScrollView>
  );
}

function StageCard({
  stage,
  plannedNodes,
  completedNodes,
  activeNode,
}: {
  stage: Stage;
  plannedNodes: string[];
  completedNodes: string[];
  activeNode: string | null;
}) {
  const inStage = plannedNodes.filter((n) => agentMetadata(n).stage === stage);
  const completed = inStage.filter((n) => completedNodes.includes(n)).length;
  const total = inStage.length;
  const isActive = activeNode ? agentMetadata(activeNode).stage === stage : false;
  const isComplete = total > 0 && completed === total;
  const fraction = total === 0 ? 0 : completed / total;
  const color = STAGE_META[stage].color;

  const stateLabel = isComplete ? 'complete' : isActive ? 'in progress' : 'pending';

  return (
    <View
      style={[
        styles.card,
        { borderColor: isActive ? color : colors.border, borderWidth: isActive ? 1.5 : 1 },
      ]}
      accessible
      accessibilityRole="progressbar"
      accessibilityLabel={`${stage} stage, ${completed} of ${total} ${stateLabel}`}
      accessibilityValue={{ min: 0, max: total, now: completed }}
    >
      <Text
        style={[styles.label, { color: isActive || isComplete ? color : colors.textMuted }]}
        numberOfLines={1}
      >
        {STAGE_META[stage].icon} {stage}
      </Text>
      <View style={styles.track}>
        <View style={[styles.fill, { width: `${fraction * 100}%`, backgroundColor: color }]} />
      </View>
      <Text style={styles.count}>
        {completed}/{total}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', gap: spacing.sm, paddingVertical: spacing.xs },
  card: {
    width: 132,
    padding: spacing.md,
    borderRadius: radius.md,
    backgroundColor: withAlpha(colors.surface, 0.5),
    gap: spacing.sm,
  },
  label: { fontSize: fontSize.sm, fontWeight: '600' },
  track: {
    height: 6,
    borderRadius: 3,
    backgroundColor: colors.surfaceMuted,
    overflow: 'hidden',
  },
  fill: { height: 6, borderRadius: 3 },
  count: { fontSize: fontSize.xs, color: colors.textMuted, fontVariant: ['tabular-nums'] },
});
