import { useEffect, useState } from 'react';
import { authService, type AuthUser } from '../services/authService';

/**
 * Observable view of `authService` for the auth gate (iOS `AuthGateView` /
 * `AuthService.state`). Subscribes to the service's change notifications and
 * kicks off a one-time `hydrate()` so persisted tokens are loaded before the
 * gate decides between the login and main flows.
 */
export interface AuthState {
  /** True once persisted tokens have been read from SecureStore. */
  hydrated: boolean;
  isSignedIn: boolean;
  /** Whether Supabase sign-in is configured (URL + anon key present). */
  isConfigured: boolean;
  user: AuthUser | null;
}

function snapshot(): AuthState {
  return {
    hydrated: authService.isHydrated,
    isSignedIn: authService.isSignedIn,
    isConfigured: authService.isConfigured,
    user: authService.currentUser,
  };
}

export function useAuth(): AuthState {
  const [state, setState] = useState<AuthState>(snapshot);

  useEffect(() => {
    const update = () => setState(snapshot());
    // Catch any change that landed between the initial render and subscribe.
    update();
    const unsubscribe = authService.subscribe(update);
    // Idempotent: `hydrate()` returns early once loaded, and notifies on first load.
    void authService.hydrate();
    return unsubscribe;
  }, []);

  return state;
}
