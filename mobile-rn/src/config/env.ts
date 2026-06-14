import Constants from 'expo-constants';

/**
 * Runtime configuration. No URLs or secrets are hardcoded in source — they
 * resolve from `EXPO_PUBLIC_*` env (read at build time into `expo.extra` via
 * app.config.ts) with safe local-dev fallbacks.
 *
 * Priority: a value set in `expo.extra` (from `EXPO_PUBLIC_*`) wins; otherwise
 * the documented dev fallback is used.
 */
type Extra = {
  apiBaseUrl?: string;
  supabaseUrl?: string;
  supabaseAnonKey?: string;
};

const extra = (Constants.expoConfig?.extra ?? {}) as Extra;

/** Backend base URL, e.g. http://localhost:8000 (dev) or the deployed HTTPS host. */
export const DEFAULT_API_BASE_URL =
  extra.apiBaseUrl?.trim() || 'http://localhost:8000';

/** Supabase project URL (same value the backend uses in web/db.py). */
export const SUPABASE_URL = extra.supabaseUrl?.trim() || '';

/** Supabase anon / publishable key — safe to ship in a client; never the service-role key. */
export const SUPABASE_ANON_KEY = extra.supabaseAnonKey?.trim() || '';

/** True when a Supabase project URL + anon key are configured for sign-in. */
export const supabaseConfigured = Boolean(SUPABASE_URL && SUPABASE_ANON_KEY);
