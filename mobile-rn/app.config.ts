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
    infoPlist: {
      // Required for the voice-conversational layer (Phase 4). Without this
      // string iOS denies microphone access and the LiveKit room joins as
      // listen-only, which makes the agent appear unresponsive.
      NSMicrophoneUsageDescription:
        'StockAgents uses the microphone so you can talk to the analysis agents in real time.',
    },
  },
  android: {
    package: 'com.stockagents.mobile',
    adaptiveIcon: {
      backgroundColor: '#0f172a',
      foregroundImage: './assets/android-icon-foreground.png',
    },
    permissions: [
      // Voice-conversational layer (Phase 4). LiveKit requires explicit mic
      // + bluetooth perms on Android 12+; we keep the rest of the perms list
      // empty so Expo's defaults aren't overridden.
      'android.permission.RECORD_AUDIO',
      'android.permission.MODIFY_AUDIO_SETTINGS',
      'android.permission.BLUETOOTH_CONNECT',
    ],
  },
  web: {
    favicon: './assets/favicon.png',
  },
  plugins: [
    'expo-secure-store',
    'expo-web-browser',
    ['expo-local-authentication', { faceIDPermission: 'Authorize LIVE trades with Face ID.' }],
    // LiveKit React Native SDK Expo plugin chain. These config plugins are
    // applied during `expo prebuild` so the resulting iOS + Android projects
    // link the LiveKit WebRTC native modules. Without the dev build the
    // voice client falls back to a "voice unavailable" message and the rest
    // of the app keeps working.
    '@livekit/react-native-expo-plugin',
    '@config-plugins/react-native-webrtc',
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
