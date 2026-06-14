import React, { useMemo } from 'react';
import Markdown from 'react-native-markdown-display';
import { colors, spacing } from '../theme/theme';

/**
 * Renders an agent's GFM report. Uses `react-native-markdown-display` (headings,
 * lists, tables, code, links), themed to the dark palette — richer than the
 * SwiftUI MVP's inline-only `AttributedString(markdown:)`.
 */
export function MarkdownBody({ source }: { source: string }) {
  const stylesheet = useMemo(
    () => ({
      body: { color: colors.textPrimary, fontSize: 14, lineHeight: 20 },
      heading1: { color: colors.textPrimary, fontSize: 18, fontWeight: '700' as const, marginTop: spacing.sm },
      heading2: { color: colors.textPrimary, fontSize: 16, fontWeight: '700' as const, marginTop: spacing.sm },
      heading3: { color: colors.textPrimary, fontSize: 15, fontWeight: '600' as const, marginTop: spacing.xs },
      strong: { color: colors.textPrimary, fontWeight: '700' as const },
      em: { color: colors.textPrimary, fontStyle: 'italic' as const },
      link: { color: colors.accent },
      bullet_list: { marginVertical: spacing.xs },
      ordered_list: { marginVertical: spacing.xs },
      code_inline: {
        color: colors.accent,
        backgroundColor: colors.surfaceMuted,
        borderRadius: 4,
        paddingHorizontal: 4,
      },
      code_block: {
        color: colors.textPrimary,
        backgroundColor: colors.surfaceMuted,
        borderColor: colors.border,
        borderWidth: 1,
        borderRadius: 8,
        padding: spacing.sm,
      },
      fence: {
        color: colors.textPrimary,
        backgroundColor: colors.surfaceMuted,
        borderColor: colors.border,
        borderWidth: 1,
        borderRadius: 8,
        padding: spacing.sm,
      },
      table: { borderColor: colors.border, borderWidth: 1, borderRadius: 6 },
      th: { padding: 6, color: colors.textPrimary },
      td: { padding: 6, color: colors.textPrimary },
      tr: { borderBottomColor: colors.border, borderBottomWidth: 1 },
      blockquote: {
        backgroundColor: colors.surfaceMuted,
        borderLeftColor: colors.border,
        borderLeftWidth: 3,
        paddingHorizontal: spacing.sm,
      },
      hr: { backgroundColor: colors.border },
    }),
    [],
  );

  // The library's typings are loose; cast the stylesheet to its expected shape.
  return <Markdown style={stylesheet as never}>{source}</Markdown>;
}
