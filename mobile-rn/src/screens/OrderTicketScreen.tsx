import React, { useLayoutEffect } from 'react';
import {
  View,
  Text,
  ScrollView,
  TextInput,
  ActivityIndicator,
  Alert,
  StyleSheet,
} from 'react-native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import type { RootStackParamList } from '../navigation/RootNavigator';
import { useOrderTicket, type Side, type SubmissionFailure } from '../hooks/useOrderTicket';
import { biometryLabel } from '../services/biometrics';
import { Banner, Card, TouchTarget } from '../components/primitives';
import { formatUsd } from '../utils/format';
import { lightFeedback, selectionFeedback, warningFeedback } from '../utils/haptics';
import type { TradeEvent } from '../models/events';
import { colors, fontSize, radius, spacing } from '../theme/theme';

type Props = NativeStackScreenProps<RootStackParamList, 'OrderTicket'>;

/**
 * LIVE order ticket + confirmation (iOS plan §5.3, decision 4), ported from
 * `OrderTicketView`. The user reviews / edits the agent's proposed order and taps
 * "Review order", which opens a deliberate confirmation alert — the REQUIRED gate.
 * Only its confirm action calls `confirmAndSubmit`. For a real-money order, when
 * an enrolled Face ID / Touch ID exists it also runs a biometric evaluation before
 * the network call; when biometrics are unavailable the confirm alone is enough.
 */
export function OrderTicketScreen({ route, navigation }: Props) {
  const { runId, trade } = route.params;
  const ticket = useOrderTicket(runId, trade);
  const {
    phase,
    proposed,
    side,
    setSide,
    orderType,
    notionalText,
    setNotionalText,
    quantityText,
    setQuantityText,
    placesRealMoney,
    isDryRun,
    biometryKind,
    isSubmitting,
    isPlaced,
  } = ticket;

  // Biometrics are an OPTIONAL second factor: we only surface them when an
  // enrolled Face ID / Touch ID exists. Their absence never blocks a LIVE order.
  const usesBiometric =
    placesRealMoney && (biometryKind === 'faceID' || biometryKind === 'touchID');

  useLayoutEffect(() => {
    navigation.setOptions({
      title: 'Order ticket',
      headerRight: () => (
        <TouchTarget
          onPress={() => navigation.goBack()}
          accessibilityLabel={isPlaced ? 'Done' : 'Cancel order ticket'}
        >
          <Text style={styles.headerButton}>{isPlaced ? 'Done' : 'Cancel'}</Text>
        </TouchTarget>
      ),
    });
  }, [navigation, isPlaced]);

  // Buy/Sell are equivalent options in terms of UI selection but NOT in terms
  // of consequence — the warning pulse on the buy→sell transition primes the
  // user that they are about to size a destructive action. Re-tapping the
  // already-active side is a no-op so we don't buzz it (avoids feeling cheap).
  const handleSideChange = (next: Side) => {
    if (next === side) return;
    switch (next) {
      case 'sell':
        warningFeedback();
        break;
      case 'buy':
        selectionFeedback();
        break;
      default: {
        const _exhaustive: never = next;
        void _exhaustive;
      }
    }
    setSide(next);
  };

  // The SINGLE deliberate confirm action. The biometric gate (LIVE) runs inside
  // `confirmAndSubmit`, strictly after this explicit confirmation. Sell orders
  // get a sell-specific title and destructive button — they reduce a position
  // and warrant the extra cognitive friction; buy orders are additive.
  const promptConfirm = () => {
    // Require an explicit positive size BEFORE opening the confirm dialog. Sending
    // an empty size would let the server silently fall back to the agent's stashed
    // size while the alert reads "$0.00" — so block with an inline error instead.
    const amount = ticket.parsedAmount;
    if (amount == null) {
      warningFeedback();
      ticket.reportInvalidSize();
      return;
    }
    // Haptic fires on the FIRST tap that opens the confirm Alert, not on the
    // alert's confirm button — that's the moment the user actually decides to
    // act. Sell mirrors the destructive styling with a warning pulse.
    switch (side) {
      case 'sell':
        warningFeedback();
        break;
      case 'buy':
        lightFeedback();
        break;
      default: {
        const _exhaustive: never = side;
        void _exhaustive;
      }
    }
    // Display EXACTLY what will be sent (the validated positive amount).
    const sizeText = side === 'buy' ? formatUsd(amount) : `${amount} shares`;
    const header = `${side.toUpperCase()} ${proposed.ticker} · ${sizeText} · ${orderType}`;
    const liveMessage = usesBiometric
      ? `${header}\n\nThis is a LIVE order using real money. You'll confirm with ${biometryLabel(
          biometryKind,
        )} before it is sent.`
      : `${header}\n\nThis is a LIVE order using real money. Confirm to send it.`;
    const message = placesRealMoney
      ? liveMessage
      : `${header}\n\nThis is a simulated (dry-run) order — no real funds will move.`;
    const confirmLabel = placesRealMoney
      ? usesBiometric
        ? `Place LIVE order · ${biometryLabel(biometryKind)}`
        : 'Place LIVE order'
      : 'Simulate order';
    const title = confirmAlertTitle(side, placesRealMoney);
    // `destructive` for sell mirrors the red CTA on iOS so the action color
    // matches the segment color; on Android it falls back to default styling.
    const confirmStyle: 'destructive' | 'default' =
      placesRealMoney || side === 'sell' ? 'destructive' : 'default';

    Alert.alert(title, message, [
      { text: 'Cancel', style: 'cancel' },
      {
        text: confirmLabel,
        style: confirmStyle,
        onPress: () => void ticket.confirmAndSubmit(),
      },
    ]);
  };

  const reviewDisabled = isSubmitting || isPlaced;
  // Sell is destructive — use `colors.down` so the CTA matches the active Sell
  // segment, reinforcing the directional semantics on a real-money screen.
  const reviewColor = reviewDisabled
    ? colors.border
    : placesRealMoney
      ? colors.danger
      : side === 'sell'
        ? colors.down
        : colors.accent;

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      <ModeBanner live={placesRealMoney} isDryRun={isDryRun} />

      <Card style={styles.section}>
        <Text style={styles.sectionTitle}>Proposed by the agents</Text>
        <Row label="Ticker" value={proposed.ticker || '—'} />
        <Row label="Rating" value={proposed.rating || '—'} />
        {proposed.reason !== '' && <Text style={styles.reason}>{proposed.reason}</Text>}
      </Card>

      <Card style={styles.section}>
        <Text style={styles.sectionTitle}>Order</Text>
        <Segmented value={side} onChange={handleSideChange} />
        {side === 'buy' ? (
          <Field label="Notional (USD)">
            <TextInput
              value={notionalText}
              onChangeText={setNotionalText}
              placeholder="0.00"
              placeholderTextColor={colors.textMuted}
              keyboardType="decimal-pad"
              style={styles.input}
              accessibilityLabel="Notional in US dollars"
            />
          </Field>
        ) : (
          <Field label="Quantity (shares)">
            <TextInput
              value={quantityText}
              onChangeText={setQuantityText}
              placeholder="0"
              placeholderTextColor={colors.textMuted}
              keyboardType="decimal-pad"
              style={styles.input}
              accessibilityLabel="Quantity in shares"
            />
          </Field>
        )}
        <Row label="Type" value={orderType} />
      </Card>

      <ResultSection phase={phase} biometryKind={biometryKind} navigation={navigation} />

      <TouchTarget
        onPress={promptConfirm}
        disabled={reviewDisabled}
        feedback="scale"
        accessibilityLabel={
          placesRealMoney
            ? 'Review live order'
            : side === 'sell'
              ? 'Review sell order'
              : 'Review order'
        }
        style={[styles.reviewButton, { backgroundColor: reviewColor }]}
      >
        <Text style={styles.reviewLabel}>
          {placesRealMoney ? 'Review LIVE order' : 'Review order'}
        </Text>
      </TouchTarget>
    </ScrollView>
  );
}

/**
 * Title for the confirm Alert. Sell orders always read as "Confirm sell order"
 * (with a LIVE prefix when real money is on the line) so the alert clearly
 * announces the destructive action before the user taps the colored confirm.
 */
function confirmAlertTitle(side: Side, placesRealMoney: boolean): string {
  switch (side) {
    case 'sell':
      return placesRealMoney ? 'Confirm LIVE sell order?' : 'Confirm sell order?';
    case 'buy':
      return placesRealMoney ? 'Place LIVE order?' : 'Place simulated order?';
    default: {
      const _exhaustive: never = side;
      return _exhaustive;
    }
  }
}

function ModeBanner({ live, isDryRun }: { live: boolean; isDryRun: boolean }) {
  const title = live ? 'LIVE — real money' : isDryRun ? 'Simulated (dry run)' : 'Simulated';
  const subtitle = live
    ? 'Placing this order will move real funds in your Robinhood account.'
    : 'This placement is simulated — no real order is sent.';
  return <Banner text={`${title}\n${subtitle}`} tone={live ? 'danger' : 'warning'} icon />;
}

function ResultSection({
  phase,
  biometryKind,
  navigation,
}: {
  phase: ReturnType<typeof useOrderTicket>['phase'];
  biometryKind: ReturnType<typeof useOrderTicket>['biometryKind'];
  navigation: Props['navigation'];
}) {
  switch (phase.kind) {
    case 'ready':
      return null;
    case 'authenticating':
      return (
        <Card style={styles.resultRow}>
          <ActivityIndicator color={colors.textMuted} />
          <Text style={styles.resultMuted}>{`Waiting for ${biometryLabel(biometryKind)}…`}</Text>
        </Card>
      );
    case 'submitting':
      return (
        <Card style={styles.resultRow}>
          <ActivityIndicator color={colors.textMuted} />
          <Text style={styles.resultMuted}>Submitting…</Text>
        </Card>
      );
    case 'placed':
      return (
        <Card style={styles.section}>
          <Text style={styles.placedText}>{placedSummary(phase.trade)}</Text>
        </Card>
      );
    case 'failed':
      return <FailureSection failure={phase.failure} navigation={navigation} />;
    default: {
      const _exhaustive: never = phase;
      return _exhaustive;
    }
  }
}

function FailureSection({
  failure,
  navigation,
}: {
  failure: SubmissionFailure;
  navigation: Props['navigation'];
}) {
  switch (failure.kind) {
    case 'brokerNotConnected':
      return (
        <Card style={styles.section}>
          <Text style={[styles.failText, { color: colors.warning }]}>{failure.message}</Text>
          <TouchTarget
            onPress={() => navigation.navigate('RobinhoodConnect')}
            feedback="scale"
            accessibilityLabel="Connect Robinhood"
            style={[styles.reviewButton, { backgroundColor: colors.accent }]}
          >
            <Text style={styles.reviewLabel}>Connect Robinhood</Text>
          </TouchTarget>
        </Card>
      );
    case 'runUnavailable':
      return (
        <Card style={styles.section}>
          <Text style={[styles.failText, { color: colors.textMuted }]}>{failure.message}</Text>
        </Card>
      );
    case 'rateLimited':
    case 'validation':
    case 'uncertain':
      return (
        <Card style={styles.section}>
          <Text style={[styles.failText, { color: colors.warning }]}>{failure.message}</Text>
        </Card>
      );
    case 'generic':
      return (
        <Card style={styles.section}>
          <Text style={[styles.failText, { color: colors.danger }]}>{failure.message}</Text>
        </Card>
      );
    default: {
      const _exhaustive: never = failure;
      return _exhaustive;
    }
  }
}

function placedSummary(trade: TradeEvent): string {
  switch (trade.status) {
    case 'placed':
      return `Order placed${trade.orderId ? ` (#${trade.orderId})` : ''}.`;
    case 'dry_run':
      return 'Simulated order recorded (dry run).';
    case 'skipped':
      return trade.message ?? 'Order skipped.';
    case 'error':
      return trade.message ?? 'Order failed.';
    default:
      return trade.message ?? `Order status: ${trade.status}.`;
  }
}

function Segmented({ value, onChange }: { value: Side; onChange: (side: Side) => void }) {
  return (
    <View style={styles.segmented} accessibilityRole="tablist">
      {(['buy', 'sell'] as const).map((option) => {
        const selected = value === option;
        const activeTint = activeColor(option);
        return (
          <TouchTarget
            key={option}
            onPress={() => onChange(option)}
            feedback="opacity"
            accessibilityRole="button"
            accessibilityState={{ selected }}
            accessibilityLabel={option === 'buy' ? 'Buy' : 'Sell'}
            style={[
              styles.segment,
              selected && {
                backgroundColor: activeTint,
                borderColor: activeTint,
              },
            ]}
          >
            <Text style={[styles.segmentLabel, selected && styles.segmentLabelActive]}>
              {option === 'buy' ? 'Buy' : 'Sell'}
            </Text>
          </TouchTarget>
        );
      })}
    </View>
  );
}

/** Directional tint for the segmented control — `up` for buy, `down` for sell. */
function activeColor(side: Side): string {
  switch (side) {
    case 'buy':
      return colors.up;
    case 'sell':
      return colors.down;
    default: {
      const _exhaustive: never = side;
      return _exhaustive;
    }
  }
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <View style={styles.field}>
      <Text style={styles.fieldLabel}>{label}</Text>
      {children}
    </View>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.row}>
      <Text style={styles.rowLabel}>{label}</Text>
      <Text style={styles.rowValue}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.background },
  content: { padding: spacing.lg, gap: spacing.lg, paddingBottom: spacing.xl * 2 },
  headerButton: { color: colors.accent, fontSize: fontSize.md },
  section: { gap: spacing.sm },
  sectionTitle: {
    color: colors.textMuted,
    fontSize: fontSize.sm,
    fontWeight: '700',
    textTransform: 'uppercase',
    letterSpacing: 0.4,
  },
  reason: { color: colors.textMuted, fontSize: fontSize.sm, lineHeight: 18 },
  row: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  rowLabel: { color: colors.textMuted, fontSize: fontSize.sm },
  rowValue: { color: colors.textPrimary, fontSize: fontSize.sm },
  field: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  fieldLabel: { color: colors.textPrimary, fontSize: fontSize.sm, fontWeight: '500' },
  input: {
    color: colors.textPrimary,
    fontSize: fontSize.lg,
    minWidth: 120,
    textAlign: 'right',
    paddingVertical: spacing.xs,
  },
  segmented: {
    flexDirection: 'row',
    backgroundColor: colors.surfaceMuted,
    borderRadius: radius.sm,
    padding: 3,
    gap: 3,
  },
  segment: {
    flex: 1,
    paddingVertical: spacing.sm,
    alignItems: 'center',
    borderRadius: radius.sm - 2,
    borderWidth: 1,
    borderColor: 'transparent',
  },
  segmentLabel: { color: colors.textMuted, fontSize: fontSize.md, fontWeight: '600' },
  segmentLabelActive: { color: colors.background },
  resultRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  resultMuted: { color: colors.textMuted, fontSize: fontSize.md },
  placedText: { color: colors.accent, fontSize: fontSize.md, fontWeight: '600' },
  failText: { fontSize: fontSize.sm, lineHeight: 18 },
  reviewButton: {
    borderRadius: radius.md,
    paddingVertical: 14,
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: spacing.xs,
  },
  reviewLabel: { color: colors.background, fontSize: fontSize.lg, fontWeight: '700' },
});
