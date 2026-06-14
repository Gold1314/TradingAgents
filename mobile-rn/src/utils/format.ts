/**
 * Number/token/cost formatting, mirroring `fmtTok` / `fmtCost` / `fmtUsd` in
 * `web/static/index.html` and the Swift `Formatters`.
 */

/** Compact token count: 1234 -> "1.2K", 1_500_000 -> "1.5M". */
export function formatTokens(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return String(value);
}

/** Estimated cost in USD, or "—" when the model is unpriced (cost == null). */
export function formatCost(value: number | null | undefined): string {
  if (value == null) return '—';
  if (value < 0.01) return `$${value.toFixed(4)}`;
  return `$${value.toFixed(2)}`;
}

/** Whole-dollar / 2dp currency for account + order amounts. */
export function formatUsd(value: number | null | undefined): string {
  if (value == null) return '—';
  return `$${value.toFixed(2)}`;
}

/** Format a Date as YYYY-MM-DD (UTC) — the wire format the backend expects. */
export function toTradeDate(date: Date): string {
  const y = date.getUTCFullYear();
  const m = String(date.getUTCMonth() + 1).padStart(2, '0');
  const d = String(date.getUTCDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}
