# StockAgents iOS — Testing Guide

> A concrete, copy-pasteable plan for testing the **StockAgents** iOS app
> (`ios/`) and the backend it talks to (`web/server.py` + the gated mobile layer
> `web/mobile/`), covering **every feature except the live Robinhood OAuth
> connect** (that one flow is validated separately — see [§E](#e-what-still-requires-live-robinhood)).
>
> Companion to [`docs/ios-app-plan.md`](./ios-app-plan.md). It honours the
> web-app isolation principle (plan §1.6): everything here is **additive and
> default-OFF**; with the mobile flags unset the web app behaves exactly as it
> does today.

---

## 0. TL;DR — what you'll do

1. Bring up the backend in **mobile mode** locally ([§A](#a-backend-in-mobile-mode-locally)).
2. Mint a **dev bearer token** and smoke-test `/api/v2/*` with `curl`.
3. Run the iOS app in the **Simulator**, point it at the backend, sign in,
   watch a run, and exercise the trading UI **without live Robinhood** using a
   gated **fake broker** ([§C](#c-testing-the-trading-ui-without-live-robinhood)).
4. Run the XCTest unit tests on a Mac with Xcode ([§D](#d-ios-buildrun--automated-tests)).
5. Know what's deliberately **out of scope** ([§E](#e-what-still-requires-live-robinhood)).

Prereqs: Python env per `web/requirements.txt`, a valid LLM key (e.g.
`OPENAI_API_KEY`) for real runs, and — for the iOS parts — a Mac with **Xcode
15+** and **XcodeGen** (`brew install xcodegen`).

---

## A. Backend in mobile mode, locally

The mobile API is an **additive** `/api/v2/*` router gated behind
`MOBILE_API_ENABLED` (`web/mobile/__init__.py` → `include_mobile_api`). With the
flag unset it is never registered and the legacy `/api/*` web endpoints are
untouched.

### A.1 Environment

Create/append to a local `.env` (or just `export` in your shell). The minimum
to bring up the gated mobile API with the simple **dev** auth mode:

```bash
# Master switch for the /api/v2/* mobile surface (default OFF).
export MOBILE_API_ENABLED=true

# Dev auth: minimal HMAC bearer tokens minted locally (NOT for production).
export MOBILE_AUTH_MODE=dev
export MOBILE_AUTH_SECRET=dev-test-secret-please-change

# Keep rate limits low so you can SEE the 429 quickly while testing.
export MOBILE_RUNS_PER_HOUR=3
export MOBILE_RUNS_PER_DAY=20
export MOBILE_ORDERS_PER_HOUR=5
export MOBILE_ORDERS_PER_DAY=20

# Per-user broker token storage (kept out of the default home dir while testing).
export MOBILE_TOKEN_DIR="$PWD/.tmp-tokens"

# An LLM key so runs actually execute (use whichever provider you've configured).
export OPENAI_API_KEY=sk-...
```

> **Auth modes.** `MOBILE_AUTH_MODE=dev` (default) verifies a locally-minted
> HMAC token — perfect for `curl` and Simulator testing without an IdP. For the
> production path use `MOBILE_AUTH_MODE=supabase` plus the Supabase env
> (`SUPABASE_URL` + `SUPABASE_JWT_SECRET` **or** JWKS); see `.env.example` and
> `web/mobile/auth.py`. The iOS app's `LoginView` signs in against Supabase
> Auth, so end-to-end login testing needs `supabase` mode (see [§B](#b-per-feature-test-matrix-ios-app) row 1).

### A.2 Run the server

```bash
cd /Users/jalajnautiyal/Projects/TradingAgents
python -m uvicorn web.server:app --reload --port 8000
```

Confirm the mobile API registered (look for this on startup):

```
INFO ... Mobile API (/api/v2) enabled.
```

### A.3 Mint a dev bearer token

The dev token is an HMAC over `user_id|exp` (see `mint_dev_token` in
`web/mobile/auth.py`). Mint one with the **same secret** the server is running
with:

```bash
export TOKEN=$(python -c "from web.mobile.auth import mint_dev_token; \
print(mint_dev_token('test-user-1', 'dev-test-secret-please-change'))")
echo "$TOKEN"
```

(Default TTL is 30 days.) Send it as `Authorization: Bearer <token>` on every
`/api/v2/*` call.

### A.4 `curl` smoke tests

**Public bootstrap (no auth):**

```bash
curl -s localhost:8000/api/config | python -m json.tool | head -30
```

**Auth is enforced on v2 — expect 401 without a token:**

```bash
curl -s -o /dev/null -w "%{http_code}\n" \
  -X POST localhost:8000/api/v2/runs \
  -H 'Content-Type: application/json' \
  -d '{"ticker":"NVDA","trade_date":"2026-06-12"}'
# → 401
```

**Start a run (authenticated). Returns `{cached:false, run_id, nodes[]}`:**

```bash
curl -s -X POST localhost:8000/api/v2/runs \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"ticker":"NVDA","trade_date":"2026-06-12","analysts":["market","news"]}' \
  | python -m json.tool
```

Grab the `run_id`, then **watch the SSE stream** (the v2 run reuses the existing
read endpoints in `web/server.py`):

```bash
RUN_ID=...   # from the response above
curl -N -H "Authorization: Bearer $TOKEN" \
  "localhost:8000/api/runs/$RUN_ID/events"
# Server-Sent Events: id:/data: frames, `: keepalive` every 15s, ends on `done`.
```

> Resume test: note the last `id:` you saw, kill the curl, and reconnect with
> `-H "Last-Event-ID: <n>"` — the server replays from `n+1` (`run_events()`).

**Rate-limit (429) behaviour.** With `MOBILE_RUNS_PER_HOUR=3`, the 4th run in an
hour for the same user is rejected with `429` + `Retry-After`:

```bash
for i in 1 2 3 4; do
  echo -n "run $i → "
  curl -s -o /dev/null -w "%{http_code}  Retry-After=%header{retry-after}\n" \
    -X POST localhost:8000/api/v2/runs \
    -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -d '{"ticker":"NVDA","trade_date":"2026-06-12","force":true}'
done
# → 200, 200, 200, 429 (with Retry-After seconds)
```

(Per-user buckets: a token for a *different* `user_id` has its own quota.)

**Place an order in DRY-RUN mode.** The order endpoint targets the run's
proposed order, scoped to the calling user's own broker. **It requires a
*connected* broker** — see the important note below.

```bash
curl -s -X POST "localhost:8000/api/v2/runs/$RUN_ID/orders" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"action":"buy","notional":100}' \
  | python -m json.tool
```

> **Without a connected broker this returns `409 "Robinhood not connected."`** —
> by design. To get an actual simulated `trade` payload back over `curl`/in the
> Simulator **without live Robinhood**, enable the **fake broker**
> (`MOBILE_FAKE_BROKER=1`) described in [§C](#c-testing-the-trading-ui-without-live-robinhood).
> With the fake broker on, the same call returns:
>
> ```jsonc
> { "type": "trade", "trade_mode": "manual", "can_place_real_orders": false,
>   "dry_run": true, "status": "dry_run", "intent": { ... }, "message": "DRY RUN — ..." }
> ```

---

## B. Per-feature test matrix (iOS app)

How to read this: **Depends** lists what must be true for the row to work
(backend up? auth? broker?). **Device** marks where it must be tested —
🖥️ Simulator is fine, 📱 needs a **physical device** (biometrics and, in
practice, `ASWebAuthenticationSession`, behave differently or unavailable on the
Simulator).

| # | Feature / screen (source) | How to test | Depends | Expected result | Device |
| - | - | - | - | - | - |
| 1 | **Supabase login** (`LoginView`/`AuthService`) | In Settings set `SUPABASE_URL`+anon key (or build with them); enter email/password; sign in. Backend in `MOBILE_AUTH_MODE=supabase`. | Live backend + Supabase project + valid user | Lands on `RootView`; bearer is injected on subsequent calls. Blank config → "sign-in isn't configured". | 🖥️ |
| 2 | **401 → refresh → retry** (`APIClient.perform`) | Sign in, then make the access token stale (e.g. shorten Supabase JWT exp, or wait it out) and trigger a call (start a run). | Live backend (supabase mode) | One transparent refresh + retry; UI doesn't bounce to login. If refresh fails → signed out. | 🖥️ |
| 3 | **Run setup form** (`RunSetupView`/`RunSetupViewModel`) | Open app; form pre-fills from `GET /api/config`. Try empty ticker / zero analysts. | Live backend (`/api/config` is public) | Defaults populate; client-side validation blocks empty ticker / no analysts (mirrors server 400s). | 🖥️ |
| 4 | **Start run / 60-min cache** (`startRun`) | Tap "Run analysis". Re-run the **same** ticker/date within 60 min. | Live backend + LLM key | First → live SSE; identical re-run → cache banner; "Run fresh" re-posts with `force:true`. | 🖥️ |
| 5 | **Live SSE agent feed** (`RunStreamClient`→`RunSessionViewModel`) | Watch a run end-to-end: stage stepper, per-agent markdown cards, status pill + timer. | Live backend + LLM key | Cards stream in; stepper advances; `: keepalive` ignored; stream ends on `done`. | 🖥️ |
| 6 | **Mid-stream token refresh** (SSE) | During a long run (supabase mode) let the access token expire mid-stream. | Live backend (supabase mode) | On the mid-run `401`, client refreshes once and **reconnects with `Last-Event-ID`**; no events dropped. | 🖥️ |
| 7 | **Last-Event-ID resume** (`RunStreamClient` reconnect) | Mid-run, toggle Airplane Mode briefly (or kill/restore network) to drop the connection. | Live backend | Client reconnects with `Last-Event-ID`; server replays from `last+1`; feed continues without dupes. | 🖥️ (📱 for real radio) |
| 8 | **Verdict gauge** (`VerdictView`) | Let a run finish (`final` event). | Live backend + LLM key | Rating word + Sell→Buy gauge marker at the right position; PM rationale expands. | 🖥️ |
| 9 | **Price chart** (`PriceChartView`) | Open the Market Analyst card. | Live backend (`GET /api/chart`) | Candles + volume + SMA/EMA/Bollinger + RSI subchart render (Apple Charts). | 🖥️ |
| 10 | **Economics / token meter** (`EconomicsView`) | Watch `usage`/`usage_summary` during + after a run. | Live backend + LLM key | Per-agent token/cost bars; authoritative totals on `usage_summary`. | 🖥️ |
| 11 | **Session restore** (`ActiveRunStore`) | Start a run, force-quit the app mid-run, relaunch. | Live backend | `{run_id, request}` restored from `UserDefaults`; `GET /api/runs/{id}` re-attaches; stream resumes. | 🖥️ |
| 12 | **Connect status badge** (`RobinhoodConnectView`/`BrokerConnectViewModel`) | With `MOBILE_FAKE_BROKER=1`, open Connect; observe status. | Live backend + **fake broker** | `GET /api/v2/robinhood/status` → `connected:true` with mock account; badge shows connected. See [§C](#c-testing-the-trading-ui-without-live-robinhood). | 🖥️ |
| 13 | **Account snapshot** (status `account` block / `account` SSE) | Fake broker: read the `account` block from status. Full in-run grounding panel needs the legacy path. | Fake broker (status) / live broker (in-run SSE) | Mock buying power/positions via status. In-run SSE `account` panel needs live Robinhood (see §C.3 caveat). | 🖥️ |
| 14 | **Order ticket — DRY-RUN** (`OrderTicketView`/`OrderTicketViewModel`) | Fake broker + `TRADINGAGENTS_ROBINHOOD_DRY_RUN=true`. Finish a run, tap Review order, confirm (labeled "Simulated", no biometric). | Live backend + fake broker | `POST /api/v2/runs/{id}/orders` → `status:"dry_run"`; no real order; amber "Simulated" styling. | 🖥️ |
| 15 | **Order ticket error handling** | Force each: **409** (fake broker OFF → not connected), **404** (bad/expired `run_id`), **409** (`TRADE_MODE=off`), **429** (`MOBILE_ORDERS_PER_HOUR=1`, submit twice), **400** (buy with no size). | Live backend | Each maps to its typed `SubmissionFailure`: 409→Connect prompt / reason; 404→"no longer available"; 429→rate-limit + `Retry-After`; 400→size guidance. | 🖥️ |
| 16 | **Biometric LIVE gate** (`BiometricAuthenticator`) | Fake broker + LIVE config (`TRADE_MODE=manual`, `DRY_RUN=false`). Tap Review → confirm → Face ID/Touch ID prompt must pass before submit. | Live backend + fake broker + **enrolled biometrics** | Face ID/Touch ID prompt; success → `status:"placed"` with a **fake** order id (no real money). Cancel/fail → **not** submitted. No biometrics+no passcode → confirm disabled. | 📱 |

### B.1 Simulator vs physical device — what differs

- **Face ID / Touch ID (rows 14–16, `BiometricAuthenticator`).** The Simulator
  *can* fake biometrics via **Features ▸ Face ID ▸ Enrolled** then **Matching
  Face / Non-matching Face**, which is enough to test the success/cancel
  branches. But authoritative LIVE-gate testing (real enrollment, passcode
  fallback, the "no auth available → confirm disabled" branch) should be on a
  **physical device**.
- **`ASWebAuthenticationSession` (OAuth connect).** Out of scope here (see §E),
  but note: the system auth sheet is unreliable/headless-hostile on the
  Simulator and is best verified on device. The **fake broker** sidesteps it
  entirely for trading-UI testing — the broker reports connected without any
  OAuth round-trip.
- **Real cellular/radio (row 7).** Genuine network-drop/resume is best on a
  physical device on cellular; the Simulator can only approximate via Airplane
  Mode / Network Link Conditioner.

---

## C. Testing the trading UI WITHOUT live Robinhood

This is the important part. The trading surface is only reachable through a
**connected** broker, and connecting means live Robinhood OAuth (out of scope).
Two mechanisms make the rest testable safely:

### C.1 DRY-RUN trade mode (no real orders, ever)

The server decides dry-run-vs-live, never the client (`web/mobile/orders.py`).
With dry-run on, `POST /api/v2/runs/{id}/orders` runs the **real** safety
pipeline (ticker forced from the run, `clamp_intent`, idempotency) but returns a
**simulated** `trade` payload instead of sending an order:

```bash
export TRADINGAGENTS_ROBINHOOD_TRADE_MODE=manual
export TRADINGAGENTS_ROBINHOOD_DRY_RUN=true     # ← simulate; nothing leaves the building
```

This is driven by `RobinhoodConfig` (`tradingagents/brokers/config.py`); dry-run
is the **default**. The executor's dry-run branch (`AutoTradeExecutor.execute_intent`,
`tradingagents/brokers/executor.py`) returns `status:"dry_run"` and never calls
`place_order`.

### C.2 The fake broker (so "connected" + the order endpoint actually work)

DRY-RUN alone is **not enough**: with no saved OAuth tokens the real broker
reports `connected:false`, so `/api/v2/robinhood/status` shows disconnected and
the order endpoint rejects with `409 "Robinhood not connected."` — you can't
reach the order ticket at all.

So this repo adds a minimal, **additive, default-OFF** in-memory fake broker —
`web/mobile/fake_broker.py`, wired into the per-user `BrokerRegistry`
(`web/mobile/broker_registry.py`) and gated by `MOBILE_FAKE_BROKER`
(`web/mobile/settings.py`). It:

- reports **connected** with deterministic **mock account data** (buying power
  `$10,000`, a held `NVDA` position) → status badge + account snapshot render;
- runs the **same** `web/mobile/orders.py` + executor safety pipeline (so
  re-clamping, ticker-forcing, idempotency are all genuinely exercised);
- **never touches the network**, and **even with `dry_run=false` (LIVE) returns
  a fake order id** rather than placing a real order — so you can rehearse the
  full LIVE confirm + Face ID gate **safely**.

Enable it:

```bash
export MOBILE_FAKE_BROKER=1        # default OFF; test/dev only — never in production
```

Then re-run the §A.4 order `curl` — it now returns a real `dry_run` (or, under
LIVE config, `placed` with a `fake-order-…` id) payload. In the Simulator, the
Connect screen shows **connected** immediately (no OAuth), and the order ticket
becomes reachable.

> **§1.6 compliance.** The fake broker is entirely inside `web/mobile/` (the
> isolated mobile package), behind a new default-OFF flag, and `web/server.py`
> is **not** modified beyond the pre-existing guarded `include_mobile_api`
> include. The legacy `get_broker()` singleton path is untouched. With
> `MOBILE_FAKE_BROKER` unset, `build_user_broker` returns a real
> `RobinhoodBroker` exactly as before (covered by a regression test).

### C.3 Recommended trading-UI test configs

**Connected + DRY-RUN (rows 12–14):**

```bash
export MOBILE_API_ENABLED=true MOBILE_AUTH_MODE=dev MOBILE_AUTH_SECRET=...
export MOBILE_FAKE_BROKER=1
export TRADINGAGENTS_ROBINHOOD_TRADE_MODE=manual
export TRADINGAGENTS_ROBINHOOD_DRY_RUN=true
```

**Connected + LIVE biometric gate, still safe (row 16):**

```bash
export MOBILE_FAKE_BROKER=1
export TRADINGAGENTS_ROBINHOOD_TRADE_MODE=manual
export TRADINGAGENTS_ROBINHOOD_DRY_RUN=false   # status shows "LIVE — real money"…
# …but the fake broker returns a fake order id; no real order is ever sent.
```

> **Caveat — in-run account grounding SSE panel.** The `account` SSE event
> emitted *during a run* comes from the pipeline's grounding path, which uses
> the **legacy** process-wide `get_broker()` singleton in `web/server.py`
> (lines ~441/641), **not** the per-user registry — so the fake broker does
> **not** feed it. The fake broker covers the **connect status badge**, the
> **account snapshot via `/api/v2/robinhood/status`**, and the **order
> ticket/biometric** flow (the core trading UI). The in-run grounding panel
> genuinely needs a live (or legacy-configured) broker and is left to the live
> path. This is intentional: `web/server.py` is not modified (§1.6).

### C.4 Run the backend fake-broker tests

```bash
python -m pytest tests/test_mobile_fake_broker.py -q
```

Covers: default-OFF (real broker returned), flag-ON (fake returned), status
reports connected + mock account, DRY-RUN returns `dry_run` (no placement),
LIVE returns `placed` with a `fake-order-…` id and **no network**, server-side
re-clamping still applies, and the HTTP `/api/v2/robinhood/status` endpoint
reports connected with the flag on.

---

## D. iOS build/run + automated tests

The Xcode project is generated from `ios/project.yml` via **XcodeGen** (no
checked-in `.pbxproj`). All steps below require a Mac with Xcode.

### D.1 Generate & open

```bash
brew install xcodegen          # one-time
cd ios
xcodegen generate              # writes StockAgents.xcodeproj from project.yml
open StockAgents.xcodeproj
```

Pick the **StockAgents** scheme and an iOS 16+ Simulator (e.g. iPhone 15).

### D.2 Configure the backend base URL + Supabase

- **Base URL.** Resolves (priority): in-app **Settings** override
  (`UserDefaults`) → `STOCKAGENTS_BASE_URL` in `Info.plist`/`project.yml`
  (defaults `http://localhost:8000`) → fallback. For the Simulator hitting your
  local server, `http://localhost:8000` works (the Info.plist enables
  `NSAllowsLocalNetworking`). Production must be HTTPS.
- **Supabase sign-in** (needed for login row 1/2). Set `SUPABASE_URL` and
  `SUPABASE_ANON_KEY` (the **publishable anon** key — never the service-role
  key) via `project.yml`, an xcconfig, or the runtime `UserDefaults` overrides
  (`stockagents.supabase.url` / `stockagents.supabase.anonKey`). Blank → the
  login screen shows "sign-in isn't configured". The backend must run
  `MOBILE_AUTH_MODE=supabase` to verify the token the app sends.

> Tip: to test runs/feed quickly without standing up Supabase, you can leave
> auth unconfigured and exercise rows 3–11 against the public/legacy endpoints,
> since today's `web/server.py` does not enforce auth on `/api/*` (the client
> injects a bearer only if it has one). The `/api/v2/*` trading rows (12–16)
> still need a token — use `dev` mode + the fake broker, or `supabase` login.

### D.3 Run the XCTest unit tests

```bash
cd ios
xcodegen generate
xcodebuild test \
  -scheme StockAgents \
  -destination 'platform=iOS Simulator,name=iPhone 15'
```

(or ⌘U in Xcode). `StockAgentsTests/` covers the highest-value, backend-free
logic:

- `AgentEventDecodingTests` — every SSE event `type` decodes to the right case
  (incl. the `unknown` forward-compat path).
- `SSEParsingTests` — `RunStreamClient` line/frame parsing edge cases.
- `ModelMappingTests` — verdict mapping, started-vs-cached run discrimination,
  config/chart decoding.
- `AuthModelsTests` — Supabase session/error decoding.

### D.4 What can / can't be verified without a full Xcode toolchain

- **Honest caveat:** this repo's CI (and the environment this scaffold was
  authored in) has **no full Xcode SDK** — only `swiftc -parse` style syntax
  checking is available. That means: the Swift **compiles/links**, full
  **XCTest**, the **Simulator**, and anything touching `LocalAuthentication` /
  `ASWebAuthenticationSession` / Apple **Charts** **cannot** be verified here.
- The app was written to be compile-ready and its models/parsers are unit-tested
  in pure Swift, but **expect to fix minor issues on first real build**
  (`ios/README.md` says as much).
- **Recommendation:** on your Mac, run `xcodegen generate` then `xcodebuild
  build` and `xcodebuild test` (commands above) to get the authoritative
  compile + unit-test signal, then walk the §B matrix in the Simulator, and
  finish the biometric rows (14–16) on a physical device.

---

## E. What still requires live Robinhood

Deliberately **excluded** from this plan (validate separately, with a real
Agentic account):

1. **The live OAuth connect round-trip** — `GET /api/v2/robinhood/authorize` →
   `ASWebAuthenticationSession` → Robinhood consent → `GET
   /api/v2/robinhood/callback` → PKCE token exchange → per-user token persist →
   app-scheme bounce / status poll (plan §5.1.3). *(Currently blocked because the
   test account already has an agent bound.)*
2. **Real order placement** — an actual `place_equity_order` over MCP with
   `dry_run=false` against a real account (the fake broker simulates this; only a
   live broker proves the real MCP arg-mapping + order id round-trip).
3. **Real account grounding** — the in-run SSE `account` panel populated from a
   live brokerage account (uses the legacy `get_broker()` grounding path; see
   §C.3 caveat).
4. **Robinhood-specific OAuth facts** still needing a credentialed test
   (plan §5.1.5.G): whether Robinhood honours our HTTPS callback post-login,
   redirect allowlisting, end-to-end token exchange, and refresh-token
   TTL/rotation.

Everything else in the §B matrix — login + 401/refresh, run setup, live SSE feed
(incl. mid-stream refresh + `Last-Event-ID` resume), verdict, chart, economics,
session restore, the order ticket + error handling, and the biometric LIVE gate
— is testable **without** live Robinhood using DRY-RUN and the gated fake broker.
