#!/usr/bin/env sh
# Container entrypoint for the TradingAgents web app on Railway (or any PaaS).
#
# Robinhood OAuth cannot complete on a headless server (it needs a browser +
# localhost loopback). The supported pattern is: authorize ONCE locally, then
# ship the resulting token JSON to the server. This script seeds that token
# from the ROBINHOOD_TOKEN_JSON env var on first boot, writing it to the
# persistent token path so the deployed server reconnects via the refresh
# token with no browser. It never overwrites an existing file, so tokens that
# Robinhood rotates (and the app writes back to the volume) are preserved.
set -e

TOKEN_PATH="${TRADINGAGENTS_ROBINHOOD_TOKEN_PATH:-$HOME/.tradingagents/robinhood_token.json}"

if [ -n "${ROBINHOOD_TOKEN_JSON:-}" ] && [ ! -f "$TOKEN_PATH" ]; then
    mkdir -p "$(dirname "$TOKEN_PATH")"
    printf '%s' "$ROBINHOOD_TOKEN_JSON" > "$TOKEN_PATH"
    chmod 600 "$TOKEN_PATH"
    echo "[entrypoint] Seeded Robinhood token at $TOKEN_PATH"
fi

exec uvicorn web.server:app --host 0.0.0.0 --port "${PORT:-8000}"
