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
#
# The container starts as root so it can take ownership of a root-owned mounted
# volume (Railway mounts volumes as root:root) and then drops to the
# unprivileged APP_USER to run the server.
set -e

APP_USER="${APP_USER:-appuser}"
# Default to APP_USER's home (not root's) so the path matches what the app
# resolves from `~` when it runs as APP_USER.
TOKEN_PATH="${TRADINGAGENTS_ROBINHOOD_TOKEN_PATH:-/home/$APP_USER/.tradingagents/robinhood_token.json}"
TOKEN_DIR="$(dirname "$TOKEN_PATH")"

IS_ROOT=0
[ "$(id -u)" = "0" ] && IS_ROOT=1

# Make the token directory writable by the unprivileged app user. Needed when a
# fresh volume is mounted root-owned; harmless otherwise (fail-open).
if [ "$IS_ROOT" = "1" ]; then
    mkdir -p "$TOKEN_DIR"
    chown -R "$APP_USER":"$APP_USER" "$TOKEN_DIR" 2>/dev/null || true
fi

if [ -n "${ROBINHOOD_TOKEN_JSON:-}" ] && [ ! -f "$TOKEN_PATH" ]; then
    mkdir -p "$TOKEN_DIR"
    printf '%s' "$ROBINHOOD_TOKEN_JSON" > "$TOKEN_PATH"
    chmod 600 "$TOKEN_PATH"
    [ "$IS_ROOT" = "1" ] && chown "$APP_USER":"$APP_USER" "$TOKEN_PATH" 2>/dev/null || true
    echo "[entrypoint] Seeded Robinhood token at $TOKEN_PATH"
fi

if [ "$IS_ROOT" = "1" ]; then
    exec gosu "$APP_USER" uvicorn web.server:app --host 0.0.0.0 --port "${PORT:-8000}"
fi
exec uvicorn web.server:app --host 0.0.0.0 --port "${PORT:-8000}"
