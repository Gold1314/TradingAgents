import React, { useMemo, useState } from 'react';
import {
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { agentMetadata } from '../models/agentNode';
import type { AgentCardState } from '../models/agentNode';
import { TouchTarget } from './primitives';
import { colors, fontSize, radius, spacing, withAlpha } from '../theme/theme';

/**
 * Multi-select sheet for the "Group call" flow.
 *
 * The user picks 2-3 panelists from the agents that have already finished
 * on this run (we don't let them call an agent whose report isn't on
 * screen — that's the same gate the single-agent Talk button enforces).
 * The confirm button stays disabled until at least 2 panelists are picked
 * because a one-element "panel" is identical to a regular single-agent
 * call, and we want that flow to live behind the regular Talk button.
 *
 * The parent typically opens this sheet from a "Group call" button next
 * to each agent card. The tapped agent should be passed as
 * ``preselected`` so the user starts with one chip already checked.
 */

const MIN_PANEL_SIZE = 2;

export interface PanelPickerProps {
  visible: boolean;
  /** Agents that have reached `done` on this run. Anything else is hidden. */
  candidates: AgentCardState[];
  /** Agent the user tapped Group Call from — pre-checked when the sheet opens. */
  preselected?: string | null;
  /** Server-enforced cap (default 3). The sheet refuses to add a 4th chip. */
  maxSize: number;
  onConfirm: (agentIds: string[]) => void;
  onClose: () => void;
}

export function PanelPicker({
  visible,
  candidates,
  preselected,
  maxSize,
  onConfirm,
  onClose,
}: PanelPickerProps) {
  const initialSelection = useMemo(
    () => (preselected ? [preselected] : []),
    [preselected],
  );
  const [selected, setSelected] = useState<string[]>(initialSelection);

  // Reset selection whenever the sheet is opened with a different
  // preselect. Otherwise stale state survives across opens.
  React.useEffect(() => {
    if (visible) setSelected(initialSelection);
  }, [visible, initialSelection]);

  const done = candidates.filter((c) => c.status === 'done');

  const toggle = (agentId: string) => {
    setSelected((prev) => {
      if (prev.includes(agentId)) {
        return prev.filter((a) => a !== agentId);
      }
      if (prev.length >= maxSize) {
        return prev; // soft-cap at the server-enforced max
      }
      return [...prev, agentId];
    });
  };

  const canConfirm = selected.length >= MIN_PANEL_SIZE;

  return (
    <Modal
      visible={visible}
      animationType="slide"
      presentationStyle="pageSheet"
      onRequestClose={onClose}
    >
      <View style={styles.screen}>
        <View style={styles.headerBar}>
          <Text style={styles.title}>Group call</Text>
          <Pressable
            onPress={onClose}
            hitSlop={12}
            accessibilityLabel="Close panel picker"
          >
            <Text style={styles.cancel}>Cancel</Text>
          </Pressable>
        </View>

        <Text style={styles.subtitle}>
          {`Pick ${MIN_PANEL_SIZE}-${maxSize} agents to join the call. They'll debate together; you can chime in any time.`}
        </Text>

        <View style={styles.preview}>
          <Text style={styles.previewLabel}>
            {selected.length === 0
              ? 'No one selected yet'
              : `Selected (${selected.length}/${maxSize})`}
          </Text>
          <View style={styles.previewRow}>
            {selected.map((id) => {
              const m = agentMetadata(id);
              return (
                <View
                  key={id}
                  style={[
                    styles.previewChip,
                    {
                      borderColor: withAlpha(m.color, 0.7),
                      backgroundColor: withAlpha(m.color, 0.18),
                    },
                  ]}
                >
                  <Text style={[styles.previewChipText, { color: m.color }]}>
                    {m.icon} {m.name}
                  </Text>
                </View>
              );
            })}
          </View>
        </View>

        <ScrollView contentContainerStyle={styles.list}>
          {done.length === 0 ? (
            <Text style={styles.muted}>
              No agents have finished yet. Wait for the run to complete, then
              come back to start a group call.
            </Text>
          ) : (
            done.map((card) => {
              const m = agentMetadata(card.node);
              const isSelected = selected.includes(card.node);
              const isAtCap = !isSelected && selected.length >= maxSize;
              return (
                <TouchTarget
                  key={card.node}
                  onPress={() => toggle(card.node)}
                  feedback="opacity"
                  disabled={isAtCap}
                  style={[
                    styles.row,
                    {
                      borderColor: isSelected
                        ? m.color
                        : withAlpha(colors.border, 0.6),
                      backgroundColor: isSelected
                        ? withAlpha(m.color, 0.12)
                        : withAlpha(colors.surface, 0.5),
                      opacity: isAtCap ? 0.4 : 1,
                    },
                  ]}
                  accessibilityRole="checkbox"
                  accessibilityState={{ checked: isSelected }}
                  accessibilityLabel={`${m.name}, ${isSelected ? 'selected' : 'not selected'}`}
                >
                  <View
                    style={[
                      styles.rowAvatar,
                      {
                        borderColor: withAlpha(m.color, 0.6),
                        backgroundColor: withAlpha(m.color, 0.18),
                      },
                    ]}
                  >
                    <Text style={styles.rowAvatarIcon}>{m.icon}</Text>
                  </View>
                  <View style={styles.rowText}>
                    <Text style={styles.rowName}>{m.name}</Text>
                    <Text style={styles.rowBlurb} numberOfLines={1}>
                      {m.blurb}
                    </Text>
                  </View>
                  <View
                    style={[
                      styles.checkBox,
                      {
                        borderColor: isSelected ? m.color : colors.border,
                        backgroundColor: isSelected ? m.color : 'transparent',
                      },
                    ]}
                  >
                    {isSelected && <Text style={styles.checkMark}>✓</Text>}
                  </View>
                </TouchTarget>
              );
            })
          )}
        </ScrollView>

        <View style={styles.footer}>
          <TouchTarget
            onPress={() => canConfirm && onConfirm(selected)}
            disabled={!canConfirm}
            style={[
              styles.confirmButton,
              !canConfirm && styles.confirmButtonDisabled,
            ]}
            accessibilityLabel={
              canConfirm
                ? `Start group call with ${selected.length} agents`
                : 'Pick at least two agents to enable group call'
            }
          >
            <Text style={styles.confirmText}>
              {canConfirm
                ? `Start group call (${selected.length})`
                : `Pick at least ${MIN_PANEL_SIZE}`}
            </Text>
          </TouchTarget>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: colors.background,
    padding: spacing.lg,
    gap: spacing.md,
  },
  headerBar: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  title: { color: colors.textPrimary, fontSize: fontSize.xl, fontWeight: '700' },
  subtitle: { color: colors.textMuted, fontSize: fontSize.sm },
  cancel: { color: colors.accent, fontSize: fontSize.md, fontWeight: '600' },
  preview: {
    padding: spacing.md,
    borderRadius: radius.md,
    backgroundColor: withAlpha(colors.surface, 0.4),
    gap: spacing.sm,
  },
  previewLabel: {
    color: colors.textMuted,
    fontSize: fontSize.xs,
    fontWeight: '700',
    textTransform: 'uppercase',
    letterSpacing: 0.4,
  },
  previewRow: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  previewChip: {
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs,
    borderRadius: radius.xl,
    borderWidth: 1,
  },
  previewChipText: { fontSize: fontSize.sm, fontWeight: '600' },
  list: { gap: spacing.sm, paddingBottom: spacing.lg },
  muted: { color: colors.textMuted, fontSize: fontSize.sm },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
    borderRadius: radius.md,
    borderWidth: 1,
    padding: spacing.md,
  },
  rowAvatar: {
    width: 40,
    height: 40,
    borderRadius: 20,
    borderWidth: 1,
    alignItems: 'center',
    justifyContent: 'center',
  },
  rowAvatarIcon: { fontSize: 18 },
  rowText: { flex: 1 },
  rowName: { color: colors.textPrimary, fontSize: fontSize.md, fontWeight: '600' },
  rowBlurb: { color: colors.textMuted, fontSize: fontSize.sm },
  checkBox: {
    width: 24,
    height: 24,
    borderRadius: radius.sm,
    borderWidth: 2,
    alignItems: 'center',
    justifyContent: 'center',
  },
  checkMark: { color: colors.background, fontWeight: '700' },
  footer: { paddingVertical: spacing.sm },
  confirmButton: {
    backgroundColor: colors.accent,
    borderRadius: radius.md,
    paddingVertical: spacing.md,
    alignItems: 'center',
  },
  confirmButtonDisabled: { backgroundColor: withAlpha(colors.accent, 0.3) },
  confirmText: { color: colors.background, fontSize: fontSize.md, fontWeight: '700' },
});
