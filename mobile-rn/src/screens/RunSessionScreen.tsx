import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { View, Text, Pressable, ScrollView, StyleSheet } from 'react-native';
import { useShallow } from 'zustand/react/shallow';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import type { RootStackParamList } from '../navigation/RootNavigator';
import { useRunSession, selectOrderedAgents, type RunPhase } from '../state/runSessionStore';
import { useRunStream, useChart } from '../hooks/useRunController';
import { StageStepper } from '../components/StageStepper';
import { VerdictHero } from '../components/VerdictHero';
import { Economics } from '../components/Economics';
import { AgentFeed } from '../components/AgentFeed';
import { PanelPicker } from '../components/PanelPicker';
import { Banner } from '../components/primitives';
import { isActionable } from '../models/order';
import { apiClient } from '../services/apiClient';
import { colors, radius, spacing, withAlpha } from '../theme/theme';

type Props = NativeStackScreenProps<RootStackParamList, 'RunSession'>;

/**
 * The live run screen: status header, optional cache banner, stage stepper,
 * verdict hero, run economics, and the streaming agent feed (with the price
 * chart hosted inside the Market Analyst card). Ports the SwiftUI
 * `RunSessionView` container.
 */
export function RunSessionScreen({ route, navigation }: Props) {
  const params = route.params;

  // Initialize the store exactly once, synchronously, so children render fresh.
  const initialized = useRef(false);
  if (!initialized.current) {
    initialized.current = true;
    const store = useRunSession.getState();
    if (params.mode === 'started') {
      store.initStarted(params.runId, params.nodes, params.request);
    } else if (params.mode === 'restore') {
      store.initRestore(params.runId, params.request, params.nodes);
    } else {
      store.initCached(params.run);
    }
  }

  // Derive chart inputs + stream config from the mode. SSE streaming is only
  // started for live (`started`/`restore`) modes — a cached run is already
  // complete by definition. The `runId`, on the other hand, is needed by
  // every mode that has one so the voice layer (Talk button → voice
  // session) can attach to the persisted run. For cached hits, the id
  // comes from the Supabase row carried by the navigation params.
  const live = params.mode !== 'cached';
  const runId =
    params.mode === 'cached' ? params.run.runId : params.runId;
  const chartArgs =
    params.mode === 'cached'
      ? { ticker: params.run.ticker, tradeDate: params.run.tradeDate, assetType: params.run.assetType ?? 'stock' }
      : {
          ticker: params.request.ticker,
          tradeDate: params.request.tradeDate,
          assetType: params.request.assetType,
        };

  useRunStream({ runId, lastEventId: null, enabled: live });
  useChart(chartArgs.ticker, chartArgs.tradeDate, chartArgs.assetType);

  useLayoutEffect(() => {
    navigation.setOptions({
      title: `${chartArgs.ticker} · ${chartArgs.tradeDate}`,
      headerRight: () => (
        <Pressable onPress={() => navigation.popToTop()}>
          <Text style={styles.headerButton}>New run</Text>
        </Pressable>
      ),
    });
  }, [navigation, chartArgs.ticker, chartArgs.tradeDate]);

  // Capability probe for the Talk + Group Call buttons. Failures collapse to
  // off so the rest of the run UI is unaffected — see
  // `apiClient.fetchVoiceConfig`. ``panelReady`` is independent of
  // ``voiceReady``: the server can keep panel mode off while single-agent
  // voice ships, in which case we hide the Group Call entry point.
  const [voiceReady, setVoiceReady] = useState(false);
  const [panelReady, setPanelReady] = useState(false);
  const [panelMaxSize, setPanelMaxSize] = useState(3);
  useEffect(() => {
    let cancelled = false;
    void apiClient.fetchVoiceConfig().then((cfg) => {
      if (cancelled) return;
      setVoiceReady(cfg.ready);
      setPanelReady(cfg.ready && cfg.panelEnabled);
      setPanelMaxSize(cfg.panelMaxSize);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // Panel picker state — opened from the per-card "Group call" button with
  // that agent pre-checked.
  const [panelPickerOpen, setPanelPickerOpen] = useState(false);
  const [panelPreselect, setPanelPreselect] = useState<string | null>(null);
  const orderedAgents = useRunSession(useShallow(selectOrderedAgents));

  const phase = useRunSession((s) => s.phase);
  const statusMessage = useRunSession((s) => s.statusMessage);
  const isCached = useRunSession((s) => s.isCached);
  const cacheCreatedAt = useRunSession((s) => s.cacheCreatedAt);
  const errorMessage = useRunSession((s) => s.errorMessage);
  const hasEconomics = useRunSession(
    (s) => Object.keys(s.usageByNode).length > 0 || s.usageTotals != null,
  );
  const lastTrade = useRunSession((s) => s.lastTrade);
  const canReviewOrder = runId != null && lastTrade?.intent != null && isActionable(lastTrade.intent);
  const orderIsLive =
    (lastTrade?.canPlaceRealOrders ?? false) && !(lastTrade?.dryRun ?? true);

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      <StatusHeader phase={phase} statusMessage={statusMessage} />

      {isCached && (
        <Banner
          text={`Served from the 60-minute cache${cacheCreatedAt ? ` (${cacheCreatedAt})` : ''}.`}
          tone="warning"
        />
      )}
      {phase === 'failed' && errorMessage && <Banner text={errorMessage} tone="danger" />}

      <StageStepper />
      <VerdictHero />
      {hasEconomics && <Economics />}

      {canReviewOrder && runId != null && lastTrade?.intent != null && (
        <Pressable
          style={[styles.orderButton, orderIsLive && styles.orderButtonLive]}
          onPress={() => navigation.navigate('OrderTicket', { runId, trade: lastTrade })}
          accessibilityRole="button"
        >
          <Text style={styles.orderButtonText}>
            {`${orderIsLive ? 'Review LIVE' : 'Review'} ${lastTrade.intent.action.toUpperCase()} · ${lastTrade.intent.ticker}`}
          </Text>
        </Pressable>
      )}

      <AgentFeed
        voiceReady={voiceReady && runId != null}
        onTalkRequested={
          runId != null
            ? (agentId) => navigation.navigate('VoiceCall', { runId, agentId })
            : undefined
        }
        panelReady={panelReady && runId != null}
        onGroupCallRequested={
          runId != null
            ? (agentId) => {
                setPanelPreselect(agentId);
                setPanelPickerOpen(true);
              }
            : undefined
        }
      />

      <PanelPicker
        visible={panelPickerOpen}
        candidates={orderedAgents}
        preselected={panelPreselect}
        maxSize={panelMaxSize}
        onClose={() => setPanelPickerOpen(false)}
        onConfirm={(agentIds) => {
          setPanelPickerOpen(false);
          if (runId != null) {
            navigation.navigate('VoiceCall', { runId, agentIds });
          }
        }}
      />
    </ScrollView>
  );
}

function StatusHeader({ phase, statusMessage }: { phase: RunPhase; statusMessage: string }) {
  const color =
    phase === 'failed'
      ? colors.danger
      : phase === 'streaming' || phase === 'finished'
        ? colors.accent
        : colors.warning;
  const text = statusText(phase, statusMessage);
  const showTimer = phase === 'streaming' || phase === 'connecting';

  return (
    <View style={styles.header}>
      <View style={[styles.dot, { backgroundColor: color }]} />
      <Text style={styles.statusText}>{text}</Text>
      <View style={{ flex: 1 }} />
      {showTimer && <ElapsedTimer />}
    </View>
  );
}

function statusText(phase: RunPhase, statusMessage: string): string {
  switch (phase) {
    case 'idle':
      return 'Idle';
    case 'connecting':
      return 'Connecting…';
    case 'streaming':
      return statusMessage || 'Running…';
    case 'finished':
      return statusMessage || 'Complete';
    case 'failed':
      return 'Failed';
    default: {
      const _never: never = phase;
      return _never;
    }
  }
}

function ElapsedTimer() {
  const start = useRef(Date.now()).current;
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, []);
  const elapsed = Math.floor((Date.now() - start) / 1000);
  const mm = Math.floor(elapsed / 60);
  const ss = String(elapsed % 60).padStart(2, '0');
  return <Text style={styles.timer}>{`${mm}:${ss}`}</Text>;
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.background },
  content: { padding: spacing.lg, gap: spacing.lg, paddingBottom: spacing.xl * 2 },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    padding: spacing.md,
    borderRadius: radius.md,
    backgroundColor: withAlpha(colors.surface, 0.5),
  },
  dot: { width: 10, height: 10, borderRadius: 5 },
  statusText: { color: colors.textPrimary, fontSize: 14, fontWeight: '500' },
  timer: { color: colors.textMuted, fontSize: 14, fontVariant: ['tabular-nums'] },
  headerButton: { color: colors.accent, fontSize: 15 },
  orderButton: {
    paddingVertical: spacing.md,
    paddingHorizontal: spacing.lg,
    borderRadius: radius.md,
    alignItems: 'center',
    backgroundColor: colors.accent,
  },
  orderButtonLive: { backgroundColor: colors.danger },
  orderButtonText: { color: colors.background, fontSize: 15, fontWeight: '700' },
});
