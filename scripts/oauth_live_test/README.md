# Robinhood OAuth — Live Test Harness & Runbook

A self-contained, **additive and isolated** harness (per
[`docs/ios-app-plan.md`](../../docs/ios-app-plan.md) §1.6) for executing the
remaining **live, credentialed** OAuth checklist in **§5.1.5.G** of that plan.

It reuses the real OAuth machinery — `tradingagents/brokers/oauth.py`
(`FileTokenStorage`, the `OAuthClientMetadata` shape) and the installed `mcp`
SDK (PKCE, Dynamic Client Registration, token exchange/refresh) — so what you
test here is the *same* code path the shipped server-mediated flow (§5.1.3) will
run. It does **not** import or modify `web/server.py`, the web frontend, or the
behaviour of any existing broker module.

> **Safety contract**
> - `discover` and `build-authorize-url` are **unauthenticated and read-only** — no credentials, no tokens.
> - `exchange` and `refresh` are **opt-in** behind `--live` and stop at confirming token acquisition/refresh.
> - **No command ever places, reviews, or simulates a brokerage order.** This harness has no code path to the order tools.

---

## 0. What each command verifies (mapped to §5.1.5.G)

| Command | Credentials? | §5.1.5.G items it bears on |
| --- | --- | --- |
| `discover` | No | Validates the §5.1.5.C assumptions (DCR open, PKCE S256, public client, endpoints) and corroborates §5.1.5.D (DCR returns a shared client + echoes redirects). Establishes preconditions for G1/G3/G4; prints all six as `UNKNOWN`. |
| `build-authorize-url` | No | Produces the URL whose post-login result answers **G1** (callback honored), and supports **G2/G6** by letting you build URLs for different redirect shapes/hosts. |
| `exchange --live` | **Yes** | **G1** (a code that exchanges proves the redirect was honored), **G3** (non-empty `access_token` + `refresh_token` persisted), partial **G2** (our exact URL accepted). |
| `refresh --live` | **Yes** | **G4** (TTL + rotation; run repeatedly over time). |
| *(manual observation)* | — | **G5** (desktop-only onboarding) and the "broadly accepted vs allowlisted" half of **G2** / **G6** are recorded by hand — see §5 below. |

---

## 1. Prerequisites

1. **A real Robinhood Agentic account.**
   - Per §5.1.5.D, the Agentic account must be **opened/onboarded on a desktop
     browser** (this is itself checklist item **G5** — note whether onboarding
     and *re-auth* differ).
   - No client secret / developer signup / API key is required (open DCR +
     public client).
2. **A deployed HTTPS callback you control** for the primary (shipped) flow,
   e.g. `https://staging.<your-domain>/api/robinhood/callback`. You only need it
   to *receive* the `?code=...&state=...` redirect and read the values; a real
   FastAPI handler is not required for the test (you can read the code from your
   server's access log or the browser address bar). **No server to deploy?** Use
   the zero-infrastructure **tunnel** flow in §3b — a cloudflared/ngrok tunnel
   plus the bundled local listener gives you a real HTTPS callback on your
   laptop. For local-only testing you can also use the demoted loopback shape
   (see §4).
3. **Python env with the project deps** (the repo's `.venv` already has them):
   ```bash
   # from the repo root
   .venv/bin/python -m scripts.oauth_live_test --help
   ```

### Requirements / dependencies note

**No new dependencies.** The harness uses only the standard library plus what
`oauth.py` already relies on:
- `httpx` — already installed (transitively via the `mcp` SDK / project deps).
- `mcp` — already required by `tradingagents/brokers/oauth.py` (installed via
  `langchain-mcp-adapters`). Needed for `build-authorize-url`/`exchange`/`refresh`.

`discover` needs only `httpx`. If `mcp` is somehow missing, `discover` still
runs; the other commands print a clear install hint.

---

## 2. Configuration (flags or `OAUTH_LIVE_TEST_*` env vars)

Every flag has an env-var equivalent; flags win. Defaults run with zero setup.

| Flag | Env var | Default | Purpose |
| --- | --- | --- | --- |
| `--mcp-url` | `OAUTH_LIVE_TEST_MCP_URL` | repo `DEFAULT_MCP_URL` | Robinhood MCP URL |
| `--public-base-url` | `OAUTH_LIVE_TEST_PUBLIC_BASE_URL` | — | HTTPS base for the `remote-https` redirect |
| `--redirect-shape` | `OAUTH_LIVE_TEST_REDIRECT_SHAPE` | `remote-https` | `remote-https` \| `custom-scheme` \| `loopback` |
| `--redirect-uri` | `OAUTH_LIVE_TEST_REDIRECT_URI` | — | Explicit URI (overrides the shape) |
| `--custom-scheme-uri` | `OAUTH_LIVE_TEST_CUSTOM_SCHEME_URI` | `stockagents://oauth/robinhood` | Custom-scheme redirect |
| `--callback-path` | `OAUTH_LIVE_TEST_CALLBACK_PATH` | `/api/robinhood/callback` | Path appended to the base URL |
| `--callback-port` | `OAUTH_LIVE_TEST_CALLBACK_PORT` | `8765` | Loopback port |
| `--client-id` | `OAUTH_LIVE_TEST_CLIENT_ID` | (via DCR) | Skip DCR and use a known client_id |
| `--client-name` | `OAUTH_LIVE_TEST_CLIENT_NAME` | `TradingAgents` | DCR client_name |
| `--token-path` | `OAUTH_LIVE_TEST_TOKEN_PATH` | `~/.tradingagents/oauth_live_test/harness_token.json` | Where exchanged tokens persist (**isolated** — never the real broker token) |
| `--session-file` | `OAUTH_LIVE_TEST_SESSION_FILE` | `~/.tradingagents/oauth_live_test/session.json` | PKCE verifier/state between build + exchange |
| `--results-file` | `OAUTH_LIVE_TEST_RESULTS_FILE` | `~/.tradingagents/oauth_live_test/results.json` | Appended measurement log |
| `--timeout` | `OAUTH_LIVE_TEST_TIMEOUT` | `30` | Per-request HTTP timeout (s) |

All scratch files default **outside the repo** (under `~/.tradingagents/`), so
nothing the harness writes can be committed. The token path is deliberately
**not** the real `~/.tradingagents/robinhood_token.json`.

---

## 3. The run, step by step (primary = remote-HTTPS)

### Step 1 — `discover` (no credentials)

```bash
.venv/bin/python -m scripts.oauth_live_test discover
```

**PASS looks like:** every `assume.*` row is `PASS` (DCR open, PKCE S256, public
client `none`, endpoints present) and the DCR probe shows all redirect shapes
returning the **same** `client_id` with `redirect_uris` merely echoed. This
re-confirms §5.1.5.C/D. The six `G*` rows are expected to be `UNKNOWN` here.

### Step 2 — `build-authorize-url` (no credentials)

```bash
.venv/bin/python -m scripts.oauth_live_test build-authorize-url \
  --public-base-url https://staging.<your-domain>
```

This prints the authorize URL (with `code_challenge`, `state`, `scope=internal`,
and the RFC 8707 `resource` param) and saves the PKCE verifier/state to the
session file.

**Do this:** open the printed URL in a desktop browser **while logged in to your
Robinhood Agentic account** and approve consent.

**PASS for G1 looks like:** Robinhood redirects to **your** callback with
`?code=...&state=...`. **FAIL looks like:** you land on
`https://robinhood.com/oauth/error` (the redirect was rejected — see §5.1.5.D).

Read the `code` and `state` from your callback (server log or address bar).

### Step 3 — `exchange --live` (credentialed; **no orders**)

```bash
.venv/bin/python -m scripts.oauth_live_test exchange --live \
  --code "<CODE_FROM_CALLBACK>" --state "<STATE_FROM_CALLBACK>"
```

The harness validates `state` against the saved session, then performs the SDK
PKCE token exchange and persists via `FileTokenStorage`.

**PASS for G3 looks like:** `ok: True`, `access_token_present: True`,
`access_token_len` > 0, `refresh_token_present: True`, `persisted_ok: True`.
This also flips **G1 → PASS** (a code that exchanges proves the redirect was
honored end-to-end). Note the reported `expires_in_human` (initial access-token
TTL).

### Step 4 — `refresh --live` (credentialed; **no orders**)

```bash
.venv/bin/python -m scripts.oauth_live_test refresh --live
```

Exercises the `refresh_token` grant and appends a timestamped record.

**PASS for G4 looks like:** `ok: True` with `access_token_changed: True`. Inspect
`refresh_token_rotated` (True ⇒ the refresh token rotated) and `new_expires_in`.
**Run this repeatedly over hours/days** to measure the real TTL, whether each
refresh rotates the refresh token, and the re-consent cadence. Each run appends
to the results file for comparison.

---

## 3b. HTTPS callback via tunnel (zero-infrastructure)

You do **not** need to deploy a server to run the live test. A local listener
plus a tunnel (cloudflared or ngrok) gives Robinhood a real `https://…` redirect
that forwards to your laptop, and the harness captures `code`/`state`
automatically. The only thing you must bring is a **Robinhood Agentic account**.

**How it works:**

```
Robinhood ──redirect──▶ https://<rand>.trycloudflare.com/api/robinhood/callback
                                   │  (cloudflared/ngrok, runs on your laptop)
                                   ▼
                        http://127.0.0.1:8765/api/robinhood/callback
                                   │  (one-shot listener: scripts/oauth_live_test/callback_server.py)
                                   ▼
                 captures code+state, validates state, stores code in the session
```

Two new pieces (both inside `scripts/oauth_live_test/`, nothing else touched):

- **`callback_server.py`** — a one-shot listener bound to `127.0.0.1` that
  serves the configured callback path, captures `code`/`state`, validates
  `state`, shows a plain success page, and auto-shuts-down after the first
  callback (or a 300s timeout). It **never logs the code or any token**, and has
  no order/trade code path.
- **`tunnel.py`** — starts `cloudflared`/`ngrok` (whichever is installed) and
  prints the public URL. It does **not** bundle or download binaries; if neither
  tool is on `PATH` it prints install instructions and exits without failing.

### Step-by-step

> Use a **desktop browser** logged in to your Agentic account (first-time
> onboarding is desktop-only per §5.1.5.D — **G5**).

**Terminal A — start the tunnel** (leave it running):

```bash
# cloudflared (recommended; no signup for quick tunnels):
cloudflared tunnel --url http://localhost:8765
#   → prints e.g. https://random-words.trycloudflare.com

# …or ngrok (needs a free authtoken once: `ngrok config add-authtoken <TOKEN>`):
ngrok http 8765

# …or let the harness pick whichever is installed:
.venv/bin/python -m scripts.oauth_live_test tunnel --port 8765
```

Copy the printed `https://…` URL — that's your `--public-base-url`.

**Terminal B — run the harness:**

```bash
# 1) (optional) re-confirm metadata, no credentials:
.venv/bin/python -m scripts.oauth_live_test discover

# 2) Build the authorize URL with the TUNNEL url, and start the listener.
#    The redirect becomes  https://<tunnel>/api/robinhood/callback .
.venv/bin/python -m scripts.oauth_live_test build-authorize-url \
  --public-base-url https://random-words.trycloudflare.com \
  --serve-callback
#    → prints the authorize URL, then blocks listening on 127.0.0.1:8765.

# 3) Open the printed authorize URL in your desktop browser, log in, approve.
#    Robinhood → tunnel → local listener captures code+state, validates state,
#    saves the code into the session (NOT printed), and shows a success page.

# 4) Exchange (credentialed; no orders). No --code needed — it's in the session:
.venv/bin/python -m scripts.oauth_live_test exchange --live

# 5) Refresh (credentialed; no orders); repeat over time for TTL/rotation:
.venv/bin/python -m scripts.oauth_live_test refresh --live
```

To mirror the **production** redirect path exactly (`web/mobile/settings.py`
`CALLBACK_PATH = /api/v2/robinhood/callback`), add
`--callback-path /api/v2/robinhood/callback` to the `build-authorize-url`
command (the listener will serve that path).

### What PASS looks like (mapped to §5.1.5.G)

- **G1** — after approving, the browser lands on the listener's "Robinhood
  connected." page and Terminal B prints `code_captured: True`,
  `state_matches: True`. **FAIL** = the browser lands on
  `https://robinhood.com/oauth/error` (redirect rejected) — the listener stays
  waiting / times out.
- **G3** — `exchange --live` prints `ok: True`, `access_token_present: True`,
  `refresh_token_present: True`, `persisted_ok: True`.
- **G4** — `refresh --live` prints `ok: True`, `access_token_changed: True`;
  inspect `refresh_token_rotated` and `new_expires_in`; repeat over hours/days.
- **G2 (partial)** — a tunnel HTTPS URL being accepted shows HTTPS works, but a
  fresh `trycloudflare.com` host changing on each run is itself a useful probe of
  whether **any** HTTPS callback is accepted vs. a **specific allowlisted** URL
  (see the caveat below). For the dev-vs-prod half (**G6**), re-run §3b with a
  second tunnel/host and compare.

### Caveat — Robinhood may still require redirect allowlisting

Per §5.1.5.D, the redirect gate is enforced **post-login**, and external
evidence shows Robinhood **allowlists specific redirects** for the shared client
(custom-scheme `cursor://…` accepted; CLI loopback rejected). A random
`trycloudflare.com`/`ngrok` host therefore **might be rejected even though the
mechanics are correct**. If so, that is itself the answer to **G2**: HTTPS is
**not** broadly accepted and our exact callback URL must be allowlisted via
Robinhood partner onboarding — **record the rejection and the contact/onboarding
path** in §5 / `docs/ios-app-plan.md` §5.1.5. A rejected tunnel does **not**
invalidate the shipped server-mediated design (§5.1.3); it just means the real
deployed callback host needs allowlisting before v1.

---

## 4. Testing alternative redirect shapes (G2 / G6)

The ship decision (§5.1.5.F) is **remote-https only**, but you can empirically
probe what the post-login allowlist accepts:

- **Second HTTPS host (G6 — dev/prod coexistence):** re-run Steps 2–3 with a
  different `--public-base-url` (e.g. prod vs staging) and compare whether each
  is honored post-login.
- **Custom scheme (secondary per §5.1.5.F.4):**
  ```bash
  .venv/bin/python -m scripts.oauth_live_test build-authorize-url --redirect-shape custom-scheme
  ```
- **Loopback (demoted fallback; expected to FAIL per §5.1.5.D):** capture the
  code automatically with a one-shot listener:
  ```bash
  .venv/bin/python -m scripts.oauth_live_test build-authorize-url \
    --redirect-shape loopback --serve-loopback
  ```

For **G2**, note whether *any* HTTPS callback works (broadly accepted) or only a
specific pre-arranged URL (you needed Robinhood to **allowlist** it — if so,
record the partner/onboarding contact path you used).

---

## 5. Recording results & feeding back into the plan

The harness appends machine-readable records to the results file
(`~/.tradingagents/oauth_live_test/results.json` by default). In addition,
capture these **manual** observations alongside the run:

- **G2 / allowlist:** Was an arbitrary HTTPS callback accepted, or did your exact
  URL have to be allowlisted by Robinhood? If allowlisted, what was the request
  path/contact?
- **G4 / TTL & rotation:** initial `expires_in`; refresh-token validity window
  before full re-login; whether refresh rotates the refresh token; behaviour on
  reuse of an old refresh token (family revocation?).
- **G5 / desktop-only onboarding:** Did first-time Agentic account creation
  require desktop? Did subsequent *re-auth* work without it? Describe the iOS
  first-run hand-off implication.
- **G6 / coexistence:** Which redirect hosts/shapes were honored simultaneously.

Then update `docs/ios-app-plan.md`:

- If the **remote-HTTPS callback is honored (G1 PASS)** and HTTPS is broadly
  accepted (G2), mark the **primary server-mediated flow (§5.1.3) as confirmed**
  and finalize v1 dates (§7).
- If our exact URL had to be **allowlisted**, record that **partner allowlisting
  is required** and capture the process in §5.1.5/§9.
- If remote-HTTPS is **rejected**, fall back to the §5.1.5.F-recommended
  **server-registered custom-scheme/deeplink** (not loopback), and re-run §4 to
  confirm.
- Fold the measured **TTL/rotation (G4)** into the re-auth UX in §5.2, and the
  **desktop-onboarding (G5)** finding into the iOS first-run design.

---

## 6. Cleanup

The harness writes only to `~/.tradingagents/oauth_live_test/` by default
(outside the repo). To wipe scratch + any acquired tokens:

```bash
rm -rf ~/.tradingagents/oauth_live_test
```
