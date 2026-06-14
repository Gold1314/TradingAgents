import type { ExpoConfig } from 'expo/config';

/**
 * Dynamic Expo config. Public configuration is read from `EXPO_PUBLIC_*` env at
 * build time into `expo.extra` (consumed by `src/config/env.ts`) — no URLs or
 * secrets are hardcoded in source. `scheme: 'stockagents'` is the OAuth
 * bounce-back target reserved for the v1 brokerage flow.
 */
const config: ExpoConfig = {
  name: 'StockAgents',
  slug: 'stockagents-mobile',
  scheme: 'stockagents',
  version: '0.1.0',
  orientation: 'portrait',
  userInterfaceStyle: 'dark',
  backgroundColor: '#0f172a',
  icon: './assets/icon.png',
  ios: {
    supportsTablet: true,
    bundleIdentifier: 'com.stockagents.mobile',
  },
  android: {
    package: 'com.stockagents.mobile',
    adaptiveIcon: {
      backgroundColor: '#0f172a',
      foregroundImage: './assets/android-icon-foreground.png',
    },
  },
  web: {
    favicon: './assets/favicon.png',
  },
  plugins: [
    'expo-secure-store',
    'expo-web-browser',
    ['expo-local-authentication', { faceIDPermission: 'Authorize LIVE trades with Face ID.' }],
  ],
  extra: {
    apiBaseUrl: process.env.EXPO_PUBLIC_API_BASE_URL,
    supabaseUrl: process.env.EXPO_PUBLIC_SUPABASE_URL,
    supabaseAnonKey: process.env.EXPO_PUBLIC_SUPABASE_ANON_KEY,
    // EAS project id. Because this is a dynamic config, `eas init` cannot write
    // it back automatically; after running `eas init` set EAS_PROJECT_ID in your
    // shell/.env (or replace this with the literal id it prints). See EAS_BUILD.md.
    eas: {
      projectId: process.env.EAS_PROJECT_ID,
    },
  },
};

export default config;
