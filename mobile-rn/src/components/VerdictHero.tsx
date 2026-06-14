import React, { useState } from 'react';
import { View, Text, Pressable, StyleSheet, type LayoutChangeEvent } from 'react-native';
import Svg, { Defs, LinearGradient, Stop, Rect, Circle } from 'react-native-svg';
import { useRunSession } from '../state/runSessionStore';
import {
  VERDICT_ORDER,
  VERDICT_COLOR,
  VERDICT_GRADIENT,
  VERDICT_GAUGE_POSITION,
} from '../models/verdict';
import { MarkdownBody } from './MarkdownBody';
import { colors, radius, spacing, withAlpha } from '../theme/theme';

/**
 * Verdict hero: the large rating word, a Sell→Buy gradient gauge with a marker,
 * and an expandable PM rationale. Ports `showVerdict` + the gauge from
 * `index.html` (`RATING_POS` / `RATING_COLOR`) and the Swift `VerdictView`.
 */
export function VerdictHero() {
  const decision = useRunSession((s) => s.decision);
  const verdict = useRunSession((s) => s.verdict);
  const rationale = useRunSession((s) => s.rationale);
  const phase = useRunSession((s) => s.phase);
  const isStreaming = phase === 'streaming' || phase === 'connecting';

  const [showRationale, setShowRationale] = useState(false);
  const [width, setWidth] = useState(0);

  const onLayout = (e: LayoutChangeEvent) => setWidth(e.nativeEvent.layout.width);
  const markerX = verdict ? (width * VERDICT_GAUGE_POSITION[verdict]) / 100 : 0;
  const clampedX = Math.min(Math.max(9, markerX), Math.max(9, width - 9));

  return (
    <View style={styles.card}>
      <Text
        style={[styles.rating, { color: verdict ? VERDICT_COLOR[verdict] : colors.textMuted }]}
      >
        {decision ?? (isStreaming ? '…' : '—')}
      </Text>

      <View style={styles.gaugeWrap} onLayout={onLayout}>
        {width > 0 && (
          <Svg width={width} height={18}>
            <Defs>
              <LinearGradient id="verdictGradient" x1="0" y1="0" x2="1" y2="0">
                {VERDICT_GRADIENT.map((color, i) => (
                  <Stop
                    key={color}
                    offset={`${(i / (VERDICT_GRADIENT.length - 1)) * 100}%`}
                    stopColor={color}
                  />
                ))}
              </LinearGradient>
            </Defs>
            <Rect x={0} y={4} width={width} height={10} rx={5} fill="url(#verdictGradient)" />
            {verdict && (
              <Circle cx={clampedX} cy={9} r={8} fill="#ffffff" stroke={VERDICT_COLOR[verdict]} strokeWidth={3} />
            )}
          </Svg>
        )}
      </View>

      <View style={styles.tierRow}>
        {VERDICT_ORDER.map((tier) => (
          <Text
            key={tier}
            style={[
              styles.tierLabel,
              { color: verdict === tier ? VERDICT_COLOR[tier] : colors.textMuted },
            ]}
          >
            {tier}
          </Text>
        ))}
      </View>

      {rationale ? (
        <View style={styles.rationale}>
          <Pressable onPress={() => setShowRationale((v) => !v)}>
            <Text style={styles.disclosure}>
              {showRationale ? '▾' : '▸'} Portfolio Manager rationale
            </Text>
          </Pressable>
          {showRationale && (
            <View style={{ marginTop: spacing.sm }}>
              <MarkdownBody source={rationale} />
            </View>
          )}
        </View>
      ) : isStreaming ? (
        <Text style={styles.deliberating}>Agents are deliberating…</Text>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: withAlpha(colors.surface, 0.5),
    borderRadius: radius.xl,
    padding: spacing.lg,
    gap: spacing.md,
    alignItems: 'stretch',
  },
  rating: { fontSize: 38, fontWeight: '800', textAlign: 'center' },
  gaugeWrap: { height: 18, justifyContent: 'center' },
  tierRow: { flexDirection: 'row', justifyContent: 'space-between' },
  tierLabel: { fontSize: 11, flex: 1, textAlign: 'center' },
  rationale: { marginTop: spacing.xs },
  disclosure: { color: colors.textPrimary, fontSize: 14, fontWeight: '600' },
  deliberating: { color: colors.textMuted, fontSize: 13, textAlign: 'center' },
});
