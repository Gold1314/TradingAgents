/**
 * Dark theme ported verbatim from the SwiftUI app (`ios/StockAgents/Utilities/
 * Theme.swift`), which in turn mirrors the web app's palette. Keeping the exact
 * hex values guarantees visual parity across SwiftUI / web / React Native.
 */
export const colors = {
  background: '#0f172a',
  surface: '#1e293b',
  surfaceMuted: '#0b1220',
  border: '#334155',
  textPrimary: '#e2e8f0',
  textMuted: '#94a3b8',
  accent: '#34d399',
  warning: '#fbbf24',
  danger: '#f43f5e',
  up: '#34d399',
  down: '#fb7185',
} as const;

export const spacing = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
} as const;

export const radius = {
  sm: 10,
  md: 12,
  lg: 14,
  xl: 16,
} as const;

/**
 * Type-scale tokens. The minimum body / label size is `sm` (13px) which keeps
 * `colors.textMuted` (#94a3b8 on #0f172a) at the WCAG AA 4.5:1 threshold.
 * Reserve `xs` (11px) for captions rendered in `colors.textPrimary` only.
 */
export const fontSize = {
  xs: 11,
  sm: 13,
  md: 14,
  lg: 16,
  xl: 20,
  display: 28,
} as const;

/** Apple HIG minimum touch target. Android's 48dp recommendation is satisfied at 1x density. */
export const minHitSize = 44;

/** Add alpha to a #rrggbb hex (parity with SwiftUI `.opacity(...)`). */
export function withAlpha(hex: string, alpha: number): string {
  const a = Math.round(Math.min(1, Math.max(0, alpha)) * 255)
    .toString(16)
    .padStart(2, '0');
  return `${hex}${a}`;
}
