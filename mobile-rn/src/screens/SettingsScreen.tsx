import React, { useCallback, useEffect, useRef, useState } from 'react';
import { View, Text, ScrollView, StyleSheet } from 'react-native';
import * as Clipboard from 'expo-clipboard';
import Constants from 'expo-constants';
import { authService } from '../services/authService';
import { apiClient } from '../services/apiClient';
import { useAuth } from '../hooks/useAuth';
import { Card, TouchTarget } from '../components/primitives';
import { Icon } from '../components/Icon';
import { colors, fontSize, radius, spacing } from '../theme/theme';

/**
 * Settings + account screen, ported from the SwiftUI `SettingsView`.
 *
 * Layout (PR6 restructure):
 *  - Backend card with tap-to-copy base URL and an inline "Copied" confirmation.
 *  - Account card with email + sign-out grouped together (sign-out acts on the
 *    account, so it lives inside that card rather than floating at the bottom).
 *  - App version footer pinned at the end of the scroll content.
 *
 * Sign-out calls `authService.signOut()`, which notifies the auth gate in
 * `RootNavigator` to swap back to the `LoginScreen` automatically — this screen
 * does not navigate itself.
 *
 * The runtime base-URL override from the SwiftUI app is intentionally read-only
 * here (the URL is configured via `EXPO_PUBLIC_API_BASE_URL`); a writable
 * override is a deferred follow-up.
 */
export function SettingsScreen() {
  const { user } = useAuth();
  const accountLabel = user?.email ?? user?.id ?? 'Signed in';
  const baseURL = apiClient.baseURL;

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      <Section title="Backend">
        <Card>
          <Text style={styles.label}>Base URL</Text>
          <CopyableURL url={baseURL} />
        </Card>
        <Text style={styles.footer}>
          The StockAgents FastAPI server. Set via EXPO_PUBLIC_API_BASE_URL.
        </Text>
      </Section>

      <Section title="Account">
        <Card style={styles.accountCard}>
          <View style={styles.accountField}>
            <Text style={styles.label}>Signed in</Text>
            <Text style={styles.value}>{accountLabel}</Text>
          </View>
          <View style={styles.divider} />
          <TouchTarget
            onPress={() => void authService.signOut()}
            accessibilityLabel="Sign out"
            style={styles.signOut}
          >
            <Text style={styles.signOutText}>Sign Out</Text>
          </TouchTarget>
        </Card>
        <Text style={styles.footer}>
          Signs out of Supabase and clears the stored session from this device.
        </Text>
      </Section>

      <VersionFooter />
    </ScrollView>
  );
}

/**
 * Tap-to-copy row for the backend URL. Press copies the URL to the system
 * clipboard and surfaces an inline "Copied" pill for 1.5s; the trailing icon
 * swaps from `copy` to `check` for the same window to reinforce the action.
 */
function CopyableURL({ url }: { url: string }) {
  const [copied, setCopied] = useState(false);
  // Track timeout across re-renders so a rapid second press doesn't leave the
  // "Copied" state stale, and to clear on unmount.
  const resetTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (resetTimer.current) clearTimeout(resetTimer.current);
    };
  }, []);

  const onCopy = useCallback(async () => {
    try {
      await Clipboard.setStringAsync(url);
      setCopied(true);
      if (resetTimer.current) clearTimeout(resetTimer.current);
      resetTimer.current = setTimeout(() => setCopied(false), 1500);
    } catch {
      // Swallow: clipboard access can be denied on web / restricted devices;
      // there's nothing actionable to surface here.
    }
  }, [url]);

  return (
    <TouchTarget
      onPress={onCopy}
      accessibilityLabel={`Copy backend URL ${url}`}
      accessibilityHint="Copies the URL to the clipboard"
      style={styles.copyRow}
    >
      <Text style={styles.value} numberOfLines={1} ellipsizeMode="middle">
        {url}
      </Text>
      <View style={styles.copyTrailing}>
        {copied && <Text style={styles.copiedPill}>Copied</Text>}
        <Icon
          name={copied ? 'check' : 'copy'}
          size={16}
          color={copied ? colors.accent : colors.textMuted}
        />
      </View>
    </TouchTarget>
  );
}

/**
 * Small "StockAgents · v1.0.0 (build 1)" footer at the bottom of the scroll
 * surface. Reads from `expoConfig` (populated from `app.config.ts`); build
 * number is platform-specific and may be unset in dev, in which case we omit
 * the parenthetical.
 */
function VersionFooter() {
  const cfg = Constants.expoConfig;
  const appName = cfg?.name ?? 'StockAgents';
  const version = cfg?.version;
  const build = cfg?.ios?.buildNumber ?? cfg?.android?.versionCode;
  const versionPart = version ? `v${version}` : null;
  const buildPart = build != null ? `build ${build}` : null;
  const suffix = [versionPart, buildPart ? `(${buildPart})` : null].filter(Boolean).join(' ');
  const label = suffix ? `${appName} · ${suffix}` : appName;
  return (
    <Text accessibilityRole="text" style={styles.versionFooter}>
      {label}
    </Text>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>{title}</Text>
      {children}
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.background },
  content: { padding: spacing.lg, gap: spacing.xl },
  section: { gap: spacing.sm },
  sectionTitle: {
    color: colors.textMuted,
    fontSize: fontSize.sm,
    fontWeight: '700',
    textTransform: 'uppercase',
    letterSpacing: 0.4,
  },
  label: { color: colors.textPrimary, fontSize: fontSize.sm, fontWeight: '500' },
  value: { color: colors.textPrimary, fontSize: fontSize.md, marginTop: spacing.xs, flexShrink: 1 },
  footer: { color: colors.textMuted, fontSize: fontSize.xs },
  copyRow: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    justifyContent: 'space-between',
    gap: spacing.sm,
  },
  copyTrailing: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.xs,
    paddingBottom: 2,
  },
  copiedPill: {
    color: colors.accent,
    fontSize: fontSize.xs,
    fontWeight: '600',
  },
  accountCard: { gap: spacing.md },
  accountField: { gap: 0 },
  divider: { height: StyleSheet.hairlineWidth, backgroundColor: colors.border },
  signOut: {
    borderRadius: radius.md,
    paddingVertical: spacing.md,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderColor: colors.danger,
  },
  signOutText: { color: colors.danger, fontSize: fontSize.lg, fontWeight: '700' },
  versionFooter: {
    color: colors.textMuted,
    fontSize: fontSize.xs,
    textAlign: 'center',
    marginTop: spacing.sm,
  },
});
