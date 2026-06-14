# StockAgents — iOS app

A native SwiftUI client for the **StockAgents** backend (`web/server.py`), the
FastAPI layer that drives the **TradingAgents** multi-agent pipeline. This folder
contains the **Foundations + MVP** scope from
[`docs/ios-app-plan.md`](../docs/ios-app-plan.md): a user can configure and start
a run, watch the live agent feed stream in over SSE, and see the final verdict +
price chart — **plus** the v1 brokerage layer: Robinhood connect over
server-mediated OAuth and a biometric-gated LIVE order ticket.

> **Brokerage / live-trading (v1) is now built** against the additive
> `/api/v2/*` backend (`web/mobile/`). The phone never holds a brokerage token —
> it only drives the OAuth sheet and polls connection status, and every LIVE
> order is gated behind explicit confirmation **and** Face ID / Touch ID. See
> [Brokerage — connect + LIVE orders](#brokerage--connect--live-orders-v1) and
> the backend TODOs in [Deferred / stubbed](#deferred--stubbed). The legacy
> auth/login UI remains a stub.

---

## Project setup — why XcodeGen

The project is defined declaratively in **[`project.yml`](project.yml)** and
generated with [**XcodeGen**](https://github.com/yonaskolb/XcodeGen). A
hand-maintained `.pbxproj` is fragile and merge-hostile; regenerating it from a
plain-text spec keeps the repo reviewable and avoids binary-ish project files in
version control. (A Swift Package alone can't host an iOS **app** target with an
Info.plist/entitlements, hence an app target via XcodeGen rather than SPM-only.)

### Generate & open

```bash
brew install xcodegen          # one-time
cd ios
xcodegen generate             # writes StockAgents.xcodeproj from project.yml
open StockAgents.xcodeproj
```

Then pick the **StockAgents** scheme and run on an iOS 16+ simulator.

- **Toolchain:** Xcode 15+, Swift 5.9, deployment target **iOS 16.0**.
- **Dependencies:** none. Networking is `URLSession` (+ `URLSession.bytes` for
  SSE), charts use Apple's first-party **Charts** framework, markdown uses
  Foundation `AttributedString(markdown:)`. No SPM packages to resolve.

> Note: this scaffold was authored without a macOS/Xcode toolchain available, so
> it has **not been compiled**. The code is written to be compile-ready and the
> models/parsers are unit-tested, but expect to fix minor issues on first build.

---

## Configuring the backend base URL

No URLs or secrets are hardcoded in source. The base URL resolves in priority
order:

1. **Runtime override** — set in-app via the **Settings** (gear) screen; persisted
   in `UserDefaults`.
2. **Build-time default** — the `STOCKAGENTS_BASE_URL` key in
   `StockAgents/Resources/Info.plist` (also declared in `project.yml`). Defaults
   to `http://localhost:8000`.
3. Fallback `http://localhost:8000`.

**Local dev:** run the backend with `uvicorn web.server:app --reload --port 8000`
and point the app at `http://localhost:8000`. The Info.plist enables
`NSAllowsLocalNetworking` so the simulator can reach plain-HTTP localhost.
**Production must be HTTPS** — remove/replace the ATS exception for release.

---

## Configuring Supabase sign-in

The app signs in directly against **Supabase Auth (GoTrue)** over its REST API
(`POST {SUPABASE_URL}/auth/v1/token`) using `URLSession` — **no `supabase-swift`
SPM package**, consistent with the zero-dependency rule. The access token it
gets back is sent as `Authorization: Bearer <token>` on every `/api/v2/*` call;
the backend validates it when `MOBILE_AUTH_MODE=supabase` (`web/mobile/auth.py`).

**No secrets are hardcoded.** Two values are required and resolve in priority
order (see `Services/SupabaseConfig.swift`):

1. **Runtime override** — `UserDefaults` keys `stockagents.supabase.url` /
   `stockagents.supabase.anonKey` (handy for dev/staging swaps).
2. **Build-time default** — the `SUPABASE_URL` / `SUPABASE_ANON_KEY` keys in
   `StockAgents/Resources/Info.plist` (declared in `project.yml`). Both ship
   **blank**; set them per build.

Set them by editing `project.yml` locally, or better, wire an xcconfig
(`SUPABASE_URL = $(SUPABASE_URL)` etc.) so the values live outside source:

- `SUPABASE_URL` — your project base URL, e.g. `https://<project-ref>.supabase.co`
  (the same value the backend uses in `web/db.py`).
- `SUPABASE_ANON_KEY` — the project **anon / publishable** key (Supabase
  dashboard → Project → Settings → API). This key is *publishable* and safe to
  ship in a client; it is **not** the service-role key. The backend's
  `SUPABASE_KEY` (service role) is **never** used by the app.

When either value is blank the login screen shows a "sign-in isn't configured"
message instead of attempting a doomed request.

> Backend side: the matching backend must run with `MOBILE_API_ENABLED=true`,
> `MOBILE_AUTH_MODE=supabase`, and the Supabase env (`SUPABASE_URL` +
> `SUPABASE_JWT_SECRET` or JWKS) per `.env.example`, so it can verify the token
> the app sends.

### How sign-in / refresh / sign-out work

- **Sign in** (`AuthService.signIn`) → `POST /auth/v1/token?grant_type=password`
  with the anon key as the `apikey` header; the access + refresh tokens, expiry,
  and user id/email are persisted in the **Keychain**
  (`kSecAttrAccessibleWhenUnlockedThisDeviceOnly`).
- **Refresh** (`AuthService.refresh`) → `POST /auth/v1/token?grant_type=refresh_token`.
  `APIClient` calls it automatically on a `401`: it refreshes **once**, rebuilds
  the request with the new bearer, and retries. Concurrent 401s share a single
  coalesced refresh. If the refresh fails the session is cleared and the app
  returns to the login screen.
- **Sign out** (`AuthService.signOut`, from Settings) clears the Keychain and
  best-effort calls `POST /auth/v1/logout` to revoke the refresh token.

Login is surfaced by `AuthGateView`: signed-out → `LoginView`, signed-in → the
main app. Sign-out lives in **Settings**.

---

## Architecture

MVVM + a thin service layer (plan §3.2), iOS 16+, `async/await` throughout.

```
ios/
├─ project.yml                     XcodeGen spec (generates the .xcodeproj)
├─ StockAgents/
│  ├─ App/
│  │  ├─ StockAgentsApp.swift       @main
│  │  └─ AppEnvironment.swift       DI container + base-URL config + VM factories
│  ├─ Models/                       Codable API contract
│  │  ├─ RunConfig.swift            GET /api/config (+ RobinhoodPublicStatus)
│  │  ├─ RunRequest.swift           POST /api/runs body (+ AssetType)
│  │  ├─ RunResponse.swift          StartRunResponse (started|cached), RunStatus
│  │  ├─ AgentEvent.swift           the full SSE event union (14 types)
│  │  ├─ AgentNode.swift            NODE/STAGES metadata + live card state
│  │  ├─ Verdict.swift              5-tier rating (RATING_POS/RATING_COLOR)
│  │  ├─ ChartPayload.swift         GET /api/chart
│  │  ├─ OrderIntent.swift          trade intent + PlaceOrderRequest (order ticket)
│  │  ├─ RobinhoodStatus.swift      v2 authorize-response + per-user broker status
│  │  ├─ AuthModels.swift           AuthState/AuthUser + Supabase session/error
│  │  └─ JSONValue.swift            type-erased value for loosely-typed fields
│  ├─ Services/
│  │  ├─ APIClient.swift            REST: config/runs/status/chart, 401→refresh→retry
│  │  ├─ APIError.swift             typed errors incl. 401 + 429 (rate limit)
│  │  ├─ RunStreamClient.swift      SSE over URLSession.bytes + Last-Event-ID
│  │  ├─ AuthService.swift          Supabase GoTrue sign-in/refresh/sign-out
│  │  ├─ SupabaseConfig.swift       Supabase URL + anon key resolution (no secrets in src)
│  │  ├─ KeychainStore.swift        this-device-only secret storage
│  │  ├─ ActiveRunStore.swift       persist/restore in-flight run on relaunch
│  │  ├─ BrokerConnectService.swift ASWebAuthenticationSession OAuth launcher
│  │  └─ BiometricAuthenticator.swift LocalAuthentication LIVE-order gate
│  ├─ ViewModels/
│  │  ├─ RunSetupViewModel.swift    form defaults + validation + start
│  │  ├─ RunSessionViewModel.swift  the live SSE → UI projection (port of handleEvent)
│  │  ├─ BrokerConnectViewModel.swift connect-flow state + status polling
│  │  ├─ LoginViewModel.swift       Supabase sign-in input + loading/error state
│  │  └─ OrderTicketViewModel.swift order edits + confirm + biometric gate
│  ├─ Views/
│  │  ├─ AuthGateView.swift         signed-out → LoginView, signed-in → RootView
│  │  ├─ LoginView.swift            email/password Supabase sign-in screen
│  │  ├─ RootView.swift             shell, routing, session restore, Connect entry
│  │  ├─ RunSetupView.swift         the run form (ticker/date/asset/analysts/advanced)
│  │  ├─ RunSessionView.swift       container: status + stepper + verdict + economics + feed + order entry
│  │  ├─ StageStepperView.swift     5-stage progress
│  │  ├─ VerdictView.swift          rating word + Sell→Buy gauge + rationale
│  │  ├─ AgentFeedView.swift        agent cards (markdown) + chart host
│  │  ├─ PriceChartView.swift       Apple Charts candles + SMA/Bollinger + RSI
│  │  ├─ EconomicsView.swift        token/cost meter
│  │  ├─ RobinhoodConnectView.swift connect screen (connecting/connected/failed)
│  │  ├─ OrderTicketView.swift      LIVE order ticket + biometric confirm sheet
│  │  └─ SettingsView.swift         base-URL config
│  ├─ Utilities/
│  │  ├─ Theme.swift                Color(hex:) + palette ported from the web app
│  │  ├─ Formatters.swift           token/cost/usd formatting
│  │  └─ Markdown.swift             AttributedString markdown rendering
│  └─ Resources/
│     ├─ Info.plist
│     └─ Assets.xcassets
└─ StockAgentsTests/                event decoding, SSE parsing, model mapping
```

### How the SSE streaming works

`RunStreamClient` (`Services/RunStreamClient.swift`) is hand-rolled on
`URLSession.bytes(for:)` — there is no first-party SwiftUI SSE API.

1. It opens `GET /api/runs/{run_id}/events` with `Accept: text/event-stream` and,
   on a resume, the `Last-Event-ID` header (and the auth bearer header when
   present).
2. It reads the async byte stream **line-by-line** (`bytes.lines`), buffering
   `id:` and `data:` fields. A **blank line** dispatches the buffered frame; lines
   starting with `:` (the server's `: keepalive` every 15s) are ignored.
3. Each `data:` frame is decoded into an `AgentEvent` and yielded — together with
   its SSE `id` — through an `AsyncThrowingStream`.
4. The last seen `id` is tracked; if the connection drops without a terminal
   `done`, the client **reconnects with `Last-Event-ID`** (linear backoff, capped
   attempts), so the server resumes from `last-event-id + 1` exactly like the
   browser's `EventSource` (`run_events()` in `web/server.py`).
5. **Mid-stream token refresh.** A long run can outlive the access token. When a
   (re)connect is rejected with `401`, `RunStreamClient` calls
   `AuthService.attemptRefresh()` (the same single-flight refresh `APIClient`
   uses) and reconnects **immediately** (no backoff) with the new bearer **and**
   the preserved `Last-Event-ID`, so no events are dropped. Consecutive
   refresh+reconnect cycles are capped (reset on any successful connection); if
   the refresh fails — or the fresh token keeps being rejected — the client
   signs out and finishes the stream with an auth error, matching `APIClient`'s
   signed-out behavior. `RunStreamClient` already receives the `AuthService` via
   its initializer (wired by `AppEnvironment`), the same way `APIClient` does.

`RunSessionViewModel` consumes that stream on the main actor and projects each
event into observable state (`plannedNodes`, `agents`, `verdict`, `usageByNode`,
`activeNode`/`completedNodes`, `account`, `lastTrade`) — the Swift port of
`handleEvent()` in `index.html`. The decode uses an **exhaustive switch** over the
event-type enum (`AgentEvent.init(from:)`), so a newly added server event type
forces a compile-time decision here. An unknown `type` decodes to `.unknown` so
the stream never dies on a benign new event.

Session restore mirrors the web app's `tryRestoreActiveRun`: the active run
`{run_id, request, nodes}` is persisted in `UserDefaults` (`ActiveRunStore`); on
launch the app calls `GET /api/runs/{id}` and, if the run still exists,
reconnects the stream (a fresh connection replays from the start).

---

## What's implemented (MVP)

- **Run form** mirroring the web inputs: ticker, trade date, asset type, analyst
  multi-select, and the Advanced disclosure (provider, deep/quick model, debate &
  risk rounds). Defaults from `GET /api/config`; client-side validation matches
  the server's 400s (non-empty ticker, ≥1 analyst).
- **Start run / 60-minute cache**: `POST /api/runs` → either streams a fresh run
  or renders a cache hit inline with a banner.
- **Live run screen**: status header + elapsed timer, 5-stage stepper with
  per-stage progress, the streaming agent feed (expandable, markdown-rendered
  cards themed per agent), the verdict hero with the Sell→Buy gauge + PM
  rationale, the token/cost economics meter, and the price chart (Apple Charts:
  candles + SMA/EMA/Bollinger overlays + RSI subchart) hosted in the Market
  Analyst card.
- **Supabase sign-in (plan §3.4.1)**: `LoginView` + `AuthService` authenticate
  against Supabase Auth (GoTrue) over REST; tokens persist in `KeychainStore` and
  are injected as `Authorization: Bearer` on every `APIClient` /
  `RunStreamClient` request. `APIClient` refreshes the token on a `401` and
  retries once (falling back to signed-out), and maps `429` (rate-limit) to a
  friendly error (plan §3.4.1/§3.4.1a). `RunStreamClient` does the same for
  long-lived SSE streams: on a mid-run `401` it refreshes and reconnects with the
  preserved `Last-Event-ID` (see [How the SSE streaming works](#how-the-sse-streaming-works)).
  Sign-out lives in Settings. See
  [Configuring Supabase sign-in](#configuring-supabase-sign-in).

---

## Brokerage — connect + LIVE orders (v1)

The v1 brokerage layer integrates with the additive `/api/v2/*` mobile router
(`web/mobile/`), which is gated behind `MOBILE_API_ENABLED` and requires a bearer
token (`current_user`). It is **additive and iOS-only** — no web/backend code was
changed (plan §1.6).

### Robinhood connect — server-mediated OAuth (plan §5.1.3)

Entry point: the **building** icon in the top-left of the run-setup screen opens
`RobinhoodConnectView` as a sheet. The flow (`BrokerConnectViewModel` +
`BrokerConnectService`):

1. `GET /api/v2/robinhood/authorize` → `{authorization_url, connect_session}`.
   The server builds the provider with a **remote-HTTPS** redirect it owns and
   returns just the authorization URL (no server browser is opened).
2. The app opens that URL in an **`ASWebAuthenticationSession`** with
   `callbackURLScheme: "stockagents"`. The user authorizes inside the system
   browser; the brokerage session cookie never reaches app code.
3. Robinhood redirects to the **server's** `/api/v2/robinhood/callback`, which
   completes the PKCE exchange server-side and 302s to the app deep link
   `stockagents://oauth/robinhood?status=ok`. `ASWebAuthenticationSession`
   matches the scheme and hands that URL back to the app (so the deep link is
   consumed by the session callback — no separate `onOpenURL` is required).
4. The app then **polls `GET /api/v2/robinhood/status`** (~1.5s × up to 20) until
   `connected: true`. The server is authoritative, so the connection resolves
   even if the app-scheme bounce is flaky (plan §5.1.3 step 5). States surface as
   connecting / connected / failed.

**The phone never sees a brokerage token.** It only launches the auth URL and
reads the non-secret status payload (`RobinhoodStatus`); tokens live server-side,
keyed per user (plan §5.2).

### Biometric-gated LIVE order ticket (plan §5.3, decision 4)

When a finished run proposes an actionable buy/sell (`trade` SSE event with an
actionable `OrderIntent`), `RunSessionView` shows a **Review order** entry that
opens `OrderTicketView`. LIVE vs. simulated is driven by the trade event's
`can_place_real_orders`/`dry_run` (a real-money order requires
`can_place_real_orders && !dry_run`).

The two-factor gate is enforced in `OrderTicketViewModel.confirmAndSubmit()`,
which is the **only** path to `APIClient.submitOrder(...)`:

1. **Explicit confirmation** — a deliberate confirmation dialog summarizing
   side / ticker / size / type, labeled "**LIVE — real money**" (red) or
   "Simulated" (amber). Reaching `confirmAndSubmit()` *is* the explicit confirm.
2. **Biometric evaluation** — for a LIVE order, `BiometricAuthenticator.evaluate`
   runs a `LocalAuthentication` check (`.deviceOwnerAuthenticationWithBiometrics`,
   falling back to `.deviceOwnerAuthentication` / passcode) that must **succeed
   `try await`** before the network call. If it throws (cancel / fail /
   unavailable) the code never reaches `submitOrder`, so a LIVE order **cannot**
   be placed without passing Face ID / Touch ID.

If the device has no biometrics *and* no passcode, the confirm action is disabled
for LIVE orders (`liveBlockedByMissingBiometrics`) — the gate is never skipped.
Dry-run/simulated placements still show the confirmation summary but skip the
biometric (plan §5.3). The client sends only ticket *edits*
(`action/quantity/notional/order_type`); the server re-clamps and forces the
ticker — client limits are never authoritative.

**Submission error handling.** `POST /api/v2/runs/{run_id}/orders`
(`web/mobile/orders.py`) maps distinct failures onto HTTP statuses, and
`OrderTicketViewModel` classifies them (via the status carried by `APIError`)
into a typed `SubmissionFailure` so `OrderTicketView` can show an actionable,
recoverable state instead of a generic error:

- **`409` "Robinhood not connected."** → a distinct *connect* prompt with a
  **Connect Robinhood** button that opens `RobinhoodConnectView` inline (the VM
  factory is passed into `OrderTicketView`). Other `409`s (execution off, run has
  no ticker) surface the server's reason verbatim.
- **`404`** (run unknown / expired / not owned — the server deliberately doesn't
  distinguish "not owned" to avoid leaking existence) → a calm "this run is no
  longer available, start a new run" message, not a crash-style error.
- **`429`** (per-user order quota) → a rate-limit message; when the server sends
  `Retry-After`, the wait time is shown. `APIError.rateLimited(retryAfter:)`
  already captures `Retry-After` (shared with the runs limiter), so no change was
  needed there.
- **`400`** (no actionable order / missing size) → a validation message that
  echoes the server detail and guides the user to set a side + a positive size.

All of these occur **after** the biometric gate (the network call only runs once
`confirmAndSubmit()` has passed Face ID / Touch ID), and the Review button simply
re-enables in the failed state — so resolving an error (e.g. connecting
Robinhood, fixing the size) re-runs the **full** confirm + biometric gate. No
error state can bypass it.

### URL scheme + Info.plist additions

`project.yml` (and the on-disk `Info.plist`) now declare:

- **`NSFaceIDUsageDescription`** — required for the Face ID prompt on LIVE orders.
- **`CFBundleURLTypes`** with scheme **`stockagents`** — the OAuth bounce-back
  target. This must match the scheme of the backend's `MOBILE_OAUTH_APP_REDIRECT`
  (`stockagents://oauth/robinhood`).

Regenerate the project (`xcodegen generate`) after pulling so the plist keys land
in the build.

---

## Deferred / stubbed

Clearly called out so nobody mistakes a stub for a finished feature:

- **Login UI / auth flow — BUILT.** `AuthService` now performs a real Supabase
  Auth (GoTrue) email/password sign-in over REST, persists the access + refresh
  tokens in the Keychain, injects the bearer on every request, and refreshes on
  `401`. `LoginView` (gated by `AuthGateView`) is the sign-in screen and Settings
  has a sign-out affordance. The backend verifies the token with
  `MOBILE_AUTH_MODE=supabase` (`web/mobile/auth.py`). See
  [Configuring Supabase sign-in](#configuring-supabase-sign-in) for the required
  `SUPABASE_URL` / `SUPABASE_ANON_KEY` config (no secrets in source). Remaining:
  Sign in with Apple (Apple requires it once other social logins exist) and a
  sign-up / password-reset screen are not built; this ships email/password only.
- **Brokerage connect + LIVE order ticket — BUILT (v1).** See
  [Brokerage — connect + LIVE orders](#brokerage--connect--live-orders-v1). What
  is *not* yet built: a standalone read-only **account panel** (buying
  power/positions) screen — the `account` SSE event is still decoded but not
  surfaced as its own panel.
- **Order-submission endpoint — DONE (per-user v2).** `APIClient.submitOrder(...)`
  now targets the per-user-safe `POST /api/v2/runs/{run_id}/orders`
  (`web/mobile/orders.py` → `place_user_order`), which resolves the broker via the
  per-user `BrokerRegistry` (never the `get_broker()` singleton), forces the run's
  ticker, re-clamps size, and enforces the server's dry-run/live policy. It
  requires a bearer token. The order UI now **handles** the v2 error cases:
  `409` (Robinhood not connected → inline connect prompt), `404` (run
  expired/not owned → "no longer available"), `429` (order quota, with
  `Retry-After`), and `400` (no actionable order / missing size → validation
  guidance). See [Biometric-gated LIVE order ticket](#biometric-gated-live-order-ticket-plan-53-decision-4).
- **v2 endpoints now use the real sign-in.** The `/api/v2/*` router enforces
  `current_user`, so the connect + order calls need a bearer token. That token
  now comes from the Supabase sign-in above — sign in once and `AuthService`
  injects + refreshes it automatically. (The backend `MOBILE_AUTH_MODE=dev` HMAC
  token path still exists for backend-only testing, but the app no longer needs
  a manually minted dev token.)
- **Markdown fidelity — partial.** Uses `AttributedString(markdown:)`
  (inline-only). Agent tables/headings degrade gracefully; swapping in
  swift-markdown for full fidelity is a TODO (plan §8).
- **v2 features** — push notifications, run history/watchlist, PDF/share export,
  the admin cache toggle, and the Blueprint page — are not built.

---

## Discrepancies between the plan and the actual backend (`web/server.py`)

Per the task, where the plan and `server.py` disagree, **`server.py` wins**. The
notable gaps — all are backend work the plan itself lists as *to-be-added*, not
bugs in this client:

1. **No auth exists in the backend yet.** The plan (decisions §1.5, §3.4.1)
   assumes a multi-user, JWT-authenticated backend, but `web/server.py` ships
   **no auth middleware** — `/api/runs`, `/api/runs/{id}`, `/api/chart`, etc. are
   open (the only gates are the admin password and the blueprint HMAC). The client
   injects a bearer token *if it has one* and is ready for 401/refresh, but does
   not require sign-in to use today's backend.
2. **No rate-limiting endpoint behavior yet.** §3.4.1a describes per-user 429
   throttling on `POST /api/runs`; the backend doesn't implement it yet. The
   client already maps `429 + Retry-After` to a friendly error so it "just works"
   once the backend adds it.
3. **Robinhood OAuth now lives on the v2 router, not `server.py`.** The
   server-mediated flow is implemented in `web/mobile/` as
   `GET /api/v2/robinhood/authorize` / `/callback` / `/status` (not the
   `POST /api/robinhood/authorize` the plan §3.4.2 originally named). This client
   targets the **v2** paths. The legacy `server.py` still only has the
   desktop-only `POST /api/robinhood/connect` (server-side `webbrowser.open`),
   which this client does not call.
4. **No v2 order-submission endpoint yet.** The v2 router has runs/authorize/
   callback/status but **no** order placement route. `submitOrder(...)` therefore
   targets the legacy `POST /api/robinhood/orders/{run_id}` (single-user broker)
   as a documented placeholder — see the
   [Deferred / stubbed](#deferred--stubbed) TODO. Per-user run **history**
   (`GET /api/runs/history`) also still does not exist.
5. **Cached-run shape is inferred from the web client.** `POST /api/runs` returns
   `{"cached": true, "run": {...}}` where `run` comes from `db.get_recent_run`;
   the exact fields aren't typed in `server.py`, so `CachedRun` mirrors what
   `renderCached()` in `index.html` consumes (`ticker, trade_date, asset_type,
   decision, final_content, identity, created_at, agents[{seq, agent, content}]`).

The MVP request/response and the **entire SSE event union** otherwise match
`server.py` exactly (verified against `manager.emit(...)` call sites and
`web/charts.py` / `web/usage.py` payload shapes).

---

## Tests

`StockAgentsTests/` covers the parts most worth locking down without a running
backend:

- `AgentEventDecodingTests` — every SSE event `type` decodes to the right case
  with the right fields (incl. the `unknown` forward-compat path).
- `SSEParsingTests` — `RunStreamClient.parseField` line parsing edge cases.
- `ModelMappingTests` — verdict mapping, `StartRunResponse` started-vs-cached
  discrimination, and config/chart decoding.

Run with `xcodebuild test -scheme StockAgents -destination 'platform=iOS Simulator,name=iPhone 15'`
(or ⌘U in Xcode).
