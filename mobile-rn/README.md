# StockAgents Mobile — React Native (Expo)

A **cross-platform (iOS + Android)** React Native client for the **StockAgents**
backend (`web/server.py`), the FastAPI layer that drives the **TradingAgents**
multi-agent pipeline. This is the React Native build the team chose over native
SwiftUI; the SwiftUI app under [`ios/`](../ios) and the gated backend under
`web/mobile/` are the behavioral reference.

This folder contains the **Foundation + MVP** scope from
[`docs/react-native-app-plan.md`](../docs/react-native-app-plan.md): a user can
configure and start a run, watch the live agent feed stream in over SSE across
the 5 stages with a token/cost meter, and see the verdict gauge + price chart.

> **Additive & isolated** (iOS plan §1.6): everything lives in this new
> `mobile-rn/` folder. No changes to `web/`, `tradingagents/`, or `ios/`.

---

## Stack

- **Expo (managed) SDK 56** + **TypeScript (strict)** — cross-platform, with
  first-party Expo modules for the security-sensitive flows (no eject needed).
- **React Navigation** (native stack) — two MVP screens; `stockagents://` scheme
  reserved for the v1 OAuth callback.
- **TanStack Query v5** — REST reads (`/api/config`, `/api/chart`) + the
  start-run mutation.
- **Zustand** — the live SSE → UI projection store (`runSessionStore`), the port
  of the SwiftUI `RunSessionViewModel`.
- **react-native-svg** — the candlestick price chart (candles + SMA/EMA/Bollinger
  + RSI), parity with the SwiftUI Apple-Charts view.
- **react-native-markdown-display** — agent report markdown.
- **expo-secure-store** — Keychain/Keystore for the app's auth token.
- **expo/fetch streaming** — SSE with auth headers + `Last-Event-ID` resume (see
  [How SSE works](#how-sse-works)).

Pinned versions live in [`package.json`](package.json) (resolved by `expo install`
for SDK 56 compatibility; verified by `expo-doctor`).

---

## Install & run

```bash
cd mobile-rn
npm install

# Start Metro / Expo dev server
npx expo start          # then press i (iOS sim), a (Android emulator), or scan in Expo Go

# Or directly:
npm run ios             # expo start --ios
npm run android         # expo start --android

# Typecheck
npm run typecheck       # tsc --noEmit
```

- **Toolchain:** Node 20.19+/22.13+/24.3+ recommended (the lockfile installs fine
  on Node 23 with an engine warning). Expo SDK 56, React Native 0.85, React 19.
- **Expo Go vs dev client:** the MVP runs in **Expo Go**. When the v1 brokerage
  flow adds `expo-local-authentication` (a config-plugin module), build a **dev
  client** (`npx expo prebuild` + `npx expo run:ios|run:android`).

> Note: this scaffold was authored in an environment without an iOS/Android
> simulator, so it has **not been launched on a device/simulator**. It **passes
> `tsc --noEmit` (strict) and all 21 `expo-doctor` checks**, and the Expo config
> resolves — but expect to fix minor runtime issues on first launch.

---

## Configure the backend base URL + Supabase

No URLs or secrets are hardcoded. Copy [`.env.example`](.env.example) to `.env`
and set the `EXPO_PUBLIC_*` values; `app.config.ts` reads them into `expo.extra`
and `src/config/env.ts` resolves them (with a localhost fallback).

| Env var | Purpose | Default |
| --- | --- | --- |
| `EXPO_PUBLIC_API_BASE_URL` | Backend base URL (`web/server.py`). | `http://localhost:8000` |
| `EXPO_PUBLIC_SUPABASE_URL` | Supabase project URL — **required for sign-in** (same value the backend uses). | _(blank)_ |
| `EXPO_PUBLIC_SUPABASE_ANON_KEY` | Supabase **anon/publishable** key (safe in a client; never the service-role key) — **required for sign-in**. | _(blank)_ |

> Without the two Supabase values the login screen shows a "Supabase isn't
> configured" message and runs cannot start (run creation now requires auth).

- **iOS simulator** reaches the host's `http://localhost:8000` directly.
- **Android emulator** reaches the host machine via `http://10.0.2.2:8000`.
- **Production** must be HTTPS.

Run the backend locally with `uvicorn web.server:app --reload --port 8000`.

---

## How SSE works

React Native has **no `EventSource` that supports custom request headers**, so
`src/services/sseClient.ts` is built on **`expo/fetch`** streaming
(`import { fetch } from 'expo/fetch'`), whose `Response.body` is a real
`ReadableStream<Uint8Array>`. This is the key constraint that drove the library
choice (`react-native-sse` is a documented XHR-based fallback, but its
reconnection / `Last-Event-ID` control is coarser).

1. Opens `GET /api/runs/{id}/events` with `Accept: text/event-stream`, the bearer
   header (when signed in), and — on a resume — the `Last-Event-ID` header.
2. Reads the stream via `body.getReader()`, decodes UTF-8, splits on `\n`,
   buffers `id:` / `data:` fields, and ignores `:` keepalive comments. A blank
   line dispatches the frame.
3. Each frame is parsed into an `AgentEvent` (a **discriminated union** with a
   `never`-checked default and an `unknown` fallback — `src/models/events.ts`)
   and yielded with its SSE id through an async generator.
4. Tracks the last id; on a dropped connection without a terminal `done`, it
   **reconnects with `Last-Event-ID`** (linear backoff, capped attempts) so the
   server resumes from `last-event-id + 1`. A mid-stream `401` triggers a
   single-flight token refresh + immediate reconnect.

`src/state/runSessionStore.ts` consumes the stream and projects each event into
observable state (planned nodes, per-agent cards, verdict, economics) — the port
of `handleEvent()` in `index.html` / the Swift `RunSessionViewModel`.

## Login & auth gate

The app is gated by Supabase Auth (iOS plan §3.4.1), mirroring the SwiftUI
`AuthGateView`:

- **`RootNavigator`** (`src/navigation/RootNavigator.tsx`) is the auth gate. It
  reads `useAuth()` (`src/hooks/useAuth.ts`), which subscribes to `authService`
  and hydrates persisted tokens. While tokens load it shows a splash; when
  **signed out** it renders only the **`LoginScreen`**; when **signed in** it
  renders the run + settings screens. A successful sign-in or a forced sign-out
  (refresh failure) flips the gate automatically — no manual navigation.
- **`LoginScreen`** (`src/screens/LoginScreen.tsx`): email/password, dark theme,
  loading/error states, and a "Supabase isn't configured" message when the env
  vars are missing. Wired to `authService.signIn`.
- **`SettingsScreen`** (`src/screens/SettingsScreen.tsx`, opened via the ⚙︎
  header button on the run setup screen): shows the backend URL + signed-in
  account and provides **Sign Out** (`authService.signOut`), mirroring the iOS
  Settings sign-out.

### How auth-token injection works

`src/services/authService.ts` owns the app's access token (in `expo-secure-store`)
and authenticates directly against Supabase GoTrue (`POST /auth/v1/token`).
`apiClient` and `sseClient` both call `authService.getAuthHeader()` and set
`Authorization: Bearer <token>` when present; on a `401`, `apiClient` does a
single-flight refresh, rebuilds the request with the new bearer, and retries
once (if the refresh fails, the session is cleared and the gate returns to
login). The open legacy reads (`/api/config`, `/api/chart`, `/api/runs/{id}`,
`/api/runs/{id}/events`) work with or without a token; the gated
`POST /api/v2/runs` requires one. For dev against a backend in
`MOBILE_AUTH_MODE=dev`, you can inject a pre-minted token via
`authService.setDevToken(...)`.

---

## What's implemented (MVP)

- **Run form** (`RunSetupScreen`): ticker, trade date, asset type, analyst
  multi-select, Advanced disclosure. Defaults from `GET /api/config`;
  client-side validation matches the server's 400s.
- **Auth gate + login**: Supabase email/password sign-in (`LoginScreen`), an
  auth gate in `RootNavigator` that swaps between login and the main app, and a
  sign-out affordance in `SettingsScreen` (⚙︎ header button).
- **Start run (authenticated)**: `POST /api/v2/runs` (requires sign-in) → streams
  a fresh run; 401 transparently refreshes and 429 surfaces a friendly
  "limit reached" message with the server's `Retry-After`.
- **Live run screen** (`RunSessionScreen`): status header + elapsed timer,
  5-stage stepper, the streaming agent feed (expandable markdown cards themed per
  agent), the verdict hero with the Sell→Buy gauge + PM rationale, the token/cost
  economics meter, and the price chart (candles + SMA/EMA/Bollinger + RSI) hosted
  in the Market Analyst card.
- **Session restore**: the in-flight run handle is persisted; on the setup screen
  a "Resume in-flight run" card reconnects the SSE stream (full replay).
- **Auth plumbing**: bearer injection + 401 refresh on both REST and SSE; `429 +
  Retry-After` mapped to a friendly error.

## Project layout

See [`docs/react-native-app-plan.md` §3](../docs/react-native-app-plan.md) for
the full tree. Briefly: `src/models` (typed contract + SSE union),
`src/services` (apiClient / sseClient / authService / secureStore),
`src/state` (Zustand run store + query client + active-run persistence),
`src/components` + `src/screens` (dark-themed UI), `src/hooks`, `src/theme`.

---

## Deferred / stubbed (follow-up milestones)

Clearly called out so nobody mistakes a stub for a finished feature:

- **Sign in with Apple / OAuth providers** — only Supabase email/password is
  wired today; social/Apple sign-in is a follow-up.
- **Brokerage / OAuth connect** (`expo-web-browser` `openAuthSessionAsync` against
  `GET /api/v2/robinhood/{authorize,callback,status}`) — **not built** (v1).
  `OrderIntent` / `RobinhoodStatus` are modeled for contract completeness, and
  `apiClient.submitOrder` exists, but no connect/order screens.
- **Biometric LIVE-order gate** (`expo-local-authentication`) — **not built** (v1).
  The "no LIVE order without explicit confirm AND a successful biometric"
  guarantee will be enforced exactly as in the SwiftUI app.
- **Account grounding panel** — the `account` SSE event is decoded into the store
  but not surfaced as its own panel.
- **DatePicker** — the trade date is a text field for the MVP; swap in
  `@react-native-community/datetimepicker` for a native picker.
- **Tests** — no Jest suite yet; the SSE-union parser and model mappers (the
  parts most worth locking down) are the first candidates (`jest-expo`).
- **v2** — push notifications, run history/watchlist, share/export, Settings
  (runtime base-URL override), iPad/tablet layout, accessibility.

## Discrepancies vs the backend

Where the plan and `web/server.py` / `web/mobile/` disagree, the **backend wins**.
The full list is in [`docs/react-native-app-plan.md` §5](../docs/react-native-app-plan.md);
the headline ones: the legacy reads + SSE (`/api/config`, `/api/chart`,
`/api/runs/{id}`, `/api/runs/{id}/events`) have **no auth**, while rate limiting +
per-user broker isolation + OAuth + order placement live on the gated `/api/v2/*`
router. **Run creation uses the authenticated `POST /api/v2/runs`** (so sign-in is
required), but the run it creates is still served by the legacy status + SSE
endpoints, since v2 reuses the same run machinery. Note `POST /api/v2/runs` never
returns a 60-minute cache hit (it always starts a fresh run), unlike the legacy
`POST /api/runs`. The full 14-type SSE event union matches `server.py`'s
`manager.emit(...)` exactly.
