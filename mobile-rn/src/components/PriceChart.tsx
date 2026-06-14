import React, { useMemo, useRef, useState } from 'react';
import {
  View,
  Text,
  ScrollView,
  StyleSheet,
  PanResponder,
  type LayoutChangeEvent,
} from 'react-native';
import Svg, { Line, Rect, Polyline, Text as SvgText } from 'react-native-svg';
import {
  type Candle,
  type ChartPayload,
  type VolumeBar,
  OVERLAY_STYLE,
  RSI_INDICATOR,
  isCandleUp,
} from '../models/chart';
import { colors, fontSize, spacing, withAlpha } from '../theme/theme';

const PRICE_HEIGHT = 240;
const VOLUME_HEIGHT = 64;
const RSI_HEIGHT = 96;
const PAD_Y = 8;
/** Left gutter reserved for Y-axis price/RSI labels (price formatter assumes ≤6 chars). */
const LABEL_GUTTER = 44;
/** Bottom strip reserved for X-axis date labels (price pane only — volume/RSI share the same X). */
const X_LABEL_HEIGHT = 16;
const Y_TICK_COUNT = 4;
const X_TICK_COUNT = 4;
const AXIS_FONT_SIZE = 10;
const CROSSHAIR_COLOR = '#cbd5e1';
const MONTH_NAMES = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
];

/**
 * Candlestick + volume + RSI chart for the Market Analyst card, built on
 * `react-native-svg`. RN parity for the SwiftUI `PriceChartView` (Apple
 * Charts) / the web's Lightweight Charts.
 *
 * Layout — three stacked panes share the LABEL_GUTTER and an identical X
 * scale (`step = plotWidth / candles.length`), so the crosshair drawn in one
 * pane lands on the same candle column across all three:
 *   Price   (PRICE_HEIGHT 240 incl. X_LABEL_HEIGHT 16)
 *   Volume  (VOLUME_HEIGHT 64)
 *   RSI     (RSI_HEIGHT 96)
 *
 * Price Y-axis uses a "nice numbers" algorithm so labels round to clean
 * values (100, 110, 120) and the projection's `lo`/`hi` expand to the nice
 * bounds — overlays (SMA/EMA/Bollinger) reuse the same projection and so
 * stay correct without per-overlay code.
 *
 * A single-touch `PanResponder` on the price pane drives a shared
 * `activeIndex`; multi-finger gestures and parent ScrollView vertical
 * scrolling are deliberately yielded.
 */
export function PriceChart({ payload }: { payload: ChartPayload }) {
  const [width, setWidth] = useState(0);
  const [activeIndex, setActiveIndex] = useState<number | null>(null);
  const onLayout = (e: LayoutChangeEvent) => setWidth(e.nativeEvent.layout.width);

  const candles = payload.candles;
  const n = candles.length;
  const hasRsi = Boolean(payload.indicators[RSI_INDICATOR]?.length);
  const hasVolume = payload.volume.length > 0;

  const plotWidth = Math.max(1, width - LABEL_GUTTER);
  const step = n > 0 ? plotWidth / n : 0;

  // Keep gesture math fresh without re-creating PanResponder every render;
  // closures captured at create-time would otherwise see stale step/n.
  const geomRef = useRef({ step, n });
  geomRef.current = { step, n };

  const panResponder = useMemo(
    () =>
      PanResponder.create({
        // Only claim single-finger touches — pinch / multi-finger gestures
        // (and the parent ScrollView's vertical pan) stay free.
        onStartShouldSetPanResponder: (_evt, g) => g.numberActiveTouches === 1,
        onMoveShouldSetPanResponder: (_evt, g) => g.numberActiveTouches === 1,
        onPanResponderGrant: (evt) => {
          const { step: s, n: nn } = geomRef.current;
          setActiveIndex(nearestIndexForTouchX(evt.nativeEvent.locationX, LABEL_GUTTER, s, nn));
        },
        onPanResponderMove: (evt) => {
          const { step: s, n: nn } = geomRef.current;
          setActiveIndex(nearestIndexForTouchX(evt.nativeEvent.locationX, LABEL_GUTTER, s, nn));
        },
        onPanResponderRelease: () => setActiveIndex(null),
        onPanResponderTerminate: () => setActiveIndex(null),
        // Yield to a parent ScrollView's vertical scroll when it asks.
        onPanResponderTerminationRequest: () => true,
      }),
    [],
  );

  const activeCandle: Candle | null = activeIndex != null ? candles[activeIndex] ?? null : null;
  const a11yLabel = useMemo(() => buildA11yLabel(payload), [payload]);

  return (
    <View style={styles.root} onLayout={onLayout}>
      <Header payload={payload} active={activeCandle} />

      {width > 0 && n > 0 ? (
        <View
          accessible
          accessibilityLabel={a11yLabel}
          style={styles.priceStack}
        >
          <View {...panResponder.panHandlers}>
            <PricePane payload={payload} width={width} step={step} n={n} activeIndex={activeIndex} />
          </View>
          {hasVolume && (
            <VolumePane payload={payload} width={width} step={step} activeIndex={activeIndex} />
          )}
        </View>
      ) : (
        <Text style={styles.empty}>No candle data.</Text>
      )}

      <Legend payload={payload} />

      {hasRsi && width > 0 && n > 0 && (
        <>
          <Text style={styles.rsiLabel}>RSI</Text>
          <RsiPane payload={payload} width={width} step={step} activeIndex={activeIndex} />
        </>
      )}
    </View>
  );
}

function Header({ payload, active }: { payload: ChartPayload; active: Candle | null }) {
  return (
    <View style={styles.header}>
      <Text style={styles.symbol}>{payload.symbol}</Text>
      {active ? (
        <Text style={styles.readout} numberOfLines={1} adjustsFontSizeToFit minimumFontScale={0.7}>
          {`${formatDate(active.time)}  O ${formatPrice(active.open)}  H ${formatPrice(active.high)}  L ${formatPrice(active.low)}  C ${formatPrice(active.close)}`}
        </Text>
      ) : (
        <Text style={styles.range} numberOfLines={1}>
          {payload.from} → {payload.to}
        </Text>
      )}
    </View>
  );
}

function PricePane({
  payload,
  width,
  step,
  n,
  activeIndex,
}: {
  payload: ChartPayload;
  width: number;
  step: number;
  n: number;
  activeIndex: number | null;
}) {
  const candles = payload.candles;
  const plotHeight = PRICE_HEIGHT - 2 * PAD_Y - X_LABEL_HEIGHT;
  const bodyWidth = Math.min(6, Math.max(2, step * 0.6));

  let rawLo = Infinity;
  let rawHi = -Infinity;
  for (const c of candles) {
    if (c.low < rawLo) rawLo = c.low;
    if (c.high > rawHi) rawHi = c.high;
  }
  for (const { key } of OVERLAY_STYLE) {
    for (const p of payload.indicators[key] ?? []) {
      if (p.value < rawLo) rawLo = p.value;
      if (p.value > rawHi) rawHi = p.value;
    }
  }
  if (!Number.isFinite(rawLo) || !Number.isFinite(rawHi)) {
    rawLo = 0;
    rawHi = 1;
  }

  const { lo, hi, ticks: yTicks } = niceTicks(rawLo, rawHi, Y_TICK_COUNT);

  const y = (value: number) => PAD_Y + (1 - (value - lo) / (hi - lo)) * plotHeight;
  const x = (i: number) => LABEL_GUTTER + (i + 0.5) * step;

  const indexByTime = new Map<string, number>();
  candles.forEach((c, i) => indexByTime.set(c.time, i));

  const xTicks = pickXTickIndices(n, X_TICK_COUNT);
  const crossX = activeIndex != null ? x(activeIndex) : null;

  return (
    <Svg width={width} height={PRICE_HEIGHT}>
      {yTicks.map((value) => (
        <Line
          key={`grid-${value}`}
          x1={LABEL_GUTTER}
          y1={y(value)}
          x2={width}
          y2={y(value)}
          stroke={colors.border}
          strokeWidth={0.5}
          opacity={0.35}
        />
      ))}

      {yTicks.map((value) => (
        <SvgText
          key={`y-${value}`}
          x={LABEL_GUTTER - 6}
          y={y(value) + AXIS_FONT_SIZE / 3}
          fill={colors.textMuted}
          fontSize={AXIS_FONT_SIZE}
          textAnchor="end"
        >
          {formatPrice(value)}
        </SvgText>
      ))}

      {candles.map((c, i) => {
        const color = isCandleUp(c) ? colors.up : colors.down;
        const cx = x(i);
        const openY = y(c.open);
        const closeY = y(c.close);
        const top = Math.min(openY, closeY);
        const bodyHeight = Math.max(1, Math.abs(openY - closeY));
        return (
          <React.Fragment key={c.time}>
            <Line x1={cx} y1={y(c.high)} x2={cx} y2={y(c.low)} stroke={color} strokeWidth={1} />
            <Rect x={cx - bodyWidth / 2} y={top} width={bodyWidth} height={bodyHeight} fill={color} />
          </React.Fragment>
        );
      })}

      {OVERLAY_STYLE.map((style) => {
        const series = payload.indicators[style.key];
        if (!series?.length) return null;
        const points = series
          .map((p) => {
            const idx = indexByTime.get(p.time);
            if (idx == null) return null;
            return `${x(idx)},${y(p.value)}`;
          })
          .filter((s): s is string => s != null)
          .join(' ');
        if (!points) return null;
        return (
          <Polyline key={style.key} points={points} fill="none" stroke={style.color} strokeWidth={1.2} />
        );
      })}

      {xTicks.map((i) => {
        const candle = candles[i];
        if (!candle) return null;
        return (
          <SvgText
            key={`x-${candle.time}`}
            x={x(i)}
            y={PRICE_HEIGHT - 4}
            fill={colors.textMuted}
            fontSize={AXIS_FONT_SIZE}
            textAnchor="middle"
          >
            {formatDate(candle.time)}
          </SvgText>
        );
      })}

      {crossX != null && (
        <Line
          x1={crossX}
          y1={PAD_Y}
          x2={crossX}
          y2={PAD_Y + plotHeight}
          stroke={CROSSHAIR_COLOR}
          strokeWidth={1}
          strokeDasharray="3 3"
          opacity={0.85}
        />
      )}
    </Svg>
  );
}

function VolumePane({
  payload,
  width,
  step,
  activeIndex,
}: {
  payload: ChartPayload;
  width: number;
  step: number;
  activeIndex: number | null;
}) {
  const plotHeight = VOLUME_HEIGHT - 2 * PAD_Y;
  const bottom = PAD_Y + plotHeight;
  const bodyWidth = Math.min(6, Math.max(2, step * 0.6));

  let volMax = 0;
  for (const b of payload.volume) {
    if (b.value > volMax) volMax = b.value;
  }
  if (volMax <= 0) volMax = 1;

  const indexByTime = new Map<string, number>();
  payload.candles.forEach((c, i) => indexByTime.set(c.time, i));

  const x = (i: number) => LABEL_GUTTER + (i + 0.5) * step;
  const crossX = activeIndex != null ? x(activeIndex) : null;

  return (
    <Svg width={width} height={VOLUME_HEIGHT}>
      <Line
        x1={LABEL_GUTTER}
        y1={bottom}
        x2={width}
        y2={bottom}
        stroke={colors.border}
        strokeWidth={0.5}
        opacity={0.35}
      />

      <SvgText
        x={LABEL_GUTTER - 6}
        y={PAD_Y + AXIS_FONT_SIZE - 1}
        fill={colors.textMuted}
        fontSize={AXIS_FONT_SIZE}
        textAnchor="end"
      >
        {formatVolume(volMax)}
      </SvgText>

      {payload.volume.map((bar) => {
        const idx = indexByTime.get(bar.time);
        if (idx == null) return null;
        const candle = payload.candles[idx];
        const baseColor =
          bar.color != null
            ? bar.color
            : candle
              ? isCandleUp(candle)
                ? colors.up
                : colors.down
              : colors.textMuted;
        const h = Math.max(1, (bar.value / volMax) * plotHeight);
        const cx = x(idx);
        return (
          <Rect
            key={`vol-${bar.time}`}
            x={cx - bodyWidth / 2}
            y={bottom - h}
            width={bodyWidth}
            height={h}
            fill={withAlpha(baseColor, 0.7)}
          />
        );
      })}

      {crossX != null && (
        <Line
          x1={crossX}
          y1={PAD_Y}
          x2={crossX}
          y2={bottom}
          stroke={CROSSHAIR_COLOR}
          strokeWidth={1}
          strokeDasharray="3 3"
          opacity={0.85}
        />
      )}
    </Svg>
  );
}

function RsiPane({
  payload,
  width,
  step,
  activeIndex,
}: {
  payload: ChartPayload;
  width: number;
  step: number;
  activeIndex: number | null;
}) {
  const plotHeight = RSI_HEIGHT - 2 * PAD_Y;

  const indexByTime = new Map<string, number>();
  payload.candles.forEach((c, i) => indexByTime.set(c.time, i));

  const y = (value: number) => PAD_Y + (1 - value / 100) * plotHeight;
  const x = (i: number) => LABEL_GUTTER + (i + 0.5) * step;

  const series = payload.indicators[RSI_INDICATOR] ?? [];
  const points = series
    .map((p) => {
      const idx = indexByTime.get(p.time);
      if (idx == null) return null;
      return `${x(idx)},${y(p.value)}`;
    })
    .filter((s): s is string => s != null)
    .join(' ');

  const crossX = activeIndex != null ? x(activeIndex) : null;

  return (
    <Svg width={width} height={RSI_HEIGHT}>
      <Line
        x1={LABEL_GUTTER}
        y1={y(70)}
        x2={width}
        y2={y(70)}
        stroke={colors.down}
        strokeWidth={1}
        strokeDasharray="4 4"
        opacity={0.4}
      />
      <Line
        x1={LABEL_GUTTER}
        y1={y(30)}
        x2={width}
        y2={y(30)}
        stroke={colors.up}
        strokeWidth={1}
        strokeDasharray="4 4"
        opacity={0.4}
      />
      <SvgText
        x={LABEL_GUTTER - 6}
        y={y(70) + AXIS_FONT_SIZE / 3}
        fill={colors.textMuted}
        fontSize={AXIS_FONT_SIZE}
        textAnchor="end"
      >
        70
      </SvgText>
      <SvgText
        x={LABEL_GUTTER - 6}
        y={y(30) + AXIS_FONT_SIZE / 3}
        fill={colors.textMuted}
        fontSize={AXIS_FONT_SIZE}
        textAnchor="end"
      >
        30
      </SvgText>
      {points ? <Polyline points={points} fill="none" stroke="#22d3ee" strokeWidth={1.2} /> : null}

      {crossX != null && (
        <Line
          x1={crossX}
          y1={PAD_Y}
          x2={crossX}
          y2={PAD_Y + plotHeight}
          stroke={CROSSHAIR_COLOR}
          strokeWidth={1}
          strokeDasharray="3 3"
          opacity={0.85}
        />
      )}
    </Svg>
  );
}

function Legend({ payload }: { payload: ChartPayload }) {
  const present = OVERLAY_STYLE.filter((s) => payload.indicators[s.key]?.length);
  if (present.length === 0) return null;
  return (
    <ScrollView horizontal showsHorizontalScrollIndicator={false}>
      <View style={styles.legend}>
        {present.map((s) => (
          <View key={s.key} style={styles.legendItem}>
            <View style={[styles.legendDot, { backgroundColor: s.color }]} />
            <Text style={styles.legendText}>{s.label}</Text>
          </View>
        ))}
      </View>
    </ScrollView>
  );
}

/** Pick `count` evenly spaced indices in `[0, n - 1]` (inclusive of both ends). */
function pickXTickIndices(n: number, count: number): number[] {
  if (n <= 0) return [];
  if (n <= count) return Array.from({ length: n }, (_, i) => i);
  const result: number[] = [];
  for (let i = 0; i < count; i++) {
    result.push(Math.round((i * (n - 1)) / (count - 1)));
  }
  return result;
}

/**
 * Heckbert's "nice numbers" — round `x` to a number whose mantissa is in
 * {1, 2, 5, 10}. With `round=true` picks the *closest* nice number (for the
 * step), with `round=false` picks the *next-largest* nice number (for the
 * total range).
 */
function niceNumber(x: number, round: boolean): number {
  if (!Number.isFinite(x) || x <= 0) return 1;
  const exp = Math.floor(Math.log10(x));
  const f = x / 10 ** exp;
  let nf: number;
  if (round) {
    if (f < 1.5) nf = 1;
    else if (f < 3) nf = 2;
    else if (f < 7) nf = 5;
    else nf = 10;
  } else {
    if (f <= 1) nf = 1;
    else if (f <= 2) nf = 2;
    else if (f <= 5) nf = 5;
    else nf = 10;
  }
  return nf * 10 ** exp;
}

/**
 * "Nice numbers" ticks across `[lo, hi]`. Returns expanded `[lo, hi]` bounds
 * (snapped outward to multiples of the chosen step) plus the tick values,
 * so the caller can use the bounds as the chart's plot range and the candles
 * / overlays are guaranteed to remain inside the visible area.
 */
function niceTicks(
  lo: number,
  hi: number,
  count: number,
): { lo: number; hi: number; ticks: number[] } {
  if (!Number.isFinite(lo) || !Number.isFinite(hi) || count < 2) {
    const v = Number.isFinite(lo) ? lo : 0;
    return { lo: v, hi: v + 1, ticks: [v, v + 1] };
  }
  if (hi <= lo) {
    // Degenerate range — pad by 1% (or ±0.5 for zero) so labels still print
    // and division-by-zero is impossible downstream.
    const pad = Math.max(Math.abs(lo) * 0.01, 0.5);
    return { lo: lo - pad, hi: lo + pad, ticks: [lo - pad, lo, lo + pad] };
  }
  const range = niceNumber(hi - lo, false);
  const step = niceNumber(range / (count - 1), true);
  if (step <= 0) return { lo, hi, ticks: [lo, hi] };
  const niceLo = Math.floor(lo / step) * step;
  const niceHi = Math.ceil(hi / step) * step;
  const ticks: number[] = [];
  // Decimal places needed so toFixed cleans up float drift (log10 of step).
  const decimals = Math.max(0, -Math.floor(Math.log10(step)));
  const epsilon = step / 1000;
  for (let v = niceLo; v <= niceHi + epsilon; v += step) {
    ticks.push(Number(v.toFixed(decimals + 2)));
  }
  return { lo: niceLo, hi: niceHi, ticks };
}

/** Format a price for axis labels. Tighter formatting for larger magnitudes. */
function formatPrice(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 10000) return value.toFixed(0);
  if (abs >= 1000) return value.toFixed(0);
  if (abs >= 100) return value.toFixed(1);
  return value.toFixed(2);
}

/**
 * Compact volume formatter — e.g. `12_400_000` → `"12.4M"`, `350_000` →
 * `"350K"`, `1_200_000_000` → `"1.2B"`. Drops the decimal when it would
 * cost a digit (e.g. `99_500` → `"100K"`, not `"99.5K"`).
 */
function formatVolume(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return '0';
  const abs = Math.abs(value);
  if (abs >= 1e9) return `${(value / 1e9).toFixed(value / 1e9 >= 10 ? 0 : 1)}B`;
  if (abs >= 1e6) return `${(value / 1e6).toFixed(value / 1e6 >= 10 ? 0 : 1)}M`;
  if (abs >= 1e3) return `${(value / 1e3).toFixed(value / 1e3 >= 10 ? 0 : 1)}K`;
  return value.toFixed(0);
}

/** "YYYY-MM-DD" → "Mar 14"; falls through to the raw string on unexpected input. */
function formatDate(time: string): string {
  const parts = time.split('-');
  if (parts.length !== 3) return time;
  const monthIdx = Number.parseInt(parts[1] ?? '', 10) - 1;
  const day = Number.parseInt(parts[2] ?? '', 10);
  if (
    !Number.isFinite(monthIdx) ||
    !Number.isFinite(day) ||
    monthIdx < 0 ||
    monthIdx >= MONTH_NAMES.length
  ) {
    return time;
  }
  return `${MONTH_NAMES[monthIdx]} ${day}`;
}

/**
 * Snap `touchX` (in chart-pixel coordinates, where `plotX0` is the left edge
 * of the plot area, i.e. `LABEL_GUTTER`) to the nearest candle index, clamped
 * to `[0, n - 1]`.
 */
function nearestIndexForTouchX(
  touchX: number,
  plotX0: number,
  step: number,
  n: number,
): number {
  if (n <= 0 || step <= 0) return 0;
  const idx = Math.floor((touchX - plotX0) / step);
  if (idx < 0) return 0;
  if (idx >= n) return n - 1;
  return idx;
}

function buildA11yLabel(payload: ChartPayload): string {
  const candles = payload.candles;
  if (candles.length === 0) {
    return `${payload.symbol || 'Price'} chart, no candle data`;
  }
  let lo = Infinity;
  let hi = -Infinity;
  for (const c of candles) {
    if (c.low < lo) lo = c.low;
    if (c.high > hi) hi = c.high;
  }
  const range =
    Number.isFinite(lo) && Number.isFinite(hi)
      ? `range $${formatPrice(lo)}–$${formatPrice(hi)}`
      : 'range unavailable';
  return `${payload.symbol} chart, ${candles.length} sessions from ${payload.from} to ${payload.to}, ${range}`;
}

const styles = StyleSheet.create({
  root: { gap: spacing.sm },
  header: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: spacing.sm },
  symbol: { color: colors.textPrimary, fontSize: fontSize.sm, fontWeight: '600' },
  range: { color: colors.textMuted, fontSize: fontSize.xs, fontVariant: ['tabular-nums'] },
  readout: {
    color: colors.textPrimary,
    fontSize: fontSize.xs,
    fontVariant: ['tabular-nums'],
    flexShrink: 1,
    textAlign: 'right',
  },
  empty: { color: colors.textMuted, fontSize: fontSize.sm },
  priceStack: { gap: 2 },
  rsiLabel: {
    color: colors.textMuted,
    fontSize: fontSize.xs,
    fontWeight: '700',
    textTransform: 'uppercase',
    letterSpacing: 0.4,
  },
  legend: { flexDirection: 'row', gap: spacing.md },
  legendItem: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs },
  legendDot: { width: 8, height: 8, borderRadius: 4 },
  legendText: { color: colors.textMuted, fontSize: fontSize.xs },
});
