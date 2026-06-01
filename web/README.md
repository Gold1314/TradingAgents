# StockAgents — Live Web UI

A thin, additive web layer (branded **StockAgents**) that runs the existing
`TradingAgentsGraph` and streams **every agent's output to the browser in real
time**. It does not modify the pipeline; it drives the same code path the CLI
uses.

```
Browser ──POST /api/runs──▶ FastAPI ──▶ TradingAgentsGraph.graph.stream(updates)
   ▲                           │
   └──── SSE (per-agent) ◀──────┘
```

## What you get

- An input form (ticker, date, asset type, analysts, optional model/round overrides).
- A **pipeline graph** whose nodes light up as each agent runs.
- A **live feed**: one expandable, markdown-rendered card per agent (analysts →
  bull/bear → research manager → trader → risk debate → portfolio manager).
- A final **Buy / Overweight / Hold / Underweight / Sell** decision banner.

## Run it (local)

From the repo root, with your virtualenv active and `.env` configured (same
`ANTHROPIC_API_KEY` / `TRADINGAGENTS_*` vars the CLI uses):

```bash
pip install -r web/requirements.txt
uvicorn web.server:app --reload --port 8000
```

Then open http://localhost:8000.

## Persistence + 60-minute cache (Supabase)

The app **works without Supabase** — storage and caching simply no-op until you
configure it. To enable them:

1. **Create a project** at [supabase.com](https://supabase.com) (free tier is fine).
2. **Create the tables**: open *SQL Editor ▸ New query*, paste the contents of
   [`web/supabase_schema.sql`](./supabase_schema.sql), and run it.
3. **Get your keys**: *Project Settings ▸ API*. Copy the **Project URL** and the
   **`service_role`** key (server-side only — never ship it to the browser).
4. **Add to `.env`** (repo root):

   ```bash
   SUPABASE_URL=https://<project-ref>.supabase.co
   SUPABASE_KEY=<service_role key>
   STOCKAGENTS_ADMIN_PASSWORD=<choose a password>   # unlocks the admin toggle
   ```

5. `pip install -r web/requirements.txt` (adds the `supabase` client) and restart.

### How it behaves

- **Every completed run** is written to `runs` (one row) + `agent_outputs` (one
  row per agent shown in the UI).
- **Repeat requests**: when the 60-minute cache is **on** and a stored run for
  the same **ticker + trade date** exists within 60 minutes, the app renders the
  stored result instantly with a banner — *"The last update for this ticker was
  made within 60 minutes (timestamp)."* — plus a **Run fresh anyway** button to
  bypass it.
- **Admin toggle**: click the ⚙ in the header, enter `STOCKAGENTS_ADMIN_PASSWORD`,
  and switch the global 60-minute cache on/off. The setting is stored in
  `app_settings` so it persists across restarts.

## Deploy to Railway

The repo ships deploy config for Railway:

- `Dockerfile.web` — builds the image (core package + web deps, runs uvicorn,
  binds `$PORT`).
- `railway.json` — tells Railway to use that Dockerfile, sets the start command,
  health check (`/api/config`), and a single replica.

### Steps

1. **Push** this repo to GitHub (Railway deploys from a repo).
2. In [Railway](https://railway.app): **New Project ▸ Deploy from GitHub repo**,
   pick this repo. Railway reads `railway.json` and builds `Dockerfile.web`
   automatically.
3. **Set variables** (Service ▸ *Variables*) — do **not** commit these:

   ```
   ANTHROPIC_API_KEY=...
   TRADINGAGENTS_LLM_PROVIDER=anthropic
   TRADINGAGENTS_DEEP_THINK_LLM=claude-sonnet-4-6
   TRADINGAGENTS_QUICK_THINK_LLM=claude-haiku-4-5
   TRADINGAGENTS_MAX_DEBATE_ROUNDS=1
   TRADINGAGENTS_MAX_RISK_ROUNDS=1
   SUPABASE_URL=https://<project-ref>.supabase.co
   SUPABASE_KEY=<service_role key>
   STOCKAGENTS_ADMIN_PASSWORD=<choose one>
   ```

   `PORT` is injected by Railway automatically — don't set it.
4. **Generate a domain**: Service ▸ *Settings ▸ Networking ▸ Generate Domain*.
   Open it — you're live.

### Important notes

- **Single replica only.** Run state (the SSE run registry) lives in process
  memory, so keep `numReplicas: 1` / one instance. Persistence + the 60-min
  cache survive restarts because they live in Supabase, not memory.
- **SSE / long runs.** A full analysis takes minutes; the server emits SSE
  keepalives every 15s so Railway's proxy won't drop the stream. It also sends
  `X-Accel-Buffering: no` to disable buffering.
- **Cost & access.** Every run consumes LLM credits. Add authentication (or
  Railway's private networking / an auth proxy) before sharing the URL publicly.
- **Filesystem is ephemeral.** The OHLCV cache and per-run `message_tool.log`
  are rebuilt on each deploy — that's expected; Supabase is the durable store.

### Local container test (optional)

```bash
docker build -f Dockerfile.web -t stockagents-web .
docker run --rm -p 8000:8000 --env-file .env stockagents-web
```
