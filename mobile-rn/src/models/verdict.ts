/**
 * The five-tier Portfolio Manager rating, ordered bearish → bullish. Ported from
 * `RATING_POS` / `RATING_COLOR` in `web/static/index.html` and `RATING_ORDER` in
 * `tradingagents/brokers/intents.py`.
 */
export type Verdict = 'Sell' | 'Underweight' | 'Hold' | 'Overweight' | 'Buy';

export const VERDICT_ORDER: Verdict[] = ['Sell', 'Underweight', 'Hold', 'Overweight', 'Buy'];

/** Marker position on the Sell→Buy gauge, 0–100 (`RATING_POS`). */
export const VERDICT_GAUGE_POSITION: Record<Verdict, number> = {
  Sell: 0,
  Underweight: 25,
  Hold: 50,
  Overweight: 75,
  Buy: 100,
};

/** Accent color hex per tier (`RATING_COLOR`). */
export const VERDICT_COLOR: Record<Verdict, string> = {
  Sell: '#f43f5e',
  Underweight: '#fb923c',
  Hold: '#94a3b8',
  Overweight: '#4ade80',
  Buy: '#10b981',
};

/** Sell→Buy gradient stops used by the gauge bar. */
export const VERDICT_GRADIENT = VERDICT_ORDER.map((v) => VERDICT_COLOR[v]);

/**
 * Tolerant parse of the backend `decision` string (case-insensitive). Unknown
 * values map to `null` so callers can show the raw word neutrally.
 */
export function parseVerdict(decision: string): Verdict | null {
  const trimmed = decision.trim().toLowerCase();
  return VERDICT_ORDER.find((v) => v.toLowerCase() === trimmed) ?? null;
}
