import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  View,
  Text,
  TextInput,
  ScrollView,
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  StyleSheet,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import { useFocusEffect } from '@react-navigation/native';
import DateTimePicker, {
  DateTimePickerAndroid,
  type DateTimePickerEvent,
} from '@react-native-community/datetimepicker';
import type { RootStackParamList } from '../navigation/RootNavigator';
import { useConfig } from '../hooks/useConfig';
import { apiClient } from '../services/apiClient';
import { describeError } from '../services/apiError';
import {
  ANALYST_ORDER,
  ASSET_TYPES,
  type AssetType,
  type RunRequest,
} from '../models/run';
import { ANALYST_DISPLAY_NAME, agentMetadata } from '../models/agentNode';
import { activeRunStore, type ActiveRun } from '../state/activeRunStore';
import { lightFeedback, selectionFeedback } from '../utils/haptics';
import { Banner, Card, TouchTarget } from '../components/primitives';
import { Icon } from '../components/Icon';
import { colors, fontSize, radius, spacing, withAlpha } from '../theme/theme';

type Props = NativeStackScreenProps<RootStackParamList, 'RunSetup'>;

/** Reserved trailing-slot width for the chip checkmark — keeps chip width stable when toggled. */
const CHIP_CHECK_SIZE = 14;

/**
 * Parse a YYYY-MM-DD trade date into a `Date` anchored at LOCAL noon. Using
 * noon (not midnight) keeps the calendar day stable for any reasonable device
 * timezone, so the native picker highlights exactly the date the field shows.
 * Falls back to "today" when the string is malformed so the picker always
 * opens on a sensible day.
 */
function parseTradeDateLocal(value: string): Date {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value.trim());
  if (!match) return new Date();
  const year = Number(match[1]);
  const month = Number(match[2]) - 1;
  const day = Number(match[3]);
  if (!Number.isFinite(year) || !Number.isFinite(month) || !Number.isFinite(day)) {
    return new Date();
  }
  return new Date(year, month, day, 12, 0, 0, 0);
}

/**
 * Format a `Date` produced by the native picker back to the YYYY-MM-DD wire
 * shape the backend expects. We deliberately use LOCAL components (mirroring
 * what the user just saw highlighted in the picker) rather than UTC — picking
 * "Jun 13" in the picker must round-trip to "2026-06-13" regardless of the
 * device's UTC offset.
 */
function formatTradeDateLocal(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

/**
 * The run configuration form. Mirrors the web form / SwiftUI `RunSetupView`:
 * ticker, trade date, asset type, analyst multi-select, and an Advanced
 * disclosure. Defaults come from `GET /api/config`; client validation matches
 * the server's 400s (non-empty ticker, ≥1 analyst).
 *
 * Layout: `KeyboardAvoidingView` wraps a `ScrollView` (form) and a sticky
 * bottom action bar that hosts the inline banners and the "Run analysis" CTA,
 * so the primary action is always reachable regardless of scroll position or
 * keyboard state.
 */
export function RunSetupScreen({ navigation }: Props) {
  const config = useConfig();
  const insets = useSafeAreaInsets();

  const [ticker, setTicker] = useState('NVDA');
  // Default to today's LOCAL calendar date using the same formatter the picker
  // round-trips through, so the field and the picker agree regardless of UTC
  // offset (a UTC default could show tomorrow's date late in the day).
  const [tradeDate, setTradeDate] = useState(() => formatTradeDateLocal(new Date()));
  const [assetType, setAssetType] = useState<AssetType>('stock');
  const [selected, setSelected] = useState<string[]>(['market', 'social', 'news', 'fundamentals']);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [provider, setProvider] = useState('');
  const [deepModel, setDeepModel] = useState('');
  const [quickModel, setQuickModel] = useState('');
  const [debateRounds, setDebateRounds] = useState('');
  const [riskRounds, setRiskRounds] = useState('');
  const [isStarting, setIsStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const [resumable, setResumable] = useState<ActiveRun | null>(null);
  // iOS shows the picker inline beneath the field; Android uses the imperative
  // `DateTimePickerAndroid.open` modal which manages its own dismissal.
  const [iosPickerOpen, setIosPickerOpen] = useState(false);

  // Bound the picker so a user can't pick a future date for an analysis. We
  // intentionally compute this once per render (cheap) so it stays aligned
  // with "now" without holding stale state.
  const todayCeiling = new Date();
  const pickerValue = useMemo(() => parseTradeDateLocal(tradeDate), [tradeDate]);

  // Reload the resume card whenever the screen regains focus (not just on mount),
  // so a run that finished/failed while away doesn't leave a stale resume card.
  useFocusEffect(
    useCallback(() => {
      let active = true;
      void activeRunStore.load().then((run) => {
        if (active) setResumable(run);
      });
      return () => {
        active = false;
      };
    }, []),
  );

  // Apply server defaults for debate/risk rounds once config loads.
  useEffect(() => {
    if (!config.data) return;
    if (config.data.maxDebateRounds != null) setDebateRounds(String(config.data.maxDebateRounds));
    if (config.data.maxRiskRounds != null) setRiskRounds(String(config.data.maxRiskRounds));
  }, [config.data]);

  const analysts = useMemo(() => {
    const keys = config.data?.analysts.length ? config.data.analysts : ANALYST_ORDER;
    return keys.map((key) => ({
      key,
      label: config.data?.analystDisplay[key] ?? ANALYST_DISPLAY_NAME[key] ?? key,
    }));
  }, [config.data]);

  const canStart = ticker.trim().length > 0 && selected.length > 0 && !isStarting;

  const toggleAnalyst = (key: string) => {
    selectionFeedback();
    setSelected((prev) => (prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]));
  };

  const handlePickerChange = useCallback(
    (event: DateTimePickerEvent, picked?: Date) => {
      if (event.type === 'set' && picked) {
        setTradeDate(formatTradeDateLocal(picked));
      }
    },
    [],
  );

  const openDatePicker = useCallback(() => {
    selectionFeedback();
    if (Platform.OS === 'android') {
      DateTimePickerAndroid.open({
        value: pickerValue,
        mode: 'date',
        maximumDate: todayCeiling,
        onChange: handlePickerChange,
      });
      return;
    }
    setIosPickerOpen((open) => !open);
  }, [pickerValue, todayCeiling, handlePickerChange]);

  const makeRequest = (force: boolean): RunRequest => {
    const order = config.data?.analysts.length ? config.data.analysts : ANALYST_ORDER;
    const orderedAnalysts = order.filter((k) => selected.includes(k));
    const parseInt = (s: string): number | null => {
      const n = Number.parseInt(s.trim(), 10);
      return Number.isFinite(n) ? n : null;
    };
    const trimOrNull = (s: string): string | null => (s.trim() ? s.trim() : null);
    return {
      ticker: ticker.trim().toUpperCase(),
      tradeDate: tradeDate.trim(),
      analysts: orderedAnalysts,
      assetType,
      provider: trimOrNull(provider),
      deepModel: trimOrNull(deepModel),
      quickModel: trimOrNull(quickModel),
      maxDebateRounds: parseInt(debateRounds),
      maxRiskRounds: parseInt(riskRounds),
      force,
    };
  };

  const start = async (force = false) => {
    lightFeedback();
    if (!canStart) {
      setStartError('Enter a ticker and pick at least one analyst.');
      return;
    }
    setIsStarting(true);
    setStartError(null);
    try {
      const request = makeRequest(force);
      const response = await apiClient.startRun(request);
      if (response.cached) {
        navigation.navigate('RunSession', { mode: 'cached', run: response.run });
      } else {
        await activeRunStore.save({ runId: response.runId, request, nodes: response.nodes });
        navigation.navigate('RunSession', {
          mode: 'started',
          runId: response.runId,
          nodes: response.nodes,
          request,
        });
      }
    } catch (error) {
      setStartError(describeError(error));
    } finally {
      setIsStarting(false);
    }
  };

  const resume = (run: ActiveRun) => {
    lightFeedback();
    navigation.navigate('RunSession', {
      mode: 'restore',
      runId: run.runId,
      request: run.request,
      nodes: run.nodes,
    });
  };

  return (
    <KeyboardAvoidingView
      style={styles.screen}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <ScrollView
        style={styles.scroll}
        contentContainerStyle={styles.content}
        keyboardShouldPersistTaps="handled"
      >
        {resumable && (
          <TouchTarget
            onPress={() => resume(resumable)}
            accessibilityLabel={`Resume in-flight run for ${resumable.request.ticker}`}
          >
            <Card style={styles.resume}>
              <Text style={styles.resumeTitle}>↻ Resume in-flight run</Text>
              <Text style={styles.resumeSub}>
                {resumable.request.ticker} · {resumable.request.tradeDate}
              </Text>
            </Card>
          </TouchTarget>
        )}

        <Section title="What to analyze">
          <Field label="Ticker">
            <TextInput
              value={ticker}
              onChangeText={setTicker}
              autoCapitalize="characters"
              autoCorrect={false}
              placeholder="e.g. NVDA"
              placeholderTextColor={colors.textMuted}
              style={styles.input}
            />
          </Field>
          <Field label="Trade date">
            <TouchTarget
              onPress={openDatePicker}
              feedback="opacity"
              accessibilityRole="button"
              accessibilityLabel={`Trade date, ${tradeDate}. Opens date picker.`}
              accessibilityState={{ expanded: iosPickerOpen }}
              style={styles.dateField}
            >
              <Text style={styles.dateFieldText}>{tradeDate || 'Select date'}</Text>
              <Icon name="calendar" size={18} color={colors.textMuted} />
            </TouchTarget>
            {Platform.OS === 'ios' && iosPickerOpen && (
              <View style={styles.inlinePickerWrap}>
                <DateTimePicker
                  value={pickerValue}
                  mode="date"
                  display="inline"
                  maximumDate={todayCeiling}
                  onChange={handlePickerChange}
                  accentColor={colors.accent}
                  themeVariant="dark"
                  textColor={colors.textPrimary}
                  style={styles.inlinePicker}
                />
              </View>
            )}
          </Field>
          <Field label="Asset type">
            <View style={styles.segment}>
              {ASSET_TYPES.map((type) => {
                const on = assetType === type;
                return (
                  <TouchTarget
                    key={type}
                    onPress={() => {
                      if (!on) selectionFeedback();
                      setAssetType(type);
                    }}
                    feedback="opacity"
                    accessibilityRole="button"
                    accessibilityState={{ selected: on }}
                    accessibilityLabel={type}
                    style={[styles.segmentItem, on && styles.segmentItemOn]}
                  >
                    <Text style={[styles.segmentText, on && styles.segmentTextOn]}>
                      {type[0].toUpperCase() + type.slice(1)}
                    </Text>
                  </TouchTarget>
                );
              })}
            </View>
          </Field>
        </Section>

        <Section
          title="Analyst team"
          footer="Plus 8 fixed agents (researchers, manager, trader, risk team, PM)."
        >
          <View style={styles.chips}>
            {analysts.map(({ key, label }) => {
              const meta = agentMetadata(ANALYST_DISPLAY_NAME[key] ?? key);
              const on = selected.includes(key);
              return (
                <TouchTarget
                  key={key}
                  onPress={() => toggleAnalyst(key)}
                  feedback="scale"
                  accessibilityRole="checkbox"
                  accessibilityState={{ checked: on }}
                  accessibilityLabel={label}
                  style={[
                    styles.chip,
                    {
                      borderColor: on ? meta.color : colors.border,
                      backgroundColor: on
                        ? withAlpha(meta.color, 0.18)
                        : withAlpha(colors.surface, 0.4),
                    },
                  ]}
                >
                  <Text style={styles.chipText}>
                    {meta.icon} {label}
                  </Text>
                  <View style={styles.chipCheckSlot}>
                    {on && <Icon name="check" size={CHIP_CHECK_SIZE} color={meta.color} />}
                  </View>
                </TouchTarget>
              );
            })}
          </View>
        </Section>

        <Section title="Advanced">
          <TouchTarget
            onPress={() => {
              selectionFeedback();
              setShowAdvanced((v) => !v);
            }}
            accessibilityRole="button"
            accessibilityState={{ expanded: showAdvanced }}
            style={styles.disclosureRow}
          >
            <Text style={styles.disclosure}>Models & debate rounds</Text>
            <Icon
              name={showAdvanced ? 'chevron-up' : 'chevron-down'}
              size={16}
              color={colors.textMuted}
            />
          </TouchTarget>
          {showAdvanced && (
            <View style={{ gap: spacing.sm, marginTop: spacing.sm }}>
              <LabeledInput
                label="Provider (optional)"
                value={provider}
                onChangeText={setProvider}
              />
              <LabeledInput
                label="Deep model (optional)"
                value={deepModel}
                onChangeText={setDeepModel}
              />
              <LabeledInput
                label="Quick model (optional)"
                value={quickModel}
                onChangeText={setQuickModel}
              />
              <View style={{ flexDirection: 'row', gap: spacing.sm }}>
                <View style={{ flex: 1 }}>
                  <LabeledInput
                    label="Debate rounds"
                    value={debateRounds}
                    onChangeText={setDebateRounds}
                    numeric
                  />
                </View>
                <View style={{ flex: 1 }}>
                  <LabeledInput
                    label="Risk rounds"
                    value={riskRounds}
                    onChangeText={setRiskRounds}
                    numeric
                  />
                </View>
              </View>
            </View>
          )}
        </Section>
      </ScrollView>

      <View style={[styles.ctaBar, { paddingBottom: spacing.md + insets.bottom }]}>
        {startError && <Banner text={startError} tone="danger" icon />}
        {config.isError && (
          <Banner text="Using built-in defaults — couldn't reach the backend." tone="warning" icon />
        )}
        <TouchTarget
          onPress={() => start(false)}
          disabled={!canStart}
          feedback="scale"
          accessibilityLabel="Run analysis"
          style={[styles.cta, { backgroundColor: canStart ? colors.accent : colors.border }]}
        >
          {isStarting ? (
            <ActivityIndicator color={colors.background} />
          ) : (
            <Text style={styles.ctaText}>Run analysis</Text>
          )}
        </TouchTarget>
      </View>
    </KeyboardAvoidingView>
  );
}

function Section({
  title,
  footer,
  children,
}: {
  title: string;
  footer?: string;
  children: React.ReactNode;
}) {
  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>{title}</Text>
      <Card>{children}</Card>
      {footer && <Text style={styles.footer}>{footer}</Text>}
    </View>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <View style={{ gap: spacing.xs, marginBottom: spacing.sm }}>
      <Text style={styles.fieldLabel}>{label}</Text>
      {children}
    </View>
  );
}

function LabeledInput({
  label,
  value,
  onChangeText,
  numeric,
}: {
  label: string;
  value: string;
  onChangeText: (s: string) => void;
  numeric?: boolean;
}) {
  return (
    <View style={{ gap: spacing.xs }}>
      <Text style={styles.fieldLabel}>{label}</Text>
      <TextInput
        value={value}
        onChangeText={onChangeText}
        autoCapitalize="none"
        autoCorrect={false}
        keyboardType={numeric ? 'number-pad' : 'default'}
        placeholderTextColor={colors.textMuted}
        style={styles.input}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.background },
  scroll: { flex: 1 },
  content: { padding: spacing.lg, gap: spacing.lg, paddingBottom: spacing.lg },
  section: { gap: spacing.sm },
  sectionTitle: {
    color: colors.textMuted,
    fontSize: fontSize.sm,
    fontWeight: '700',
    textTransform: 'uppercase',
    letterSpacing: 0.6,
  },
  footer: { color: colors.textMuted, fontSize: fontSize.xs },
  fieldLabel: { color: colors.textPrimary, fontSize: fontSize.sm, fontWeight: '500' },
  input: {
    color: colors.textPrimary,
    backgroundColor: colors.surfaceMuted,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: radius.sm,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.md,
    fontSize: fontSize.lg,
  },
  dateField: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: colors.surfaceMuted,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: radius.sm,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.md,
  },
  dateFieldText: { color: colors.textPrimary, fontSize: fontSize.lg },
  inlinePickerWrap: {
    backgroundColor: colors.surfaceMuted,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: radius.sm,
    marginTop: spacing.sm,
    overflow: 'hidden',
  },
  inlinePicker: { alignSelf: 'stretch' },
  segment: { flexDirection: 'row', gap: spacing.xs, flexWrap: 'wrap' },
  segmentItem: {
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.md,
    borderRadius: radius.sm,
    borderWidth: 1,
    borderColor: colors.border,
  },
  segmentItemOn: { borderColor: colors.accent, backgroundColor: withAlpha(colors.accent, 0.18) },
  segmentText: { color: colors.textMuted, fontSize: fontSize.sm },
  segmentTextOn: { color: colors.textPrimary, fontWeight: '600' },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  chip: {
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.md,
    borderRadius: radius.sm,
    borderWidth: 1,
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.xs,
  },
  chipText: { color: colors.textPrimary, fontSize: fontSize.sm },
  chipCheckSlot: { width: CHIP_CHECK_SIZE, alignItems: 'center', justifyContent: 'center' },
  disclosureRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingVertical: spacing.sm,
  },
  disclosure: { color: colors.textPrimary, fontSize: fontSize.md, fontWeight: '600' },
  ctaBar: {
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.md,
    backgroundColor: colors.background,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.border,
    gap: spacing.sm,
  },
  cta: {
    borderRadius: radius.md,
    paddingVertical: spacing.md,
    alignItems: 'center',
    justifyContent: 'center',
  },
  ctaText: { color: colors.background, fontSize: fontSize.lg, fontWeight: '700' },
  resume: { borderColor: colors.accent, borderWidth: 1, gap: spacing.xs },
  resumeTitle: { color: colors.accent, fontWeight: '700', fontSize: fontSize.md },
  resumeSub: { color: colors.textMuted, fontSize: fontSize.xs },
});
