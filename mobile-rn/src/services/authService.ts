import { secureStore } from './secureStore';
import { SUPABASE_URL, SUPABASE_ANON_KEY, supabaseConfigured } from '../config/env';

/**
 * App auth (multi-user backend, iOS plan §3.4.1), ported from the Swift
 * `AuthService`. It authenticates **directly against Supabase GoTrue's REST API**
 * (`POST /auth/v1/token`) with `fetch` — no `@supabase/supabase-js` dependency
 * required for the token flow (that lib is an option later for realtime/storage).
 *
 * The access token is injected as `Authorization: Bearer <token>` on every
 * `apiClient` / `sseClient` request and refreshed on a 401. Tokens persist in
 * `expo-secure-store` (Keychain / Keystore).
 *
 * The `LoginScreen` (driven by the auth gate in `RootNavigator`) calls `signIn`;
 * `SettingsScreen` calls `signOut`. When signed out, `getAuthHeader()` returns
 * null: the open legacy `/api/*` endpoints still work, but the gated `/api/v2/*`
 * endpoints (e.g. `POST /api/v2/runs`, used for run creation) return 401.
 */

const ACCESS_KEY = 'auth.accessToken';
const REFRESH_KEY = 'auth.refreshToken';
const USER_ID_KEY = 'auth.userId';
const EMAIL_KEY = 'auth.email';

export interface AuthUser {
  id: string;
  email: string | null;
}

interface GoTrueSession {
  access_token: string;
  refresh_token: string;
  user?: { id?: string; email?: string };
}

/**
 * Result of `signUp`. GoTrue's `/signup` returns either a full session (email
 * confirmation OFF) or a user object with no session (confirmation required).
 */
export type SignUpResult =
  | { status: 'session' }
  | { status: 'confirmation_required'; email: string };

type Listener = () => void;

class AuthService {
  private accessToken: string | null = null;
  private refreshToken: string | null = null;
  private user: AuthUser | null = null;
  private loaded = false;
  private refreshInFlight: Promise<boolean> | null = null;
  private listeners = new Set<Listener>();

  /** Whether Supabase sign-in is configured (URL + anon key present). */
  get isConfigured(): boolean {
    return supabaseConfigured;
  }

  get isSignedIn(): boolean {
    return this.accessToken != null;
  }

  /** Whether persisted tokens have been loaded from SecureStore yet. */
  get isHydrated(): boolean {
    return this.loaded;
  }

  get currentUser(): AuthUser | null {
    return this.user;
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private notify() {
    for (const l of this.listeners) l();
  }

  /** Load persisted tokens once on app start. */
  async hydrate(): Promise<void> {
    if (this.loaded) return;
    this.accessToken = await secureStore.get(ACCESS_KEY);
    this.refreshToken = await secureStore.get(REFRESH_KEY);
    const id = await secureStore.get(USER_ID_KEY);
    const email = await secureStore.get(EMAIL_KEY);
    this.user = id ? { id, email } : null;
    this.loaded = true;
    this.notify();
  }

  /** Header injected on every authenticated request (or null when signed out). */
  async getAuthHeader(): Promise<string | null> {
    if (!this.loaded) await this.hydrate();
    return this.accessToken ? `Bearer ${this.accessToken}` : null;
  }

  /**
   * For dev/testing without a Supabase project: inject a pre-minted bearer token
   * directly (e.g. a `MOBILE_AUTH_MODE=dev` HMAC token). Persisted like a real
   * session so `/api/v2/*` calls authenticate.
   */
  async setDevToken(token: string): Promise<void> {
    this.accessToken = token;
    await secureStore.set(ACCESS_KEY, token);
    this.notify();
  }

  /** `POST /auth/v1/token?grant_type=password`. */
  async signIn(email: string, password: string): Promise<void> {
    const session = await this.requestToken('password', { email, password });
    await this.persist(session);
  }

  /**
   * `POST /auth/v1/signup`. Creates a new account. When email confirmation is
   * disabled GoTrue returns a full session (we persist it so the auth gate
   * advances); when confirmation is required it returns a user with no session
   * (we report `confirmation_required` so the UI can prompt the user to check
   * their inbox). Additive: does not touch the sign-in flow.
   */
  async signUp(email: string, password: string): Promise<SignUpResult> {
    if (!this.isConfigured) {
      throw new Error("Sign-up isn't configured. Set EXPO_PUBLIC_SUPABASE_URL / _ANON_KEY.");
    }
    const url = `${SUPABASE_URL.replace(/\/$/, '')}/auth/v1/signup`;
    const res = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'application/json',
        apikey: SUPABASE_ANON_KEY,
      },
      body: JSON.stringify({ email, password }),
    });
    if (!res.ok) {
      let message = `Sign-up failed (HTTP ${res.status}).`;
      try {
        const data = (await res.json()) as Record<string, unknown>;
        const m = data.error_description ?? data.msg ?? data.message;
        if (typeof m === 'string' && m) message = m;
      } catch {
        // ignore parse failure
      }
      throw new Error(message);
    }
    const data = (await res.json()) as GoTrueSession;
    if (data.access_token) {
      await this.persist(data);
      return { status: 'session' };
    }
    return { status: 'confirmation_required', email };
  }

  /**
   * `POST /auth/v1/recover`. Triggers a password-reset email through Supabase
   * GoTrue (the same endpoint `supabase-js`'s `auth.resetPasswordForEmail`
   * calls). We intentionally treat the response as opaque success/failure: per
   * GoTrue's user-enumeration policy a 200 response does not confirm that the
   * email is registered, so the UI should always show a generic
   * "if an account exists…" confirmation. `redirectTo` is left to the project's
   * default Site URL — completing the reset (deep link → new password screen)
   * is a separate, deferred flow.
   */
  async resetPassword(email: string): Promise<void> {
    if (!this.isConfigured) {
      throw new Error("Password reset isn't configured. Set EXPO_PUBLIC_SUPABASE_URL / _ANON_KEY.");
    }
    const url = `${SUPABASE_URL.replace(/\/$/, '')}/auth/v1/recover`;
    const res = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'application/json',
        apikey: SUPABASE_ANON_KEY,
      },
      body: JSON.stringify({ email }),
    });
    if (!res.ok) {
      let message = `Password reset failed (HTTP ${res.status}).`;
      try {
        const data = (await res.json()) as Record<string, unknown>;
        const m = data.error_description ?? data.msg ?? data.message;
        if (typeof m === 'string' && m) message = m;
      } catch {
        // ignore parse failure
      }
      throw new Error(message);
    }
  }

  /** Exchange the stored refresh token for a fresh session (called on a 401). */
  async refresh(): Promise<void> {
    if (!this.refreshToken) throw new Error('No refresh token; please sign in again.');
    const session = await this.requestToken('refresh_token', {
      refresh_token: this.refreshToken,
    });
    await this.persist(session);
  }

  /**
   * Best-effort, single-flight refresh used by `apiClient` / `sseClient` on a
   * 401. Returns true if a fresh access token is now stored. Concurrent callers
   * share one in-flight refresh.
   */
  async attemptRefresh(): Promise<boolean> {
    if (this.refreshInFlight) return this.refreshInFlight;
    const task = (async () => {
      try {
        await this.refresh();
        return true;
      } catch {
        return false;
      } finally {
        this.refreshInFlight = null;
      }
    })();
    this.refreshInFlight = task;
    return task;
  }

  async signOut(): Promise<void> {
    const token = this.accessToken;
    this.accessToken = null;
    this.refreshToken = null;
    this.user = null;
    await Promise.all([
      secureStore.delete(ACCESS_KEY),
      secureStore.delete(REFRESH_KEY),
      secureStore.delete(USER_ID_KEY),
      secureStore.delete(EMAIL_KEY),
    ]);
    this.notify();
    // Best-effort server-side revocation; failures are ignored.
    if (token && this.isConfigured) {
      void fetch(`${SUPABASE_URL.replace(/\/$/, '')}/auth/v1/logout`, {
        method: 'POST',
        headers: { apikey: SUPABASE_ANON_KEY, Authorization: `Bearer ${token}` },
      }).catch(() => undefined);
    }
  }

  private async requestToken(
    grantType: 'password' | 'refresh_token',
    body: Record<string, string>,
  ): Promise<GoTrueSession> {
    if (!this.isConfigured) {
      throw new Error("Sign-in isn't configured. Set EXPO_PUBLIC_SUPABASE_URL / _ANON_KEY.");
    }
    const url = `${SUPABASE_URL.replace(/\/$/, '')}/auth/v1/token?grant_type=${grantType}`;
    const res = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'application/json',
        apikey: SUPABASE_ANON_KEY,
      },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      let message = `Sign-in failed (HTTP ${res.status}).`;
      try {
        const data = (await res.json()) as Record<string, unknown>;
        const m = data.error_description ?? data.msg ?? data.message;
        if (typeof m === 'string' && m) message = m;
      } catch {
        // ignore parse failure
      }
      throw new Error(message);
    }
    return (await res.json()) as GoTrueSession;
  }

  private async persist(session: GoTrueSession): Promise<void> {
    this.accessToken = session.access_token;
    this.refreshToken = session.refresh_token;
    this.user = session.user?.id
      ? { id: session.user.id, email: session.user.email ?? null }
      : this.user;
    await Promise.all([
      secureStore.set(ACCESS_KEY, session.access_token),
      secureStore.set(REFRESH_KEY, session.refresh_token),
      this.user ? secureStore.set(USER_ID_KEY, this.user.id) : Promise.resolve(),
      this.user?.email ? secureStore.set(EMAIL_KEY, this.user.email) : Promise.resolve(),
    ]);
    this.notify();
  }
}

export const authService = new AuthService();
