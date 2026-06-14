# StockAgents iOS — Engineering Plan

> A concrete, build-ready plan for a native iOS app that mirrors and extends the
> existing **StockAgents** web app (`web/static/index.html` + `web/server.py`)
> driving the **TradingAgents** multi-agent pipeline (`tradingagents/`).
>
> **Scope of this document:** planning only. No Swift is written yet. Every
> claim is grounded in the actual code in this repository, with file/endpoint
> citations so an engineer can start executing immediately.

---

## 1. Executive summary

StockAgents is a single-page web app that lets a user run an LLM "trading desk"
on a ticker and watch ~12 agents deliberate in real time over Server-Sent Events
(SSE), ending in a 5-tier rating (**Sell / Underweight / Hold / Overweight /
Buy**). It optionally grounds the agents in a live **Robinhood** brokerage
account and can place the resulting order (manual button or auto), all gated
behind conservative safety flags.

The iOS app reproduces this experience natively:

- **MVP (v0.1):** Configure and start a run, stream the live agent feed, render
  the verdict + per-agent markdown reports, show the price chart and run
  economics (token/cost meter), restore an in-flight run on relaunch.
- **v1:** Robinhood connect (mobile OAuth), account grounding panel, and the
  **live** order ticket + place-order flow — placing **real brokerage orders**,
  every one gated behind explicit on-screen confirmation **and** a device
  biometric (Face ID / Touch ID), with secure token handling.
- **v2:** Push notifications when a long run completes, background refresh,
  watchlists / **per-user run history** (backed by Supabase), share/export (PDF
  or native share sheet), the agent-graph blueprint, iPad/multitasking polish.

The backend is **multi-user** (decision 1, §1.5): each user authenticates,
gets their own broker connection/token storage, and sees only their own runs.

**Key finding that shapes the whole plan:** the backend is *almost* a clean JSON
API already — it is FastAPI with typed Pydantic request models and JSON
responses, and it streams via SSE. The three things that are **not mobile-ready**
are (a) there is **no multi-user authentication** — the only gates are an admin
password and a blueprint HMAC, and the now-required multi-user model (decision 1)
needs per-user accounts/tokens; (b) broker state is **process-global** —
`get_broker()` in `web/server.py:209` is a singleton over a single token file
(`~/.tradingagents/robinhood_token.json`, `tradingagents/brokers/config.py:39`),
so it cannot safely serve more than one user; and (c) the **Robinhood OAuth flow
is hard-wired to a server-local loopback browser** (`webbrowser.open` +
`http://localhost:8765/callback` in `tradingagents/brokers/oauth.py:176-193`),
which cannot work for a phone talking to a remote server. These require explicit
backend changes (Section 3.4).

---

## 1.5 Decisions locked (resolved open questions)

The four product questions this plan previously left open (old Section 9) are now
**decided**. They are summarized here and worked through concretely in the
detailed sections cited.

| # | Decision | What it forces | Detailed in |
| --- | --- | --- | --- |
| 1 | **Multi-user backend.** The app supports many independent users. | Real per-user accounts + per-user JWT/session tokens; per-user broker token isolation (no more single process-wide `get_broker()` + single token file); per-user run history. | §3.2, §3.4.1, §3.4.3, §3.4.4, §5.2 |
| 2 | **Robinhood OAuth redirect = needs a spike, with a redirect-agnostic design.** The current loopback flow can't work for a phone→remote-server, and whether Robinhood's MCP authorization server accepts a custom-scheme or remote-https redirect via Dynamic Client Registration is genuinely **unverified**. | Adopt a **server-mediated OAuth** design that works even if only `localhost`/fixed redirects are allowed, plus a short spike to confirm what the broker's authorization server accepts. | §5.1 (findings + spike + primary/fallback flows), §3.4.2 |
| 3 | **Monetization deferred; add basic rate limiting now.** BYOK / paid tiers are **out of scope** for this phase. | A simple per-user request quota/throttle on the LLM-consuming endpoints (chiefly `POST /api/runs`), built on the decision-1 auth identity. | §3.4.1a |
| 4 | **Live trading, biometric-gated.** The shipped app **will place real brokerage orders**, but every order requires explicit on-screen confirmation **plus** a device biometric (Face ID / Touch ID) before submission. | Mandatory `LocalAuthentication` gate + explicit confirm in the order flow; live-trading App Store/compliance posture. | §5.3, §9 (App Store) |

These are no longer "open"; the remaining genuinely-open items are narrowed to
the OAuth spike outcome (§5.1) and standard launch hygiene (§9).

---

## 1.6 Web-app isolation principle (non-negotiable)

The existing web application must keep behaving **exactly as it does today** until
the maintainer *explicitly* opts into each backend change. The iOS effort is
**additive-only** to the running web app. Every backend change this plan calls for
(multi-user auth §3.4.1, per-user rate limiting §3.4.1a, mobile OAuth endpoints
§3.4.2/§5.1, per-user broker isolation §3.4.3, run history §3.4.4) MUST follow
these rules:

- **Additive, not modifying.** Introduce new routes (e.g. a versioned `/api/v2/*`
  or a dedicated mobile router) instead of altering the existing handlers in
  `web/server.py` that the web client depends on. The current single-user web
  flow keeps hitting its current endpoints unchanged.
- **Isolated in new modules.** Put new logic in new files/packages rather than
  rewriting existing functions in place.
- **Feature-flagged / env-gated, default OFF.** Auth enforcement, rate limiting,
  per-user broker isolation, and the OAuth redirect change all default to off, so
  nothing changes for the web app until a flag is flipped.
- **No shared-state breakage.** Per-user broker isolation (§3.4.3) is added
  *alongside* the existing process-wide `get_broker()` path, not by removing it,
  until the web app is migrated deliberately.

Rationale: the web app is under active development in parallel. Disjoint files +
default-off flags guarantee the two tracks don't interfere. This principle
governs implementation order in §7 and the risks in §9.

---

## 2. Current architecture summary

### 2.1 System shape

```
iOS app ──POST /api/runs──▶ FastAPI (web/server.py) ──▶ TradingAgentsGraph.graph.stream("updates")
   ▲                            │                              │
   └── SSE /api/runs/{id}/events┘                              ├─▶ LLM provider (OpenAI/Anthropic/…)
                                                               ├─▶ yfinance / Alpha Vantage (market data)
                                                               ├─▶ Supabase (runs, agent_outputs, cache)  [web/db.py]
                                                               └─▶ Robinhood Trading MCP (OAuth)           [tradingagents/brokers/]
```

The web server is an **additive layer** (`web/server.py` docstring): it imports
and drives the unmodified `TradingAgentsGraph` (`tradingagents/graph/trading_graph.py`),
runs each analysis in a background thread, and pushes per-node events onto a
per-run `asyncio.Queue` drained by an SSE endpoint (`RunManager`, `Run`
dataclass in `web/server.py`).

### 2.2 Backend API contract

All endpoints live in `web/server.py`. Base path is the server root; JSON unless
noted. **There is no auth middleware and no CORS middleware** — the only access
controls are the admin password header and the blueprint HMAC token.

| Method | Path | Request | Response (shape) | Notes / source |
| --- | --- | --- | --- | --- |
| `GET` | `/` | — | `index.html` | `index()` |
| `GET` | `/blueprint` | — | `blueprint.html` | `blueprint_page()` |
| `GET` | `/api/config` | — | `{provider, deep_model, quick_model, max_debate_rounds, max_risk_rounds, analysts[], analyst_display{}, supabase_configured, admin_available, cache_window_minutes, robinhood{public_status}}` | `get_config()` — drives the form defaults |
| `POST` | `/api/runs` | `RunRequest` (below) | `{cached:false, run_id, nodes[]}` **or** `{cached:true, run{…}}` | `start_run()`. Serves 60-min cache when enabled |
| `GET` | `/api/runs/{run_id}` | — | `{run_id, active, finished, nodes[], events:int}` | `run_status()` — used for session restore |
| `GET` | `/api/runs/{run_id}/events` | header `Last-Event-ID` (optional) | **SSE** `text/event-stream` (event protocol below) | `run_events()`. Resumes from last id; 15s keepalive |
| `GET` | `/api/chart` | query `ticker, trade_date, asset_type=stock, lookback=180` | `{symbol, from, to, candles[], volume[], indicators{}}` | `get_chart()` → `web/charts.py` (Lightweight-Charts shaped) |
| `GET` | `/api/robinhood/status` | — | `{enabled, mcp_url, grounding_enabled, trade_mode, dry_run, can_place_real_orders, auto_places_real_orders, max_order_notional, default_order_notional, order_type, available, connected, has_saved_credentials, tools[], error}` | `robinhood_status()` |
| `POST` | `/api/robinhood/connect` | — | same status dict | `robinhood_connect()` — **triggers server-side browser OAuth** |
| `POST` | `/api/robinhood/orders/{run_id}` | `PlaceOrderRequest` (below) | trade event dict (`_trade_event`) | `place_order()` — places the run's proposed order; idempotent |
| `POST` | `/api/blueprint/access` | `{email, analysts?}` | `{ok, token, expires_in}` | `blueprint_access()` — HMAC token, allowlist-gated |
| `GET` | `/api/graph/blueprint` | query `analysts, max_debate_rounds, max_risk_rounds, include_internal, token` | blueprint topology JSON | `get_graph_blueprint()` — needs valid token |
| `GET` | `/api/admin/settings` | header `X-Admin-Password` | `{cache_enabled, supabase_configured, cache_window_minutes}` | `admin_settings()` |
| `POST` | `/api/admin/cache` | `{enabled}` + header `X-Admin-Password` | `{cache_enabled}` | `admin_set_cache()` |
| — | `/static/*` | — | static assets | `StaticFiles` mount |

**`RunRequest`** (Pydantic model, `web/server.py`):
```jsonc
{
  "ticker": "NVDA",                 // required
  "trade_date": "2026-06-12",       // required (YYYY-MM-DD)
  "analysts": ["market","social","news","fundamentals"],
  "asset_type": "stock",            // stock | crypto | forex | commodity
  "provider": null,                 // optional LLM overrides
  "deep_model": null,
  "quick_model": null,
  "max_debate_rounds": null,
  "max_risk_rounds": null,
  "force": false                    // bypass the 60-minute cache
}
```

**`PlaceOrderRequest`** (all optional; omitted fields fall back to the agent's
proposal; ticker is **never** client-supplied — forced to the run's ticker):
```jsonc
{ "action": "buy|sell", "quantity": 3, "notional": 100.0, "order_type": "market|limit" }
```

### 2.3 SSE event protocol (`/api/runs/{run_id}/events`)

Each SSE message is `id: <int>\ndata: <json>\n\n`. The JSON `type` field
discriminates. Produced by `manager.emit(...)` in `web/server.py` and consumed
by `handleEvent()` in `index.html`. The iOS app must parse the same union:

| `type` | Payload fields | UI meaning |
| --- | --- | --- |
| `nodes` | `nodes[]` | Planned agent order — build the stepper/pipeline |
| `status` | `message` | Status pill text ("Running pipeline…") |
| `memory` | `phase`, `status`, `resolved?`, `has_context?` | Cross-run memory lifecycle (mostly silent in UI) |
| `identity` | `content` | Resolved instrument identity block |
| `account` | `connected`, `buying_power?`, `portfolio_value?`, `position?`, `message?` | Robinhood account grounding panel |
| `agent` | `node`, `status`("running"\|"done"), `content` | One agent card; markdown report when done |
| `graph` | `node`, `status`, `tools[]` | Tool-node activity (UI ignores) |
| `progress` | `active`, `completed[]`, `node`, `status` | Stepper progress sync |
| `usage` | `node`, `input_tokens`, `output_tokens`, `total_tokens`, `calls`, `model`, `cost` | Per-agent token/cost (live economics) |
| `usage_summary` | `nodes{}`, `totals{}` | Authoritative end-of-run economics |
| `final` | `decision`, `content` | The 5-tier verdict + PM rationale |
| `trade` | `trade_mode`, `can_place_real_orders`, `dry_run`, `status`, `intent{}`, `order_id?`, `message` | Robinhood order: proposed / placed / dry_run / skipped / error |
| `error` | `message`, `trace?` | Run failed |
| `done` | — | Terminal; close the stream |

The agent node set (and display names) is defined by `ANALYST_DISPLAY` +
`FIXED_NODES` in `web/server.py`: 4 selectable analysts (Market, Sentiment,
News, Fundamentals) then 8 fixed agents (Bull/Bear Researcher, Research Manager,
Trader, Aggressive/Conservative/Neutral Analyst, Portfolio Manager). The web UI
groups them into 5 stages (`STAGES` in `index.html`): Analysis → Research Debate
→ Trading Plan → Risk Debate → Decision.

### 2.4 Feature inventory (from `web/static/index.html`)

1. **Run form** — ticker, trade date, asset type, analyst multi-select, and an
   "Advanced" disclosure (provider, deep/quick model, debate & risk rounds).
   Defaults from `GET /api/config`.
2. **Live status** — status pill + elapsed timer.
3. **Stage stepper** — 5 stage cards with per-stage progress bars (`buildStepper`).
4. **Verdict hero** — large rating word, a Sell→Buy gradient gauge with a marker
   (`RATING_POS`/`RATING_COLOR`), and an expandable PM rationale.
5. **Live agent feed** — one expandable, markdown-rendered card per agent
   (`feedCard`/`updateCard`), each themed by an avatar/glyph/color (`NODE` map).
6. **Price chart** — candles + volume + SMA/Bollinger/RSI overlays inside the
   Market Analyst card, from `GET /api/chart` (TradingView Lightweight Charts).
7. **Run economics** — live token meter + estimated cost per agent and per
   stage, totals (`renderEconomics`), priced via `web/usage.py`.
8. **Robinhood panel** — header connect button + status badge
   (`initRobinhood`/`refreshRobinhoodStatus`), account grounding line, and the
   order ticket (`renderProposedTicket`/`appendTicket`/`placeProposedOrder`) with
   a LIVE-money confirm dialog.
9. **60-minute cache** — banner + "Run fresh anyway" on a cache hit.
10. **Session restore** — `sessionStorage` keeps the active run id; on reload it
    calls `GET /api/runs/{id}` and reconnects the SSE stream (`tryRestoreActiveRun`).
11. **PDF export** — client-side html2canvas/jsPDF render of the report.
12. **Admin modal** — password-gated 60-minute cache toggle.
13. **Blueprint** — separate page (`/blueprint`) showing the agent graph
    topology (email-gated).

### 2.5 Configuration & how it runs

- **Run server:** `uvicorn web.server:app --reload --port 8000` (`web/README.md`).
- **Deps:** `web/requirements.txt` (fastapi, uvicorn, supabase, langchain-mcp-adapters).
- **Env (`.env.example`):** LLM keys (`OPENAI_API_KEY`, etc.), `TRADINGAGENTS_*`
  overrides (`tradingagents/default_config.py` `_ENV_OVERRIDES`), Supabase
  (`SUPABASE_URL`/`SUPABASE_KEY`), `STOCKAGENTS_ADMIN_PASSWORD`, and the
  `TRADINGAGENTS_ROBINHOOD_*` gates (`tradingagents/brokers/config.py`).
- **Robinhood safety model:** real orders need `ENABLED=true` **and**
  `TRADE_MODE` in {manual,auto} **and** `DRY_RUN=false`. Orders are re-clamped
  server-side to `max_order_notional` and buying power (`clamp_intent` in
  `tradingagents/brokers/executor.py`).

---

## 3. Recommended iOS architecture

### 3.1 Language, framework, and floor

- **SwiftUI**, not UIKit. The UI is a reactive, list-and-card, stream-driven
  feed — a natural fit for SwiftUI + Combine/`AsyncSequence`. Drop to UIKit only
  for the chart host if needed (see 3.5).
- **Minimum iOS 16.0.** Justification: `AsyncSequence`/structured concurrency is
  mature, `NavigationStack`, `Charts` (Apple's framework, iOS 16+), and
  `URLSession.bytes(for:)` for SSE all require 16. iOS 16 covers the vast
  majority of active devices and avoids back-porting workarounds.
- **Swift 5.9+ / Xcode 15+.** Use `async/await` throughout; mark networking and
  view models `@MainActor` where they publish UI state.

### 3.2 App architecture pattern — MVVM + a thin service layer

```
Views (SwiftUI)
  └─ ViewModels (@MainActor, ObservableObject / @Observable)
       └─ Services
            ├─ APIClient            (REST: /api/config, /api/runs, /api/chart, …)
            ├─ RunStreamClient      (SSE consumer for /api/runs/{id}/events)
            ├─ RobinhoodService     (status/connect/place-order + OAuth)
            ├─ AuthService          (app auth token; see 3.4)
            └─ KeychainStore        (tokens/secrets)
       └─ Models (Codable: RunRequest, AgentEvent union, ChartPayload, TradeEvent, …)
```

- **One `RunSessionViewModel` per active analysis.** It owns the decoded event
  stream and projects it into observable state: `plannedNodes`, `agentsByNode`,
  `verdict`, `economics`, `account`, `trade`. This mirrors the mutation logic in
  `handleEvent()` but in Swift.
- **State management:** `@Observable` (iOS 17) or `ObservableObject` (iOS 16) on
  view models; `@State`/`@Binding` for local view state. No external state
  library needed for MVP. Persist the active-run handle the way the web app uses
  `sessionStorage` — store `{run_id, request}` in `UserDefaults` and restore on
  launch via `GET /api/runs/{id}`.

### 3.3 Networking & streaming layer

- **REST:** a small `APIClient` actor wrapping `URLSession` with `Codable`
  models, a configurable base URL, a 60s+ timeout for `/api/runs` (runs are slow
  to *start* returning but the POST itself returns fast with a `run_id`), and an
  injected auth header (Section 3.4).
- **SSE:** there is no first-party SwiftUI SSE API. Implement `RunStreamClient`
  on `URLSession.bytes(for:)`, reading the async byte stream line-by-line,
  buffering `id:`/`data:` frames, and yielding decoded `AgentEvent`s through an
  `AsyncThrowingStream`. Track the last `id:` so a reconnect can send
  `Last-Event-ID` exactly like the browser's `EventSource` does (the server
  resumes from it — `run_events()` in `web/server.py`). Handle the `: keepalive`
  comment lines (ignore them).
- **Decoding the event union:** decode `{"type": ...}` first, then switch on a
  Swift enum with associated values. Use an exhaustive switch (per the repo's
  `typescript-exhaustive-switch` philosophy, applied to Swift) so a new server
  event type forces a compile-time decision.

### 3.4 Required backend changes (explicit)

These are the changes the backend needs for a production mobile client. They are
additive and can be done without touching the agent pipeline.

1. **Multi-user authentication (decision 1 — required, not optional).** The
   backend is now **multi-user**, so it needs real accounts, not a shared
   bearer token. Today the only gates are the admin password and the blueprint
   HMAC; `/api/runs` is open and each run "consumes LLM credits"
   (`web/README.md`). Add a proper auth scheme:
   - **Identity store + sign-in.** Adopt **Supabase Auth** (the project already
     depends on `supabase` — `web/db.py`, `web/requirements.txt`) for
     email/password (and Sign in with Apple, which Apple requires when other
     social logins exist). This gives every user a stable `user_id` (the
     Supabase `auth.users` UUID). If Supabase Auth is not desired, mint
     per-user JWTs server-side reusing the existing HMAC helpers
     (`_mint_blueprint_token`/`_verify_blueprint_token`, `web/server.py:683-704`)
     but keyed to a `user_id` with a longer TTL and a refresh path.
   - **Per-request identity.** Add a FastAPI dependency
     `current_user(authorization: str = Header(...)) -> User` that validates the
     `Authorization: Bearer <jwt>` and resolves the `user_id`. Apply it to
     `/api/runs`, `/api/runs/{id}`, `/api/runs/{id}/events`, `/api/chart`, and
     **all** `/api/robinhood/*` endpoints. `GET /api/config` stays public
     (it's the bootstrap/health call).
   - **iOS side.** `AuthService` performs sign-in, stores the access/refresh
     token in the **Keychain** (`kSecAttrAccessibleWhenUnlockedThisDeviceOnly`),
     injects the bearer header on every `APIClient`/`RunStreamClient` request,
     and refreshes on 401.

   1a. **Basic per-user rate limiting (decision 3).** Monetization (BYOK / paid
   tiers) is **deferred / out of scope** for this phase, but each authenticated
   user must be throttled so one account can't drain LLM credits. Add a small
   per-`user_id` quota in front of the LLM-consuming endpoints — chiefly
   `POST /api/runs`, and lighter limits on `/api/chart`. Keep it simple and
   specific: a fixed-window/token-bucket counter keyed by `user_id` (e.g. **N
   runs/hour** and **M runs/day**, configurable via env such as
   `STOCKAGENTS_RUNS_PER_HOUR` / `STOCKAGENTS_RUNS_PER_DAY`). Store counters in
   Supabase (a `usage_counters` row per user/window) or an in-process
   `dict[user_id] -> (window_start, count)` guarded by a lock for a single
   instance; return **HTTP 429** with a `Retry-After` header when exceeded. The
   iOS app surfaces the 429 as a friendly "daily limit reached" state. (Note the
   60-minute cache in `start_run` already absorbs duplicate identical runs.)
2. **Mobile-friendly Robinhood OAuth (required for the trade feature).** The
   current flow is desktop/local only: `robinhood_connect()`
   (`web/server.py:805`) calls `broker.connect()` →
   `build_oauth_provider(...)` → `webbrowser.open(authorization_url)` on the
   **server** and captures the code on a **server-local** loopback
   `http://localhost:8765/callback` (`_capture_callback`,
   `tradingagents/brokers/oauth.py:111-155`). A phone cannot drive a browser on
   the server. The chosen design (decision 2) is **server-mediated OAuth**: the
   redirect URI points at a **server HTTPS endpoint**, the server completes the
   PKCE token exchange and persists per-user, and the iOS app only drives an
   `ASWebAuthenticationSession` and learns the result. This is deliberately
   redirect-agnostic so it works even if Robinhood's authorization server only
   accepts `localhost`/fixed redirects (full findings, spike, and the
   primary/fallback flows are in **§5.1**). Backend work:
   - Refactor `build_oauth_provider` (`oauth.py:158`) to accept injected
     `redirect_uri`, `redirect_handler`, and `callback_handler` instead of
     hard-coding loopback (it already builds `OAuthClientMetadata` with our
     `redirect_uris` at `oauth.py:195-201`, so the redirect value is ours to
     choose).
   - Add `POST /api/robinhood/authorize` → returns the `authorization_url` (and
     an opaque `connect_session` id) **without** opening a server browser.
   - Add `GET /api/robinhood/callback` (the registered redirect target) → server
     captures `code`/`state`, completes the exchange, persists tokens for the
     **authenticated user** (§3.4.3), then 302-redirects to a short app-scheme
     URL the auth session can detect.
   - Keep token *exchange* and *storage* server-side; the phone never holds
     brokerage tokens (§5.2).
3. **Per-user broker isolation (decision 1).** `get_broker()`
   (`web/server.py:209-214`) is a **process-wide singleton** built once from
   `RobinhoodBroker(load_robinhood_config(DEFAULT_CONFIG))`, and
   `FileTokenStorage` writes a single file
   (`~/.tradingagents/robinhood_token.json`, `config.py:39-42`). In a multi-user
   backend this is unsafe — user A's tokens would serve user B, and one user's
   `connect()` would mutate the shared `_broker._connected`/`_tools`/
   `_account_number` state for everyone. Required changes:
   - **Key broker instances by `user_id`.** Replace the singleton with a
     `dict[user_id -> RobinhoodBroker]` (lock-guarded, with idle eviction), so
     `get_broker(user_id)` returns that user's broker. Every caller
     (`robinhood_status`, `robinhood_connect`, `_place_pending_order` at
     `web/server.py:258`, the run grounding path at `web/server.py:435`/`614`)
     must thread the authenticated `user_id` through.
   - **Per-user token storage.** Make the token path user-scoped — either
     `~/.tradingagents/tokens/<user_id>.json` via
     `RobinhoodConfig.token_storage_path`, or (preferred for a real deployment) a
     Supabase-backed `TokenStorage` implementation that stores each user's
     encrypted `tokens`/`client_info` blob in a `broker_tokens` table keyed by
     `user_id`, replacing `FileTokenStorage` (`oauth.py:47-108`) so it works on
     ephemeral/multi-instance hosting (e.g. Railway). The `0600`-file model
     (`oauth.py:66-71`) is single-host only.
   - **Resolve config per user**, not from the global `DEFAULT_CONFIG`, so a
     user's trade-mode/limits are their own.
4. **Per-user history / watchlist endpoints (v2).** Data already lands in
   Supabase (`web/db.py` `store_run`), but there is no list endpoint and runs
   are not associated with a user. For the multi-user model (decision 1): add a
   `user_id` column to the runs table, stamp it from `current_user` on
   `store_run`, and add `GET /api/runs/history` that returns **only the
   caller's** runs (filtered by `user_id`, never global). This powers the native
   history tab and keeps one user's analyses private from another's.
5. **Push notifications (v2).** No mechanism exists today. Add (a) a
   device-token registration endpoint, and (b) an APNs send when a run emits its
   `done`/`final` event, so the app can notify on completion of a multi-minute
   run even when backgrounded.
6. **CORS:** **not needed for the native app** (CORS is a browser policy). Only
   add `CORSMiddleware` if a web client on a different origin will also call the
   API. Mentioned here only to preempt the question — the native client sends
   normal HTTP requests and is unaffected by the absence of CORS in `server.py`.
7. **Health/version endpoint (nice-to-have):** `GET /api/config` already works
   as a de-facto health check (Railway uses it) and as the app's bootstrap call.

### 3.5 Talking to the existing Python backend

No protocol change is needed beyond auth: the app speaks the same JSON + SSE the
browser does. The base URL is user/build configurable (local `http://…:8000`
for dev, the deployed HTTPS domain for release). **ATS:** production must be
HTTPS; for local dev against `http://localhost`, add a scoped ATS exception or
use a dev scheme only.

---

## 4. Feature-by-feature mapping (web → iOS)

| Web feature (source) | iOS screen / component | API calls | Native considerations |
| --- | --- | --- | --- |
| App bootstrap / form defaults (`loadConfig`) | `ConfigBootstrap` on launch → `RunSetupView` | `GET /api/config` | Cache last config; pre-fill defaults |
| Run form (ticker/date/asset/analysts/advanced) (`getFormState`) | `RunSetupView` (Form with `TextField`, `DatePicker`, `Picker`, analyst toggles, advanced `DisclosureGroup`) | builds `RunRequest` | Validate ticker non-empty + ≥1 analyst client-side (mirrors `start_run` 400s) |
| Start run / cache hit (`startRun`) | "Run analysis" button → push `RunSessionView` | `POST /api/runs` | If `cached` → render stored run + cache banner; else open SSE |
| Live status pill + timer | Header status chip + `TimelineView` timer | SSE `status` | — |
| Stage stepper (`buildStepper`/`refreshStepper`) | `StageStepperView` (5 segmented progress cards) | SSE `nodes`, `progress` | — |
| Live agent feed (`feedCard`/`updateCard`) | `AgentFeedView` → list of `AgentCardView` (expandable, markdown body) | SSE `agent` | Render markdown (see §8); smooth insert animations |
| Verdict hero + gauge (`showVerdict`) | `VerdictView` (big rating + custom gradient gauge + rationale disclosure) | SSE `final` | Map `RATING_POS`/`RATING_COLOR` to a Swift enum |
| Price chart (`loadChart`/`renderChart`) | `PriceChartView` inside Market Analyst card | `GET /api/chart` | Use Swift `Charts` (candles via `RectangleMark`/custom) + SMA/Bollinger line marks, RSI subchart |
| Run economics (`renderEconomics`) | `EconomicsView` (totals tiles + per-agent bars + per-stage rollup) | SSE `usage`, `usage_summary` | Native bar layout; format tokens/cost like `fmtTok`/`fmtCost` |
| Robinhood status badge (`initRobinhood`) | Toolbar `RobinhoodStatusButton` | `GET /api/robinhood/status` | Reflect connected/mode/LIVE in the label |
| Robinhood connect (`connectRobinhood`) | `ASWebAuthenticationSession` flow | `POST /api/robinhood/connect` (refactored) | **Deep link / custom scheme for callback** (§3.4, §5) |
| Account grounding panel (`showAccount`) | `AccountPanelView` | SSE `account` | Show buying power / portfolio / position |
| Order ticket + place (`renderProposedTicket`/`placeProposedOrder`) | `OrderTicketView` (side/amount/qty/type + Place/Skip) | `POST /api/robinhood/orders/{run_id}` | **Biometric (Face ID) confirm for LIVE orders** instead of `confirm()`; show dry-run vs LIVE styling |
| 60-min cache banner (`showCacheBanner`) | Inline banner + "Run fresh" button | re-`POST /api/runs` with `force:true` | — |
| Session restore (`tryRestoreActiveRun`) | App-launch restore | `GET /api/runs/{id}` then reconnect SSE | Persist `{run_id, request}` in `UserDefaults`; resume with `Last-Event-ID` |
| PDF export (`downloadPDF`) | Share sheet / `ShareLink` | (client-side) | Render report to PDF via `ImageRenderer`/`UIGraphicsPDFRenderer`, or native share |
| Admin cache toggle (`admin-*`) | `AdminView` (settings, hidden by default) | `GET/POST /api/admin/*` with `X-Admin-Password` | Store admin password in Keychain; likely internal-only |
| Blueprint page (`/blueprint`) | `BlueprintView` (v2) | `POST /api/blueprint/access`, `GET /api/graph/blueprint` | Could be a `WKWebView` of `/blueprint` for v2, or native graph later |

---

## 5. Broker / OAuth handling on iOS

The brokerage layer (`tradingagents/brokers/`) is the most security-sensitive
and the most mobile-divergent part. Concrete approach:

### 5.1 OAuth (connect) — investigation findings + spike plan

This is decision 2. The previous draft assumed the app could register a custom
URL scheme (`stockagents://oauth/robinhood`) directly with Robinhood and capture
the `code` on-device. After reading the actual broker code, that assumption is
**unverified and possibly wrong**, so the design below is deliberately
redirect-agnostic and backed by a short spike.

#### 5.1.1 How the current OAuth flow actually works (from the code)

- The connect path is `robinhood_connect()` (`web/server.py:805`) →
  `RobinhoodBroker.connect()` → `_async_connect()`
  (`tradingagents/brokers/robinhood_mcp.py:129-150`) → `build_oauth_provider(...)`
  (`tradingagents/brokers/oauth.py:158-209`).
- It does **not** hand-roll OAuth. It delegates to the MCP Python SDK's
  `OAuthClientProvider` (`oauth.py:203-209`), which implements the **MCP
  authorization spec (OAuth 2.1)**: authorization-server metadata **discovery**
  from the MCP URL (`https://agent.robinhood.com/mcp/trading`, `config.py:34`),
  **Dynamic Client Registration (DCR, RFC 7591)**, **PKCE**, and the
  authorization-code grant. We supply three collaborators:
  - a `TokenStorage` — `FileTokenStorage` persists both `tokens` **and**
    `client_info` (the DCR result) to JSON (`oauth.py:73-99`);
  - a `redirect_handler` — currently `webbrowser.open(authorization_url)` on the
    **server** (`oauth.py:178-187`);
  - a `callback_handler` — a one-shot loopback HTTP server on
    `127.0.0.1:8765/callback` that blocks for the `code`/`state`
    (`_capture_callback`, `oauth.py:111-155`).
- **Crucially, the redirect URI is *ours*, not a Robinhood-fixed value.** It is
  constructed locally as `http://localhost:{callback_port}/callback`
  (`oauth.py:176`) and submitted to Robinhood inside `OAuthClientMetadata(
  redirect_uris=[redirect_uri], token_endpoint_auth_method="none", …)`
  (`oauth.py:195-201`) during DCR. `token_endpoint_auth_method="none"` ⇒ a
  **public client using PKCE**. So changing the redirect URI is a one-line
  change in *our* code — the open question is purely **whether Robinhood's
  authorization server accepts the value we register**.
- **The code exchange is library-mediated and can run anywhere the provider
  runs** (today, the server). The loopback server only *captures* `code`/`state`;
  `OAuthClientProvider` then performs the PKCE token exchange and calls
  `storage.set_tokens(...)`. Nothing about the exchange is intrinsically tied to
  the phone.

#### 5.1.2 What is genuinely unknown (the spike)

Robinhood does not publish the agentic MCP authorization-server policy, so these
must be **tested live** against `https://agent.robinhood.com/mcp/trading` (a
half-day spike, ideally with a real Agentic account):

1. **Does DCR accept a remote HTTPS redirect URI?** Register
   `redirect_uris=["https://<our-domain>/api/robinhood/callback"]` and confirm
   the registration succeeds and the authorize→callback round-trip completes.
   *(This is the single fact that determines whether the primary flow below
   works as-is.)*
2. **Does DCR accept a custom-scheme redirect** (`stockagents://oauth/robinhood`)
   or **only `http://localhost`/`https`?** Many OAuth 2.1 servers reject custom
   schemes for "web" clients but allow loopback per RFC 8252. This decides
   whether an on-device capture is even possible.
3. **Is DCR open at all,** or does Robinhood require a pre-registered client_id?
   (If pre-registration is required, the redirect URI is fixed by them and we
   must match it.)
4. **Are multiple redirect URIs / wildcards allowed** on one registration (so
   dev `localhost`, staging, and prod can coexist)?
5. **Refresh-token behavior:** TTL, rotation, and whether re-consent is needed —
   affects how often the user re-auths in §5.2.

Capture the answers by instrumenting `build_oauth_provider` to log the
`authorization_url`, the registered `client_info`, and any DCR/redirect errors.

#### 5.1.3 Primary flow — server-mediated OAuth (works for any allowed redirect)

This is the recommended design **regardless of the spike outcome**, because the
redirect URI Robinhood sees is always a **server HTTPS endpoint** the phone never
needs to host. It only requires spike answer #1 (remote-https redirect accepted),
which is the common case for OAuth 2.1 web clients.

1. App (authenticated, §3.4.1) → `POST /api/robinhood/authorize`. Server builds
   the provider with `redirect_uri = https://<our-domain>/api/robinhood/callback`,
   runs it far enough to get the `authorization_url`, stashes the PKCE
   verifier/state under a `connect_session` keyed to the **`user_id`**, and
   returns `{ authorization_url, connect_session }` **without** opening a server
   browser (the old `redirect_handler` becomes "capture URL", not "open
   browser").
2. App launches
   `ASWebAuthenticationSession(url: authorization_url, callbackURLScheme: "stockagents")`
   with `prefersEphemeralWebBrowserSession = true`. The user authorizes inside
   the system browser.
3. Robinhood redirects to **the server**:
   `GET https://<our-domain>/api/robinhood/callback?code=…&state=…`. The server
   matches `state`→`connect_session`→`user_id`, completes the PKCE token
   exchange via the SDK, and persists tokens with the **per-user** storage
   (§3.4.3).
4. The server responds with a 302 to `stockagents://oauth/robinhood?status=ok`
   (a tiny app-scheme URL). `ASWebAuthenticationSession` matches the
   `callbackURLScheme` and hands that URL back to the app, which dismisses the
   sheet and refreshes `GET /api/robinhood/status`.
5. **Robustness:** because the result already lives server-side, the app can also
   just **poll `GET /api/robinhood/status`** until `connected: true` — so even if
   the app-scheme bounce is flaky, connection still resolves. The phone never
   sees `code`/`state` or any brokerage token.

#### 5.1.4 Fallback flow — loopback stays on the server (if remote/custom redirects are rejected)

If the spike shows Robinhood **only** accepts `http://localhost:<port>/callback`
(RFC 8252 loopback) or otherwise refuses our remote/custom redirect, do **not**
try to host a loopback on the phone. Instead keep the loopback **on the server**
and let the phone drive a server-hosted consent page:

1. App → `POST /api/robinhood/authorize` as above; the server uses the **existing
   loopback** redirect (`http://localhost:8765/callback`, `oauth.py:176`) but
   binds it per-`connect_session`, and returns a **server** URL like
   `https://<our-domain>/connect/robinhood?session=<id>` (a thin page that
   triggers `authorization_url`).
2. App opens **that server URL** in `ASWebAuthenticationSession`. All OAuth
   redirects happen *relative to the server's browser context*; Robinhood
   redirects to the server's own loopback, which the server captures exactly as
   today (`_capture_callback`). The token exchange + per-user persist run
   server-side.
3. Server finishes and 302s to `stockagents://oauth/robinhood?status=ok`; the app
   dismisses and polls `GET /api/robinhood/status`. (If even the app-scheme
   bounce can't be used, the app polls status on a timer — the connection still
   completes entirely server-side.)

In both flows the phone is only a *launcher + result observer*; **token storage
and exchange stay server-side** (§5.2), and the only code change to
`build_oauth_provider` is injecting the `redirect_uri`/handlers instead of
hard-coding loopback + `webbrowser.open`.

#### 5.1.5 OAuth spike results (executed 2026-06-12)

This subsection records the outcome of the §5.1.2 spike. It was run **without
real Robinhood credentials and without placing any orders** — only code reading,
inspection of the installed `mcp` SDK source, unauthenticated metadata
discovery, and unauthenticated Dynamic Client Registration (DCR) probes. Each
finding is tagged **[repo]** (confirmed from this repo's code), **[live]**
(confirmed by an unauthenticated network probe against Robinhood), **[sdk]**
(confirmed from the installed `mcp` SDK source), **[ext]** (external docs / bug
reports), or **[unverified]** (needs a live credentialed test).

##### A. Confirmed redirect-URI mechanics (it is fully ours to set)

- **[repo]** The redirect URI is built locally as
  `http://localhost:{callback_port}/callback` — `tradingagents/brokers/oauth.py:176`
  — and submitted to Robinhood via `OAuthClientMetadata(redirect_uris=[redirect_uri],
  grant_types=["authorization_code","refresh_token"], response_types=["code"],
  token_endpoint_auth_method="none")` at `tradingagents/brokers/oauth.py:195-201`.
  `token_endpoint_auth_method="none"` ⇒ **public client + PKCE** (verified
  expectation, see below).
- **[repo]** The default `callback_port` is `8765`
  (`tradingagents/brokers/config.py:67`, `oauth.py:161`), overridable via
  `TRADINGAGENTS_ROBINHOOD_CALLBACK_PORT` (`config.py:161`).
- **[sdk]** The SDK sends *exactly* `redirect_uris[0]` as the `redirect_uri`
  query param on **both** the authorize request and the token exchange —
  `OAuthClientProvider._perform_authorization_code_grant` (`mcp/client/auth/oauth2.py:342`)
  and `_exchange_token_authorization_code` (`oauth2.py:393`). So the value we put
  in `redirect_uris[0]` is *literally* what Robinhood sees. **Changing it is a
  one-line change in our code** — confirming the §5.1.1 claim.
- **[repo/sdk]** Token exchange and persistence are SDK-mediated and run wherever
  the provider runs (today, the server): `_handle_token_response` (`oauth2.py:409-422`)
  calls `storage.set_tokens(...)` → our `FileTokenStorage.set_tokens`
  (`oauth.py:82-85`), writing `tokens` + `client_info` to a `0600` JSON file
  (`oauth.py:66-71`, default `~/.tradingagents/robinhood_token.json`,
  `config.py:39-42`). The loopback server only *captures* `code`/`state`
  (`oauth.py:111-155`); nothing about the exchange is tied to the phone. **The
  phone never needs to hold the redirect, the code, or the tokens.**

##### B. What the MCP SDK / OAuth 2.1 DCR allows for redirect URIs

- **[sdk]** Redirect URIs are typed `list[AnyUrl]` with `min_length=1`
  (`mcp/shared/auth.py:44`). `AnyUrl` accepts **`https://…`, `http://localhost…`,
  AND custom schemes** like `stockagents://oauth/robinhood` — the SDK imposes **no
  restriction** on scheme. The optional URL-based-client-id (CIMD) path
  (`should_use_client_metadata_url`, `utils.py:266-289`) only activates when the
  server advertises `client_id_metadata_document_supported=true` — Robinhood does
  **not** (see C), so the SDK uses **DCR** (`oauth2.py:585-594`).
- **Conclusion:** any restriction on redirect URIs comes **entirely from
  Robinhood's authorization server**, not from the SDK or our code.

##### C. Authorization-server metadata (discovered live, unauthenticated)

A `POST https://agent.robinhood.com/mcp/trading` with no token returns **401**
with `WWW-Authenticate: Bearer resource_metadata="https://agent.robinhood.com/.well-known/oauth-protected-resource/mcp/trading"`
**[live]**, which drives the SDK's discovery exactly as coded.

- **[live] Protected Resource Metadata** (`/.well-known/oauth-protected-resource/mcp/trading`):
  `resource = https://agent.robinhood.com/mcp/trading`,
  `authorization_servers = ["https://agent.robinhood.com/mcp/trading"]`,
  `scopes_supported = ["internal"]`, `bearer_methods_supported = ["header"]`.
- **[live] Authorization Server Metadata** (`/.well-known/oauth-authorization-server/mcp/trading`):
  ```json
  {
    "issuer": "https://agent.robinhood.com/mcp/trading",
    "authorization_endpoint": "https://robinhood.com/oauth",
    "token_endpoint": "https://api.robinhood.com/oauth2/token/",
    "registration_endpoint": "https://agent.robinhood.com/oauth/trading/register",
    "grant_types_supported": ["authorization_code", "refresh_token"],
    "response_types_supported": ["code"],
    "code_challenge_methods_supported": ["S256"],
    "token_endpoint_auth_methods_supported": ["none"],
    "scopes_supported": ["internal"]
  }
  ```
  This confirms **[live]**: DCR is advertised and open, **PKCE S256** is required,
  the client is **public** (`auth_method = "none"`), and **refresh_token** grant is
  supported. `client_id_metadata_document_supported` is absent ⇒ CIMD off ⇒ DCR
  path. The scope our flow will request is `internal` (the SDK auto-selects it
  from PRM `scopes_supported`, `utils.py:105-126`).
- **[live] DCR is open but returns a *fixed, pre-provisioned public client***.
  Unauthenticated `POST …/oauth/trading/register` with five different
  `redirect_uris` sets — remote HTTPS, `http://localhost:8765/callback`,
  `http://127.0.0.1:8765/callback`, `stockagents://oauth/robinhood`, and a
  multi-URI list — **all returned `200`**, but **every response carried the same
  `client_id` (`LtLiNmbs9owbYfWgBlC68Z2VujIPuvGoAiSYr8xW`), `client_name` forced
  to `"Robinhood Trading"`, no `client_secret`**, and merely **echoed back**
  whatever `redirect_uris` we sent. **Interpretation:** registration is **not**
  validating or persisting per-client redirect URIs — Robinhood hands every caller
  one shared public client and enforces the redirect allowlist **later, at the
  authorize step (post-login)**. So a `200` from DCR tells us **nothing** about
  whether a given redirect URI will actually be honored.

##### D. The real redirect gate is post-login — and external evidence is decisive

- **[live]** The authorize endpoint `https://robinhood.com/oauth?…` returns the
  same ~8 KB client-rendered HTML SPA for a remote-HTTPS, a custom-scheme, and a
  `localhost` redirect alike — i.e. it does **not** validate `redirect_uri`
  server-side *before* login, so unauthenticated probing **cannot** confirm
  acceptance. This is the boundary of what's testable without credentials.
- **[ext] Strong external signal (Cursor staff, Robinhood-specific):** In the
  Cursor forum thread *"Cursor CLI – Robinhood MCP OAuth Fails"*, a maintainer
  states that the **CLI's `http://localhost:8787/callback` loopback redirect is
  rejected by Robinhood's OAuth server (403 on authorize → redirect to
  `robinhood.com/oauth/error`), while the IDE's custom-scheme deeplink
  (`cursor://…`) is accepted.** This is direct evidence that **Robinhood
  allowlists specific non-loopback redirects for the shared client and rejects
  arbitrary loopback** — exactly the configuration the repo currently defaults to
  (`http://localhost:8765/callback`).
- **[ext]** A reproduced manual exchange in an `anthropics/claude-code` bug report
  shows the `POST https://api.robinhood.com/oauth2/token/` exchange returning
  `200` with a Bearer token (`expires_in` ≈ `344000` s ≈ **~4 days**) plus a
  `refresh_token`, `scope`, and `user_uuid` — i.e. the grant + refresh machinery
  works once a redirect is accepted.
- **[ext]** Robinhood docs: the Agentic **account** must be **opened/onboarded on
  a desktop** ("copy the onboarding URL and open it in a desktop browser" on
  mobile); **no client secret / developer signup / API key** is needed (matches the
  public-client + open-DCR findings).

##### E. Refresh-token behavior

- **[sdk]** Refresh is automatic: when the access token is invalid and a refresh
  token + client info exist, the SDK does a `grant_type=refresh_token` POST to the
  token endpoint (`oauth2.py:424-452`, gated by `can_refresh_token`
  `oauth2.py:137-139`) and re-persists via `set_tokens` (`oauth2.py:465-467`).
  Access-token TTL is taken from the response `expires_in`
  (`calculate_token_expiry`, `OAuthContext.update_token_expiry` `oauth2.py:125-127`).
- **[repo]** Both `tokens` and the DCR `client_info` are persisted
  (`oauth.py:82-99`), so a refresh token survives restarts and is reused without
  re-consent — until it expires or is rejected, at which point the SDK clears
  tokens and forces a fresh authorization (`oauth2.py:505-507`, `_handle_refresh_response`).
- **[ext/unverified]** Observed access-token `expires_in` ≈ **4 days** (single
  bug-report data point); third-party blog claims refresh validity ≈ **8.5 days**
  before full re-login. **Rotation behavior (whether each refresh returns a new
  refresh token, and family-revocation on reuse) is unconfirmed** and must be
  measured live.

##### F. Ship decision

**Ship the primary server-mediated OAuth flow (§5.1.3) with a registered
*remote HTTPS* redirect `https://<our-domain>/api/robinhood/callback` — do NOT
ship anything that depends on a `localhost`/loopback redirect.** Rationale:

1. **Loopback is the worst bet.** It is the one redirect shape with **direct
   negative evidence** against Robinhood **[ext]**, and the repo's current default
   (`oauth.py:176`) uses exactly that — so the existing local flow is *not* a safe
   basis for mobile and may itself be fragile.
2. **A fixed, server-side redirect allowlist** (implied by the constant client_id
   **[live]** + the post-login gate **[live/ext]**) means a **stable, registered
   HTTPS callback** is the natural fit: one URL to get allowlisted, identical
   across all users, and the phone never hosts it.
3. **Tokens stay server-side** (§5.2): the SDK exchange + persistence already run
   server-side **[repo/sdk]**, so the phone only launches
   `ASWebAuthenticationSession` and observes the result (or polls
   `GET /api/robinhood/status`). No brokerage token ever touches the device.
4. **Custom-scheme is a viable *secondary* only.** Robinhood accepts custom-scheme
   deeplinks (`cursor://…`) **[ext]**, so `stockagents://oauth/robinhood` *might*
   work for an on-device capture — but that would force PKCE/token handling onto
   the phone, contradicting the server-side-token decision (§5.2), so it is **not**
   recommended unless remote-HTTPS is rejected in the live test.

The §5.1.4 fallback ("server-hosted loopback") should be **demoted**: since the
evidence points to **loopback itself being rejected**, the correct fallback if
remote-HTTPS is refused is a **server-registered custom-scheme/deeplink redirect
handled server-side**, not a loopback. The only required code change remains
injecting `redirect_uri`/handlers into `build_oauth_provider` (`oauth.py:158`).

##### G. Still requires a live, credentialed test (with a real Agentic account)

1. **Confirm Robinhood honors `https://<our-domain>/api/robinhood/callback`** at
   the authorize step (post-login) for the shared client — i.e. the
   authorize→callback round-trip completes and returns a `code`. This is *the*
   gating fact for the primary flow and is **not** answerable from DCR's `200`.
2. **Determine how the redirect allowlist is managed:** is HTTPS broadly accepted,
   or must our exact callback URL be **allowlisted by Robinhood** (partner
   onboarding)? If allowlisting is required, identify the request path/contact.
3. **Confirm the token exchange** `POST https://api.robinhood.com/oauth2/token/`
   succeeds end-to-end through the SDK (some clients hit an empty-token bug **[ext]**;
   verify our `FileTokenStorage`/per-user storage persists a non-empty `access_token`).
4. **Measure refresh-token lifetime + rotation:** real `expires_in`, refresh
   validity window, whether refresh rotates the refresh token, and the re-consent
   cadence the user will actually experience (§5.2 UX).
5. **Confirm the desktop-only onboarding constraint** **[ext]** for *first-time*
   agentic-account creation vs. subsequent re-auth, and design the iOS first-run
   accordingly (e.g. "open this onboarding URL on desktop" hand-off).
6. **Verify multi-redirect / dev-vs-prod coexistence** (localhost-for-dev may be
   unavailable given finding D — plan to use a deployed HTTPS staging callback for
   dev instead).

### 5.2 Token storage

- **Robinhood OAuth tokens stay on the server**, but **keyed per user**
  (decision 1, §3.4.3) — not in the single shared
  `~/.tradingagents/robinhood_token.json` that `FileTokenStorage` uses today
  (`oauth.py:47-108`). Use a per-user path or, preferably, a Supabase-backed
  `TokenStorage` keyed by `user_id`. The phone never holds brokerage tokens — it
  only holds the **app's** auth token. This minimizes what a lost device exposes
  and keeps brokerage credentials off-device.
- **On-device secrets** (the user's access/refresh token, the optional admin
  password) live in the **Keychain** (`kSecAttrAccessibleWhenUnlockedThisDeviceOnly`),
  never in `UserDefaults`.

### 5.3 Order execution — LIVE trading, biometric-gated (decision 4)

The shipped app **places real brokerage orders** (`manual` mode with
`dry_run=false`). Every real-money submission is gated behind **both** an
explicit on-screen confirmation **and** a device biometric.

- Reuse the server's existing safety pipeline unchanged: `rating_to_intent` →
  proposed order in `manual` mode → `POST /api/robinhood/orders/{run_id}` →
  `_place_pending_order` (`web/server.py:227`) → `clamp_intent` + idempotent
  placement (`executor.py:166`, `executor.py:238`). The app sends only the
  ticket *edits* (`action/quantity/notional/order_type`); the server re-clamps
  to `max_order_notional`/buying power and **forces the ticker** to the run's own
  (`web/server.py:241`). Client-side limits are never authoritative.
- **Mandatory two-factor confirm for LIVE orders.** Where the web app shows a
  single `confirm()` dialog when `can_place_real_orders` is true
  (`placeProposedOrder` in `index.html`), iOS requires **both**, in order:
  1. an **explicit confirmation UI** — a summary sheet stating side, ticker,
     dollar/share size, order type, and "**LIVE — real money**", with a
     deliberate "Place LIVE order" action (no accidental tap-through); then
  2. a **`LocalAuthentication` (`LAContext`) Face ID / Touch ID** evaluation
     (`.deviceOwnerAuthenticationWithBiometrics`, with a passcode fallback via
     `.deviceOwnerAuthentication`) that must **succeed** before the
     `POST /api/robinhood/orders/{run_id}` request is sent.

  The biometric is evaluated **client-side as a submission gate**; the server
  remains the authoritative re-clamp/idempotency layer. If biometrics are
  unavailable/unenrolled, fall back to device-passcode auth — never skip the
  gate for a LIVE order.
- **Dry-run/simulated placements** (`dry_run=true`) need no biometric, but should
  still show the confirmation summary clearly labeled "Simulated".
- **Surface dry-run vs LIVE unmistakably** (color + label), mirroring
  `appendTicket`'s "Simulate (dry run)" / "Place LIVE order" states, so the user
  always knows whether real money will move. Drive this from the
  `can_place_real_orders`/`dry_run` fields already in the `trade` SSE event and
  `GET /api/robinhood/status`.

---

## 6. Proposed Xcode project structure

```
StockAgents.xcodeproj  (or a Swift Package + thin app target)
StockAgents/
├─ App/
│  ├─ StockAgentsApp.swift            // @main, deep-link routing (oauth callback)
│  └─ AppEnvironment.swift            // base URL, DI container
├─ Models/
│  ├─ RunRequest.swift
│  ├─ RunConfig.swift                 // GET /api/config
│  ├─ AgentEvent.swift                // SSE union enum (nodes/status/agent/final/trade/…)
│  ├─ AgentNode.swift                 // node metadata (stage, icon, color) ← mirrors NODE map
│  ├─ Verdict.swift                   // 5-tier rating enum + gauge position
│  ├─ ChartPayload.swift             // GET /api/chart
│  ├─ Economics.swift                 // usage / usage_summary
│  ├─ TradeEvent.swift / OrderIntent.swift / AccountSnapshot.swift
│  └─ RobinhoodStatus.swift
├─ Services/
│  ├─ APIClient.swift                 // REST
│  ├─ RunStreamClient.swift           // SSE over URLSession.bytes
│  ├─ RobinhoodService.swift          // status/connect/authorize/callback/place
│  ├─ AuthService.swift               // app bearer token
│  ├─ KeychainStore.swift
│  ├─ ActiveRunStore.swift            // UserDefaults persistence + restore
│  └─ PushService.swift               // v2 APNs registration
├─ ViewModels/
│  ├─ RunSetupViewModel.swift
│  ├─ RunSessionViewModel.swift       // owns the live event projection
│  ├─ RobinhoodViewModel.swift
│  └─ AdminViewModel.swift
├─ Views/
│  ├─ RunSetupView.swift
│  ├─ RunSessionView.swift            // container: stepper + verdict + feed + economics + trade
│  ├─ StageStepperView.swift
│  ├─ VerdictView.swift
│  ├─ AgentFeedView.swift / AgentCardView.swift
│  ├─ PriceChartView.swift
│  ├─ EconomicsView.swift
│  ├─ AccountPanelView.swift / OrderTicketView.swift
│  ├─ RobinhoodConnectView.swift
│  └─ AdminView.swift
├─ Utilities/
│  ├─ Markdown.swift                  // AttributedString / swift-markdown rendering
│  ├─ Formatters.swift                // fmtUsd/fmtTok/fmtCost equivalents
│  └─ Theme.swift                     // colors mirroring NODE/STAGES/RATING_COLOR
└─ Resources/
   └─ Assets.xcassets
StockAgentsTests/                     // model decoding, SSE parsing, verdict mapping
StockAgentsUITests/
```

Key Swift types to define first: the `AgentEvent` enum (decodes the SSE union),
`AgentNode`/stage metadata (port `NODE` + `STAGES` from `index.html`), and
`Verdict` (port `RATING_POS`/`RATING_COLOR`). These unblock most of the UI.

---

## 7. Phased delivery roadmap

> Estimates assume one experienced iOS engineer; treat as sequencing guidance,
> not commitments. Backend tasks are called out so they can run in parallel.

**Milestone 0 — Foundations (3–5 days)**
- Xcode project, app architecture skeleton, `AppEnvironment`/base-URL config.
- `APIClient` + `Codable` models for `/api/config` and `RunRequest`.
- `AuthService` (sign-in, Keychain, bearer injection, 401 refresh).
- **Backend:** multi-user auth (§3.4.1) + basic per-user rate limiting on
  `POST /api/runs` (§3.4.1a).

**MVP / v0.1 — Watch a run (1.5–2.5 weeks)**
- `RunSetupView` + defaults from `/api/config`.
- `RunStreamClient` (SSE) + `RunSessionViewModel` event projection.
- `StageStepperView`, `AgentFeedView` (markdown), `VerdictView`.
- `EconomicsView` from `usage`/`usage_summary`.
- 60-minute cache rendering + "Run fresh", session restore on launch.
- *Exit:* a user can run NVDA and watch the full deliberation to a verdict.

**v1 — Brokerage / LIVE trading (1.5–2.5 weeks; gated on OAuth spike + refactor)**
- **Spike first:** confirm Robinhood DCR redirect acceptance (§5.1.2) to pick the
  primary (§5.1.3) vs fallback (§5.1.4) OAuth flow.
- **Backend:** server-mediated OAuth endpoints (§3.4.2) + per-user broker/token
  isolation (§3.4.3, now required).
- `RobinhoodService` + `ASWebAuthenticationSession` connect, status badge,
  account grounding panel.
- `OrderTicketView` + LIVE place-order with **explicit confirm + Face ID/Touch ID
  gate** (§5.3); Keychain.
- `PriceChartView` (Swift Charts) in the Market Analyst card.

**v2 — Polish & retention (2–3 weeks)**
- **Backend:** run-history endpoints (§3.4.4) + APNs send (§3.4.5).
- Push notification on run completion; background/relaunch handling.
- History/watchlist tab from Supabase-backed runs.
- PDF / native share export; iPad/multitasking layout; Blueprint (WKWebView).
- Accessibility (VoiceOver/Dynamic Type), localization scaffolding.

---

## 8. Dependencies & tooling

**Swift packages (prefer SPM, minimal set):**
- **Networking:** none required — `URLSession` + `async/await` covers REST and
  SSE (`URLSession.bytes`). Avoid Alamofire unless a concrete need appears.
- **SSE:** hand-rolled `RunStreamClient` (no dependency). Optionally
  `LDSwiftEventSource` if reconnection/back-off becomes fiddly, but the server's
  `Last-Event-ID` resume is simple enough to implement directly.
- **Charts:** Apple's **Charts** framework (iOS 16+) for candles + SMA/Bollinger
  + RSI. No third-party charting needed for parity with `renderChart`.
- **Markdown:** `AttributedString(markdown:)` for simple bodies, or
  **swift-markdown** / a small renderer for tables/headings the agents emit (the
  web app leans on `marked` for tables — see `feed-md` CSS). Validate against
  real agent output early.
- **Keychain:** thin wrapper over Security framework (or `KeychainAccess` if
  preferred).

**Tooling / CI:**
- **CI:** Xcode Cloud or GitHub Actions (`xcodebuild test` on a simulator) for
  unit tests (model decoding, SSE frame parsing, verdict mapping) and a smoke UI
  test.
- **Lint/format:** SwiftLint + swift-format in CI.
- **Code signing:** Automatic signing for dev; an App Store distribution
  certificate + provisioning profile for release (store secrets in CI, not the
  repo). For v1 add the **Associated Domains** entitlement only if Universal
  Links are chosen over a custom URL scheme for the OAuth callback; add the
  **Push Notifications** + **Background Modes (remote notification)**
  entitlements for v2.
- **Distribution:** TestFlight for internal/external beta; App Store for
  release. Budget for review friction on a financial/trading app (Section 9).

---

## 9. Risks & open questions

**Resolved (now decided — see §1.5):**
- ~~Single- vs multi-user backend?~~ → **Multi-user** (decision 1): per-user
  auth (§3.4.1), per-user broker/token isolation (§3.4.3), per-user history
  (§3.4.4).
- ~~Where do LLM/broker costs land?~~ → Monetization **deferred**; ship **basic
  per-user rate limiting** now (decision 3, §3.4.1a).
- ~~Should the app ever place real orders?~~ → **Yes, LIVE trading**, gated
  behind explicit confirmation **+** Face ID/Touch ID (decision 4, §5.3).

**Genuinely open (needs investigation, not a product call):**
1. **Robinhood OAuth redirect acceptance (the one real unknown).** The
   redirect-agnostic server-mediated design (§5.1.3) is chosen, but we still must
   **spike** what Robinhood's MCP authorization server accepts via DCR: a remote
   HTTPS redirect (primary flow), a custom scheme, or only `localhost` (fallback
   flow, §5.1.4) — plus refresh-token TTL/rotation. The design works for any of
   these outcomes; the spike (§5.1.2) only decides **which flow ships**.
   **Update (2026-06-12):** the spike's non-credentialed portion is **done** —
   see **§5.1.5 OAuth spike results**. It confirmed (live) open DCR, PKCE S256, a
   public client, and that the redirect URI is fully ours to set, and found
   (external) that Robinhood **rejects arbitrary loopback** but accepts
   non-loopback redirects — so the **primary server-mediated flow with a remote
   HTTPS redirect is selected** and loopback is ruled out. The remaining items
   (does Robinhood honor *our* HTTPS callback post-login; refresh TTL/rotation)
   need a **live credentialed test** (checklist in §5.1.5.G) before committing v1
   dates.

**Security considerations:**
5. **Brokerage credentials/tokens.** Keep Robinhood OAuth tokens server-side and
   **per user** (§5.2, §3.4.3); never sync them to the device. Protect each
   user's app auth token in the Keychain (this-device-only). LIVE orders are
   gated behind explicit confirmation **and** biometrics (§5.3). Ensure all
   transport is HTTPS/TLS in production (ATS on).
6. **Idempotency & re-clamping are already server-side** (`_place_pending_order`,
   `clamp_intent`) — the app must not assume client-side limits are
   authoritative, and must handle the idempotent "already placed" response.
7. **No secrets in the app or repo.** Per the user's standing rules, the base
   URL and any keys must be build-config/Keychain driven, never hardcoded.

**App Store / review:**
8. **Financial-app review for a LIVE-trading app (Guideline 3.1.x / 5.x).**
   Decision 4 means the App Store build **ships real-money trading**, so plan for
   the heightened scrutiny that follows rather than hedging with dry-run-only:
   - **Confirmation + biometric gate is a review asset** — demonstrate the
     explicit-confirm + Face ID flow (§5.3) in the review notes; it directly
     supports the "user is in control of every real-money action" expectation.
   - **Risk disclosures** front-and-center (the repo already carries a "not
     financial advice" disclaimer in `README.md`); surface it in-app before the
     first LIVE order and in the App Store description.
   - **Multi-user account requirements** (decision 1): a working **account
     deletion** path (Guideline 5.1.1(v)), **Sign in with Apple** if any other
     social login is offered, and a **privacy nutrition label** covering
     brokerage/financial data + linkage to identity.
   - **Reviewer access:** provide **demo credentials** and, ideally, a reviewer
     toggle that exercises the full order flow in `dry_run` (so Apple can see the
     gating without placing a real trade).
   - **Third-party brokerage:** be ready to explain the Robinhood Agentic MCP
     relationship and that trades execute in the user's own Robinhood account.
   - Expect **longer review cycles and possible rejections**; budget schedule
     slack and keep a config flag to ship grounding/dry-run-only as a contingency
     if review blocks LIVE at launch (without changing the product decision).
9. **SSE/long-running connections on cellular and under backgrounding.** Runs
   take minutes; iOS suspends networking when backgrounded. The `Last-Event-ID`
   resume covers reconnects, but completion-while-backgrounded really wants the
   v2 push-notification path (§3.4.5). Validate keepalive behavior (server sends
   `: keepalive` every 15s) against `URLSession` timeouts.

---

## Appendix — fast file map for the implementer

| Concern | Source of truth |
| --- | --- |
| HTTP endpoints + SSE emit | `web/server.py` |
| SSE event consumption logic to port | `handleEvent()` and helpers in `web/static/index.html` |
| Agent node/stage/color metadata | `NODE`, `STAGES`, `RATING_POS`, `RATING_COLOR` in `web/static/index.html` |
| Run request/response models | `RunRequest`, `PlaceOrderRequest` in `web/server.py` |
| Chart payload shape | `web/charts.py` (`build_chart_payload`) |
| Token/cost economics shape | `web/usage.py` (`TokenUsageTracker`, `PRICES`) |
| Robinhood config & safety gates | `tradingagents/brokers/config.py`, `tradingagents/default_config.py` |
| OAuth flow (to refactor for mobile) | `tradingagents/brokers/oauth.py` (`build_oauth_provider`, `_capture_callback`) |
| Order intent / clamping / execution | `tradingagents/brokers/intents.py`, `executor.py` |
| Broker MCP client | `tradingagents/brokers/robinhood_mcp.py` |
| Account grounding text | `tradingagents/brokers/grounding.py` |
| Persistence / cache | `web/db.py`, `web/supabase_schema.sql` |
| Run/deploy | `web/README.md`, `.env.example`, `web/requirements.txt` |
