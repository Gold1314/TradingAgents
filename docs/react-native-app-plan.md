# StockAgents Mobile — React Native (Expo) Engineering Plan

> A concise, build-ready plan for a **cross-platform (iOS + Android)** React
> Native client that mirrors the existing native **SwiftUI** app (`ios/`) and
> talks to the same FastAPI backend (`web/server.py` + the gated `web/mobile/`
> layer) that drives the **TradingAgents** multi-agent pipeline.
>
> The team decided to build in **React Native instead of SwiftUI**. The SwiftUI
> app and the `web/mobile/` backend are the **behavioral reference**; where the
> backend and docs disagree, **`web/server.py` / `web/mobile/` win**.
>
> Scope of this doc: the stack decision + screen/library mapping + project
> structure + roadmap. The Foundation + MVP is scaffolded under `mobile-rn/`.

---

## 1. Stack decision

| Concern | Choice | Why |
| --- | --- | --- |
| **Runtime** | **Expo (managed) + config plugins / dev client** | Cross-platform iOS+Android from one codebase. The security-sensitive flows we need (SecureStore/Keychain+Keystore, `ASWebAuthenticationSession`-equivalent OAuth, biometrics) all have first-party Expo modules; none require ejecting. A **dev client** is only needed once we add native config-plugin modules (secure-store, local-authentication) — `expo prebuild` handles that. Bare RN buys us nothing here and costs us the Expo module ecosystem + EAS build/update. |
| **Language** | **TypeScript (strict)** | Matches the repo's exhaustive-switch discipline; the SSE union is modeled as a **discriminated union** with a `never`-checked default. |
| **Navigation** | **React Navigation** (`@react-navigation/native` + `native-stack`) | Native stack transitions, deep-link config for the `stockagents://` OAuth callback (v1), and a small, stable surface. (Expo Router was considered; a plain native stack keeps the MVP's two screens simple and matches the SwiftUI `NavigationStack`.) |
| **Server data (REST)** | **TanStack Query v5** | `GET /api/config` and `GET /api/chart` are classic cache-and-refetch reads — Query gives caching, retry, and loading/error state for free. `POST /api/runs` is a mutation. |
| **Live run state (SSE)** | **Zustand store** (`runSessionStore`) | The live agent feed is an event-driven projection, not request/response — it does **not** fit Query. A small Zustand store is the idiomatic port of the SwiftUI `RunSessionViewModel`: the SSE stream calls `applyEvent(event)` which mutates observable state (planned nodes, per-agent cards, verdict, economics). |
| **Styling / theme** | **`StyleSheet` + a typed `theme` module** | No styling lib needed for parity. A `theme.ts` reproduces the exact dark palette from `ios/.../Theme.swift` (bg `#0f172a`, surface `#1e293b`, accent `#34d399`, warning `#fbbf24`, danger `#f43f5e`, text `#e2e8f0`/`#94a3b8`). The whole app forces a dark UI. |
| **Charts** | **`react-native-svg`** (hand-rolled candlestick + overlays + RSI) | Mirrors the SwiftUI approach (Apple Charts `RuleMark`/`RectangleMark`). A custom SVG chart is dependency-light, Expo-Go-compatible, and reproduces candles + SMA/EMA/Bollinger overlays + an RSI subchart exactly from `GET /api/chart`. |
| **Markdown** | **`react-native-markdown-display`** | Renders the agents' GFM reports (headings/lists/tables/code) — richer than the SwiftUI MVP's inline-only `AttributedString(markdown:)`. Themed to the dark palette. |

### 1.1 Security-sensitive flows — library map (Swift → RN)

These are called out explicitly because they carry the real-money / credential
risk. Each preserves the SwiftUI guarantee.

| Flow | SwiftUI (reference) | React Native (Expo) | Notes / guarantee preserved |
| --- | --- | --- | --- |
| **App auth (Supabase)** | `AuthService` hitting GoTrue REST + `KeychainStore` | **`@supabase/supabase-js`** *or* direct **GoTrue REST** via `fetch` + tokens in **`expo-secure-store`** | MVP uses **direct GoTrue REST** (zero extra dep, mirrors the Swift `AuthService` exactly: `POST /auth/v1/token?grant_type=password|refresh_token`, anon key as `apikey`). Access/refresh tokens persist in SecureStore (Keychain on iOS, Keystore on Android). The bearer is injected on every `apiClient` / SSE request; refreshed on 401. `supabase-js` is an option if we later want realtime/storage. |
| **Secure token storage** | Keychain (`kSecAttrAccessibleWhenUnlockedThisDeviceOnly`) | **`expo-secure-store`** (`WHEN_UNLOCKED_THIS_DEVICE_ONLY`) | App auth token only; **never** a brokerage token (those stay server-side, §5.2 of the iOS plan). |
| **OAuth connect (broker)** | `ASWebAuthenticationSession` + `BrokerConnectService` | **`expo-web-browser` `openAuthSessionAsync`** (or `expo-auth-session`) with `callbackURLScheme: "stockagents"` | Server-mediated flow: app calls `GET /api/v2/robinhood/authorize`, opens the returned URL in the system auth session, the **server** completes the PKCE exchange and 302s to `stockagents://oauth/robinhood`, then the app polls `GET /api/v2/robinhood/status`. The phone never holds a brokerage token. (**v1 — deferred.**) |
| **Biometric LIVE-order gate** | `LocalAuthentication` (`LAContext`) | **`expo-local-authentication`** (`authenticateAsync`, biometrics → device-passcode fallback) | The "**no LIVE order without an explicit confirm AND a successful biometric**" guarantee is enforced exactly as in Swift: the biometric `await` is the only path to `submitOrder`; if it throws/cancels, the network call never runs. (**v1 — deferred.**) |
| **SSE with auth header + `Last-Event-ID` resume** | `URLSession.bytes(for:)` line reader | **`expo/fetch` streaming** (`response.body.getReader()` over a `ReadableStream<Uint8Array>`) | There is **no** native browser `EventSource` that supports custom headers in RN. `expo/fetch` (SDK 52+) gives a real streaming body **with arbitrary request headers**, so we can send `Authorization: Bearer …` + `Last-Event-ID` and read frames incrementally. Reconnect/backoff + resume are implemented in `sseClient.ts`, mirroring `RunStreamClient`. **Alternative:** `react-native-sse` (XHR-based) supports headers but reconnection/`Last-Event-ID` control is coarser — documented as the fallback. The constraint to note: neither approach is a spec `EventSource`; we own the framing + resume logic. |

---

## 2. Screen-by-screen mapping (SwiftUI → RN)

| SwiftUI screen / component | RN screen / component | API calls | Native considerations |
| --- | --- | --- | --- |
| `RunSetupView` + `RunSetupViewModel` | `RunSetupScreen` (+ `useConfig` query) | `GET /api/config`, `POST /api/runs` | Ticker/date/asset/analyst toggles + Advanced disclosure. Client validation mirrors server 400s (non-empty ticker, ≥1 analyst). `DatePicker` → `@react-native-community/datetimepicker` (or a text field for MVP). |
| `RunSessionView` + `RunSessionViewModel` | `RunSessionScreen` (+ `runSessionStore`) | `GET /api/runs/{id}` (restore), SSE `GET /api/runs/{id}/events`, `GET /api/chart` | Container: status header + stepper + verdict + economics + feed + chart. Owns the SSE subscription lifecycle; tears it down on unmount. |
| `StageStepperView` | `StageStepper` | (store) | Horizontal 5-stage progress cards (`buildStepper` parity). |
| `VerdictView` | `VerdictHero` | (store) | Big rating word + Sell→Buy gradient gauge + marker + rationale (`RATING_POS`/`RATING_COLOR`). Gradient via `react-native-svg` `LinearGradient`. |
| `AgentFeedView` / `AgentCardView` | `AgentFeed` / `AgentCard` | (store) | Expandable, markdown-rendered cards themed per agent. The price chart is hosted inside the **Market Analyst** card (web parity). |
| `PriceChartView` (Apple Charts) | `PriceChart` (`react-native-svg`) | `GET /api/chart` | Candles (wick + body), SMA/EMA/Bollinger overlays, RSI subchart with 30/70 guides. |
| `EconomicsView` | `Economics` | (store `usage`/`usage_summary`) | Totals tiles + per-agent token/cost bars; `fmtTok`/`fmtCost` parity. |
| `LoginView` / `AuthGateView` | `LoginScreen` (**stub**) / auth gate | GoTrue `POST /auth/v1/token` | MVP ships the **`authService` plumbing** (token storage + bearer injection + refresh); a full login UI is a follow-up. |
| `RobinhoodConnectView` + OAuth | `RobinhoodConnectScreen` (**v1, deferred**) | `GET /api/v2/robinhood/{authorize,callback,status}` | `expo-web-browser` auth session + status polling. |
| `OrderTicketView` + biometric | `OrderTicketScreen` (**v1, deferred**) | `POST /api/v2/runs/{run_id}/orders` | Explicit confirm + `expo-local-authentication` gate. |
| `SettingsView` | `SettingsScreen` (light, base-URL) | — | Runtime base-URL / Supabase override (persisted). MVP: base-URL only. |

---

## 3. Project structure (`mobile-rn/`)

Additive per the iOS plan **§1.6**: a brand-new top-level folder. **No changes**
to `web/`, `tradingagents/`, or `ios/`.

```
mobile-rn/
├─ app.config.ts            Expo config (scheme: stockagents, dark UI, extra env, plugins)
├─ App.tsx                  Root: SafeArea + QueryClient + NavigationContainer
├─ index.ts                 registerRootComponent
├─ .env.example             EXPO_PUBLIC_* config keys (base URL, Supabase)
├─ README.md                stack, run, config, deferred items
└─ src/
   ├─ config/env.ts         resolve base URL + Supabase from expo extra / EXPO_PUBLIC_*
   ├─ theme/theme.ts        dark palette ported from ios Theme.swift + spacing
   ├─ models/               typed API contract + the SSE event union
   │  ├─ json.ts            JsonValue (loosely-typed fields, e.g. account.position)
   │  ├─ events.ts          AgentEvent discriminated union (14 types) + parseAgentEvent
   │  ├─ run.ts             RunRequest, RunConfig, StartRunResponse, RunStatus, CachedRun
   │  ├─ chart.ts           ChartPayload
   │  ├─ verdict.ts         5-tier Verdict + gauge position/color
   │  ├─ agentNode.ts       Stage + NODE map + analyst display (ported from index.html)
   │  └─ order.ts           OrderIntent, PlaceOrderRequest, RobinhoodStatus (contract; v1)
   ├─ services/
   │  ├─ apiError.ts        typed errors (http/unauthorized/rateLimited/decoding/…)
   │  ├─ apiClient.ts       REST: config/runs/status/chart, bearer inject, 401→refresh→retry, 429
   │  ├─ sseClient.ts       SSE over expo/fetch stream + Last-Event-ID resume + backoff
   │  ├─ secureStore.ts     expo-secure-store wrapper
   │  └─ authService.ts     GoTrue sign-in/refresh/sign-out + token storage + header
   ├─ state/
   │  ├─ queryClient.ts     TanStack QueryClient
   │  ├─ runSessionStore.ts Zustand store: the live SSE → UI projection (port of handleEvent)
   │  └─ activeRunStore.ts  persist/restore the in-flight run (AsyncStorage-free; SecureStore-free)
   ├─ navigation/RootNavigator.tsx
   ├─ screens/
   │  ├─ RunSetupScreen.tsx
   │  └─ RunSessionScreen.tsx
   ├─ hooks/
   │  ├─ useConfig.ts       TanStack Query for /api/config
   │  └─ useRunController.ts wires the SSE stream + chart fetch into the store
   ├─ components/
   │  ├─ StageStepper.tsx  VerdictHero.tsx  Economics.tsx
   │  ├─ AgentFeed.tsx     AgentCard.tsx    PriceChart.tsx
   │  ├─ MarkdownBody.tsx  primitives.tsx   (Card, StatusDot, Banner)
   └─ utils/format.ts      tokens/cost/usd formatting (fmtTok/fmtCost parity)
```

### 3.1 How auth-token injection works

`authService` owns the access token (in SecureStore). `apiClient` and
`sseClient` both call `authService.getAuthHeader()` when building a request and
set `Authorization: Bearer <token>` when present. On a `401`, `apiClient`
calls `authService.attemptRefresh()` (single-flight), rebuilds the request with
the **new** bearer, and retries once; on persistent failure it signs out. The
SSE client does the same on a mid-stream 401 so a long run can outlive a token
without dropping events (it reconnects with the preserved `Last-Event-ID`).
When no token is present the header is simply omitted — today's open
`/api/*` endpoints work, and the gated `/api/v2/*` endpoints return 401.

### 3.2 How SSE works (the key constraint)

There is no `EventSource` with custom headers in React Native. `sseClient.ts`
uses **`expo/fetch`** (`import { fetch } from 'expo/fetch'`), whose `Response.body`
is a real `ReadableStream<Uint8Array>`. It:

1. Opens `GET /api/runs/{id}/events` with `Accept: text/event-stream`, the bearer
   header, and (on resume) `Last-Event-ID`.
2. Reads the stream via `body.getReader()`, decodes with `TextDecoder`, and
   splits on `\n`. It buffers `id:` / `data:` fields, ignores `:` keepalive
   comments, and dispatches a frame on a blank line.
3. Each frame is parsed into an `AgentEvent` (discriminated union) and yielded
   with its SSE id through an async generator.
4. Tracks the last id; on a dropped connection without a terminal `done`, it
   **reconnects with `Last-Event-ID`** (linear backoff, capped attempts) so the
   server resumes from `last-event-id + 1` — exactly like the browser's
   `EventSource` against `run_events()` in `web/server.py`.

---

## 4. Phased roadmap

- **MVP / v0.1 — Watch a run (this scaffold).** Run setup form + defaults from
  `/api/config`; start a run; live SSE feed across the 5 stages with token/cost
  meter; verdict gauge; price chart; 60-min cache render; session restore. Auth
  **plumbing** (token inject + refresh) is wired; login UI is a stub.
- **Auth — Supabase login UI.** A real `LoginScreen` on top of the existing
  `authService` (email/password GoTrue; Sign in with Apple/Google later).
- **v1 — Brokerage / LIVE trading.** `RobinhoodConnectScreen` (server-mediated
  OAuth via `expo-web-browser`), account grounding panel, `OrderTicketScreen`
  with explicit confirm + `expo-local-authentication` biometric gate, hitting
  the `/api/v2/*` router.
- **v2 — Polish.** Push notifications on run completion, run history/watchlist,
  share/export, iPad/tablet layout, accessibility.

---

## 5. Discrepancies vs the backend (source of truth = `web/server.py` + `web/mobile/`)

These match the SwiftUI app's findings and are **backend work to be added**, not
client bugs:

1. **Legacy `/api/*` has no auth.** `/api/runs`, `/api/runs/{id}`, `/api/chart`,
   SSE are open in `web/server.py`. The client injects a bearer *if present* and
   handles 401/refresh, but does not require sign-in against today's backend.
2. **Rate-limiting is on `/api/v2/*` only.** `web/mobile/router.py` enforces
   per-user 429 (`Retry-After`) on `POST /api/v2/runs` and orders; the legacy
   `POST /api/runs` does not. The client maps `429 + Retry-After` everywhere so it
   "just works" against either path.
3. **Two run-creation paths.** Legacy `POST /api/runs` (open, supports the 60-min
   cache + `force`) vs. `POST /api/v2/runs` (auth + quota, **no cache**, always
   `{cached:false,…}`). The MVP targets **legacy `/api/runs`** (no sign-in needed
   to demo); switching to v2 is a one-line base-path change once login ships.
4. **Cached-run shape is inferred** from what `renderCached()` consumes in
   `index.html` (`ticker, trade_date, asset_type, decision, final_content,
   identity, created_at, agents[{seq, agent, content}]`) — not a typed model in
   `server.py`.
5. **OAuth + orders live on `/api/v2/*`**, not the `POST /api/robinhood/*` paths
   the original iOS plan §3.4.2 named. v1 will target the v2 routes
   (`/api/v2/robinhood/{authorize,callback,status}`, `/api/v2/runs/{id}/orders`).
   `MOBILE_FAKE_BROKER=true` lets the trading UI be exercised without live
   Robinhood.

The MVP request/response shapes and the **entire 14-type SSE event union** match
`web/server.py`'s `manager.emit(...)` call sites exactly.
