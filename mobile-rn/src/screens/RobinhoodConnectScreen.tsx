import React, { useEffect, useLayoutEffect } from 'react';
import { View, Text, ScrollView, ActivityIndicator, StyleSheet } from 'react-native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import type { RootStackParamList } from '../navigation/RootNavigator';
import { useBrokerConnect, type ConnectPhase } from '../hooks/useBrokerConnect';
import { Banner, Card, PulseDot, StatusDot, TouchTarget } from '../components/primitives';
import { formatUsd } from '../utils/format';
import { colors, fontSize, radius, spacing } from '../theme/theme';

type Props = NativeStackScreenProps<RootStackParamList, 'RobinhoodConnect'>;

/**
 * Robinhood connect screen (iOS plan §5.1.3), ported from `RobinhoodConnectView`.
 * Shows the current connection state, drives the server-mediated OAuth flow via
 * `expo-web-browser`, and reflects connecting / connected / failed. Emphasizes
 * that the phone never holds a brokerage token.
 */
export function RobinhoodConnectScreen({ navigation }: Props) {
  const connect = useBrokerConnect();
  const { phase, status, isConnected, isBusy } = connect;

  useEffect(() => {
    void connect.loadStatus();
    // Load once on mount; `loadStatus` identity is stable per render cycle.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useLayoutEffect(() => {
    navigation.setOptions({
      title: 'Robinhood',
      headerRight: () => (
        <TouchTarget
          onPress={() => {
            connect.cancel();
            navigation.goBack();
          }}
          accessibilityLabel="Close Robinhood connect"
        >
          <Text style={styles.headerButton}>Done</Text>
        </TouchTarget>
      ),
    });
  }, [navigation, connect]);

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      <StatusCard phase={phase} />

      {isConnected && (
        <Card style={styles.safetyCard}>
          <Text style={styles.safetyTitle}>Trade-safety</Text>
          <Row label="Mode" value={status?.tradeMode ?? '—'} />
          <Row label="Real orders" value={status?.canPlaceRealOrders ? 'Enabled' : 'Off'} />
          <Row label="Dry run" value={(status?.dryRun ?? true) ? 'On (simulated)' : 'Off (LIVE)'} />
          {status?.maxOrderNotional != null && (
            <Row label="Max order" value={formatUsd(status.maxOrderNotional)} />
          )}
        </Card>
      )}

      <TouchTarget
        onPress={() => void connect.connect()}
        disabled={isBusy}
        feedback="scale"
        accessibilityLabel={isConnected ? 'Reconnect Robinhood' : 'Connect Robinhood'}
        style={[styles.actionButton, { backgroundColor: isBusy ? colors.border : colors.accent }]}
      >
        {isBusy ? (
          <View style={styles.actionRow}>
            <ActivityIndicator color={colors.background} />
            <Text style={styles.actionLabel}>{busyLabel(phase)}</Text>
          </View>
        ) : (
          <Text style={styles.actionLabel}>
            {isConnected ? 'Reconnect' : 'Connect Robinhood'}
          </Text>
        )}
      </TouchTarget>

      {phase.kind === 'failed' && <Banner text={phase.message} tone="danger" icon />}

      <Text style={styles.footnote}>
        Your Robinhood login and tokens stay on the server. This device only opens the secure
        sign-in page and checks connection status — it never sees your brokerage credentials.
      </Text>
    </ScrollView>
  );
}

function StatusCard({ phase }: { phase: ConnectPhase }) {
  return (
    <Card style={styles.statusCard}>
      <StatusIndicator phase={phase} />
      <Text style={styles.statusTitle}>{statusTitle(phase)}</Text>
      <Text style={styles.statusSubtitle}>{statusSubtitle(phase)}</Text>
    </Card>
  );
}

/**
 * Pulses while the connection is mid-flight so the card reads as "live"; renders
 * a static dot for terminal states (connected / disconnected / failed / unknown).
 */
function StatusIndicator({ phase }: { phase: ConnectPhase }) {
  const color = statusColor(phase);
  switch (phase.kind) {
    case 'starting':
    case 'authorizing':
    case 'polling':
      return <PulseDot color={color} />;
    case 'unknown':
    case 'disconnected':
    case 'connected':
    case 'failed':
      return <StatusDot color={color} />;
    default: {
      const _exhaustive: never = phase;
      return _exhaustive;
    }
  }
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.row}>
      <Text style={styles.rowLabel}>{label}</Text>
      <Text style={styles.rowValue}>{value}</Text>
    </View>
  );
}

// MARK: - Derived copy (ports of the SwiftUI computed properties)

function statusColor(phase: ConnectPhase): string {
  switch (phase.kind) {
    case 'connected':
      return colors.accent;
    case 'failed':
      return colors.danger;
    case 'starting':
    case 'authorizing':
    case 'polling':
      return colors.warning;
    case 'unknown':
    case 'disconnected':
      return colors.textMuted;
    default: {
      const _exhaustive: never = phase;
      return _exhaustive;
    }
  }
}

function statusTitle(phase: ConnectPhase): string {
  switch (phase.kind) {
    case 'unknown':
      return 'Checking…';
    case 'disconnected':
      return 'Not connected';
    case 'starting':
      return 'Starting…';
    case 'authorizing':
      return 'Authorize in browser';
    case 'polling':
      return 'Finishing connection…';
    case 'connected':
      return 'Connected';
    case 'failed':
      return 'Connection failed';
    default: {
      const _exhaustive: never = phase;
      return _exhaustive;
    }
  }
}

function statusSubtitle(phase: ConnectPhase): string {
  switch (phase.kind) {
    case 'connected':
      return 'Robinhood is linked to your account on the server.';
    case 'authorizing':
      return 'Complete the Robinhood sign-in in the secure browser sheet.';
    case 'polling':
      return 'Confirming the connection with the server…';
    case 'unknown':
    case 'disconnected':
    case 'starting':
    case 'failed':
      return 'Link your Robinhood account to ground runs in your real positions and place orders.';
    default: {
      const _exhaustive: never = phase;
      return _exhaustive;
    }
  }
}

function busyLabel(phase: ConnectPhase): string {
  switch (phase.kind) {
    case 'starting':
      return 'Starting…';
    case 'authorizing':
      return 'Waiting…';
    case 'polling':
      return 'Connecting…';
    case 'unknown':
    case 'disconnected':
    case 'connected':
    case 'failed':
      return 'Working…';
    default: {
      const _exhaustive: never = phase;
      return _exhaustive;
    }
  }
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.background },
  content: { padding: spacing.lg, gap: spacing.lg, paddingBottom: spacing.xl * 2 },
  headerButton: { color: colors.accent, fontSize: fontSize.md },
  statusCard: { alignItems: 'center', gap: spacing.md },
  statusTitle: { color: colors.textPrimary, fontSize: fontSize.lg, fontWeight: '600' },
  statusSubtitle: { color: colors.textMuted, fontSize: fontSize.sm, textAlign: 'center' },
  safetyCard: { gap: spacing.sm },
  safetyTitle: {
    color: colors.textMuted,
    fontSize: fontSize.sm,
    fontWeight: '700',
    textTransform: 'uppercase',
    letterSpacing: 0.4,
  },
  row: { flexDirection: 'row', justifyContent: 'space-between' },
  rowLabel: { color: colors.textMuted, fontSize: fontSize.sm },
  rowValue: { color: colors.textPrimary, fontSize: fontSize.sm },
  actionButton: {
    borderRadius: radius.md,
    paddingVertical: 14,
    alignItems: 'center',
    justifyContent: 'center',
  },
  actionRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  actionLabel: { color: colors.background, fontSize: fontSize.lg, fontWeight: '700' },
  footnote: { color: colors.textMuted, fontSize: fontSize.sm, lineHeight: 18 },
});
