/**
 * Response of `GET /api/chart` (`build_chart_payload` in `web/charts.py`).
 * Candles + volume + indicator series, shaped for the price chart in the Market
 * Analyst card. `time` strings are "YYYY-MM-DD".
 */
export interface Candle {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
}

export interface VolumeBar {
  time: string;
  value: number;
  color: string | null;
}

export interface IndicatorPoint {
  time: string;
  value: number;
}

export interface ChartPayload {
  symbol: string;
  from: string;
  to: string;
  candles: Candle[];
  volume: VolumeBar[];
  /** Indicator name (e.g. "close_50_sma", "boll_ub", "rsi") -> points. */
  indicators: Record<string, IndicatorPoint[]>;
}

/** Indicator keys we overlay on the candle chart (subset of CHART_INDICATORS). */
export const OVERLAY_INDICATORS = [
  'close_50_sma',
  'close_200_sma',
  'close_10_ema',
  'boll_ub',
  'boll_lb',
] as const;

/** Indicator shown in the RSI subchart. */
export const RSI_INDICATOR = 'rsi';

/** Human labels + colors for the overlay indicator keys (parity with SwiftUI). */
export const OVERLAY_STYLE: { key: string; label: string; color: string }[] = [
  { key: 'close_50_sma', label: 'SMA 50', color: '#38bdf8' },
  { key: 'close_200_sma', label: 'SMA 200', color: '#a78bfa' },
  { key: 'close_10_ema', label: 'EMA 10', color: '#fbbf24' },
  { key: 'boll_ub', label: 'Boll Upper', color: '#94a3b8' },
  { key: 'boll_lb', label: 'Boll Lower', color: '#94a3b8' },
];

function num(v: unknown, fallback = 0): number {
  return typeof v === 'number' && Number.isFinite(v) ? v : fallback;
}
function str(v: unknown, fallback = ''): string {
  return typeof v === 'string' ? v : fallback;
}

/** Parse the chart payload defensively from raw JSON. */
export function parseChartPayload(input: unknown): ChartPayload {
  const raw = (input && typeof input === 'object' ? input : {}) as Record<string, unknown>;
  const candles: Candle[] = Array.isArray(raw.candles)
    ? raw.candles.map((c) => {
        const o = (c ?? {}) as Record<string, unknown>;
        return {
          time: str(o.time),
          open: num(o.open),
          high: num(o.high),
          low: num(o.low),
          close: num(o.close),
        };
      })
    : [];
  const volume: VolumeBar[] = Array.isArray(raw.volume)
    ? raw.volume.map((v) => {
        const o = (v ?? {}) as Record<string, unknown>;
        return { time: str(o.time), value: num(o.value), color: typeof o.color === 'string' ? o.color : null };
      })
    : [];
  const indicators: Record<string, IndicatorPoint[]> = {};
  const rawInd = (raw.indicators && typeof raw.indicators === 'object' ? raw.indicators : {}) as Record<
    string,
    unknown
  >;
  for (const [key, series] of Object.entries(rawInd)) {
    if (!Array.isArray(series)) continue;
    indicators[key] = series.map((p) => {
      const o = (p ?? {}) as Record<string, unknown>;
      return { time: str(o.time), value: num(o.value) };
    });
  }
  return {
    symbol: str(raw.symbol),
    from: str(raw.from),
    to: str(raw.to),
    candles,
    volume,
    indicators,
  };
}

export function isCandleUp(candle: Candle): boolean {
  return candle.close >= candle.open;
}
