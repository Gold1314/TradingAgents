import React, { useMemo, useState } from 'react';
import { View, Text, Modal, ScrollView, StyleSheet } from 'react-native';
import { useShallow } from 'zustand/react/shallow';
import { useRunSession, selectOrderedAgents } from '../state/runSessionStore';
import { agentMetadata } from '../models/agentNode';
import { TouchTarget } from './primitives';
import { Icon } from './Icon';
import { colors, fontSize, radius, spacing, withAlpha } from '../theme/theme';

/**
 * "Start panel" affordance for a finished run. Renders a button that opens a
 * multi-select of the run's *finished* agents; picking two or more and
 * confirming launches a moderated panel call via `onStart(agentIds)`.
 *
 * Only shown when the voice layer is ready and at least two agents are `done`
 * (a panel needs a quorum). Mirrors the solo Talk affordance's gating.
 */
export function PanelLauncher({
  voiceReady,
  onStart,
}: {
  voiceReady: boolean;
  onStart: (agentIds: string[]) => void;
}) {
  const orderedAgents = useRunSession(useShallow(selectOrderedAgents));
  const finished = useMemo(
    () => orderedAgents.filter((a) => a.status === 'done').map((a) => a.node),
    [orderedAgents],
  );

  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);

  if (!voiceReady || finished.length < 2) return null;

  const toggle = (node: string) =>
    setSelected((prev) =>
      prev.includes(node) ? prev.filter((n) => n !== node) : [...prev, node],
    );

  const close = () => {
    setOpen(false);
    setSelected([]);
  };

  const confirm = () => {
    if (selected.length < 2) return;
    // Preserve panel order = pipeline order.
    const roster = finished.filter((n) => selected.includes(n));
    close();
    onStart(roster);
  };

  return (
    <>
      <TouchTarget
        onPress={() => setOpen(true)}
        feedback="scale"
        style={styles.launchButton}
        accessibilityLabel="Start a panel call"
      >
        <Text style={styles.launchText}>🎙  Start panel</Text>
      </TouchTarget>

      <Modal visible={open} transparent animationType="slide" onRequestClose={close}>
        <View style={styles.backdrop}>
          <View style={styles.sheet}>
            <View style={styles.sheetHeader}>
              <Text style={styles.sheetTitle}>Assemble a panel</Text>
              <TouchTarget onPress={close} hitSlop={12} accessibilityLabel="Cancel">
                <Icon name="chevron-down" size={18} color={colors.textMuted} />
              </TouchTarget>
            </View>
            <Text style={styles.sheetSubtitle}>
              Pick two or more agents. One speaks per turn; tap a panelist mid-call
              to direct the next answer.
            </Text>

            <ScrollView style={styles.list} contentContainerStyle={styles.listContent}>
              {finished.map((node) => {
                const meta = agentMetadata(node);
                const isSel = selected.includes(node);
                return (
                  <TouchTarget
                    key={node}
                    onPress={() => toggle(node)}
                    feedback="opacity"
                    accessibilityRole="checkbox"
                    accessibilityState={{ checked: isSel }}
                    accessibilityLabel={meta.name}
                    style={[
                      styles.row,
                      { borderColor: isSel ? meta.color : colors.border },
                      isSel && { backgroundColor: withAlpha(meta.color, 0.16) },
                    ]}
                  >
                    <View
                      style={[
                        styles.rowAvatar,
                        {
                          borderColor: withAlpha(meta.color, 0.6),
                          backgroundColor: withAlpha(meta.color, 0.18),
                        },
                      ]}
                    >
                      <Text style={styles.rowIcon}>{meta.icon}</Text>
                    </View>
                    <View style={styles.rowText}>
                      <Text style={styles.rowName}>{meta.name}</Text>
                      <Text style={styles.rowBlurb} numberOfLines={1}>
                        {meta.blurb}
                      </Text>
                    </View>
                    {isSel ? (
                      <Icon name="check" size={18} color={meta.color} />
                    ) : (
                      <View style={styles.rowUnchecked} />
                    )}
                  </TouchTarget>
                );
              })}
            </ScrollView>

            <TouchTarget
              onPress={confirm}
              disabled={selected.length < 2}
              style={[styles.confirm, selected.length < 2 && styles.confirmDisabled]}
              accessibilityLabel="Start panel call"
            >
              <Text style={styles.confirmText}>
                {selected.length < 2
                  ? 'Select at least 2'
                  : `Start panel · ${selected.length} agents`}
              </Text>
            </TouchTarget>
          </View>
        </View>
      </Modal>
    </>
  );
}

const styles = StyleSheet.create({
  launchButton: {
    paddingVertical: spacing.md,
    paddingHorizontal: spacing.lg,
    borderRadius: radius.md,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: withAlpha(colors.accent, 0.6),
    backgroundColor: withAlpha(colors.accent, 0.14),
  },
  launchText: { color: colors.accent, fontSize: fontSize.md, fontWeight: '700' },
  backdrop: { flex: 1, justifyContent: 'flex-end', backgroundColor: withAlpha('#000000', 0.5) },
  sheet: {
    backgroundColor: colors.background,
    borderTopLeftRadius: radius.lg,
    borderTopRightRadius: radius.lg,
    borderTopWidth: 1,
    borderColor: colors.border,
    padding: spacing.lg,
    gap: spacing.md,
    maxHeight: '80%',
  },
  sheetHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  sheetTitle: { color: colors.textPrimary, fontSize: fontSize.lg, fontWeight: '700' },
  sheetSubtitle: { color: colors.textMuted, fontSize: fontSize.sm },
  list: { flexGrow: 0 },
  listContent: { gap: spacing.sm, paddingVertical: spacing.xs },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
    padding: spacing.md,
    borderRadius: radius.md,
    borderWidth: 1,
    backgroundColor: withAlpha(colors.surface, 0.5),
  },
  rowAvatar: {
    width: 36,
    height: 36,
    borderRadius: 18,
    borderWidth: 1,
    alignItems: 'center',
    justifyContent: 'center',
  },
  rowIcon: { fontSize: 16 },
  rowText: { flex: 1 },
  rowName: { color: colors.textPrimary, fontSize: fontSize.md, fontWeight: '600' },
  rowBlurb: { color: colors.textMuted, fontSize: fontSize.sm },
  rowUnchecked: {
    width: 18,
    height: 18,
    borderRadius: 9,
    borderWidth: 1,
    borderColor: colors.border,
  },
  confirm: {
    backgroundColor: colors.accent,
    borderRadius: radius.md,
    padding: spacing.md,
    alignItems: 'center',
  },
  confirmDisabled: { backgroundColor: colors.surfaceMuted },
  confirmText: { color: colors.background, fontSize: fontSize.md, fontWeight: '700' },
});
