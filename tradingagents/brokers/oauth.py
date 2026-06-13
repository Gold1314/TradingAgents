"""OAuth 2.0 plumbing for connecting to the Robinhood Trading MCP.

The Robinhood MCP is an OAuth-protected streamable-HTTP server. The official
``mcp`` SDK drives the flow via an :class:`OAuthClientProvider`, which needs
three collaborators we supply here:

* a :class:`TokenStorage` — we persist tokens/client-registration to a JSON
  file so a user authorises once and subsequent runs reuse the refresh token;
* a ``redirect_handler`` — opens the consent URL in the user's browser;
* a ``callback_handler`` — a short-lived loopback HTTP server that captures the
  ``code``/``state`` Robinhood redirects back with.

All ``mcp`` imports are guarded: importing this module never fails just because
the optional SDK isn't installed. :data:`MCP_AVAILABLE` records availability and
:func:`build_oauth_provider` raises a clear error if called without it.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional, Tuple
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)

try:  # optional dependency — only needed when actually connecting
    from mcp.client.auth import OAuthClientProvider, TokenStorage
    from mcp.shared.auth import (
        OAuthClientInformationFull,
        OAuthClientMetadata,
        OAuthToken,
    )

    MCP_AVAILABLE = True
except Exception:  # noqa: BLE001 — any import problem means "unavailable"
    MCP_AVAILABLE = False

    class TokenStorage:  # type: ignore[no-redef]
        """Fallback base so the subclass below is always importable."""


class FileTokenStorage(TokenStorage):
    """Persist OAuth tokens and dynamic client registration to a JSON file.

    The file lives at ``path`` (default ``~/.tradingagents/robinhood_token.json``)
    and should be treated as a secret — it is git-ignored by the project and
    should never be committed. Reads fail open (return ``None``) so a corrupt or
    missing file simply triggers a fresh authorisation rather than crashing.
    """

    def __init__(self, path: str):
        self.path = os.path.expanduser(path)

    def _read(self) -> dict:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def _write(self, data: dict) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        # 0600 — tokens are sensitive; restrict to the owner.
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    async def get_tokens(self) -> Optional["OAuthToken"]:
        blob = self._read().get("tokens")
        if not blob:
            return None
        try:
            return OAuthToken.model_validate(blob)
        except Exception:  # noqa: BLE001 — stale shape → reauthorise
            return None

    async def set_tokens(self, tokens: "OAuthToken") -> None:
        data = self._read()
        data["tokens"] = tokens.model_dump(mode="json")
        self._write(data)

    async def get_client_info(self) -> Optional["OAuthClientInformationFull"]:
        blob = self._read().get("client_info")
        if not blob:
            return None
        try:
            return OAuthClientInformationFull.model_validate(blob)
        except Exception:  # noqa: BLE001
            return None

    async def set_client_info(self, client_info: "OAuthClientInformationFull") -> None:
        data = self._read()
        data["client_info"] = client_info.model_dump(mode="json")
        self._write(data)

    def has_tokens(self) -> bool:
        return bool(self._read().get("tokens"))

    def clear(self) -> None:
        try:
            os.remove(self.path)
        except OSError:
            pass


def _capture_callback(port: int, timeout: float = 300.0) -> Tuple[str, Optional[str]]:
    """Run a one-shot loopback server and return the (code, state) it receives.

    Blocks until Robinhood redirects to ``http://localhost:<port>/callback`` or
    ``timeout`` elapses. Designed to be called from a worker thread.
    """
    captured: dict = {}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 — required name
            params = parse_qs(urlparse(self.path).query)
            captured["code"] = params.get("code", [None])[0]
            captured["state"] = params.get("state", [None])[0]
            captured["error"] = params.get("error", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            body = (
                "<html><body style='font-family:sans-serif;text-align:center;"
                "margin-top:80px'><h2>Robinhood connected.</h2>"
                "<p>You can close this tab and return to TradingAgents.</p>"
                "</body></html>"
            )
            self.wfile.write(body.encode())
            done.set()

        def log_message(self, *_args):  # silence default stderr logging
            return

    server = HTTPServer(("127.0.0.1", port), Handler)
    server.timeout = 1.0
    try:
        elapsed = 0.0
        while not done.is_set() and elapsed < timeout:
            server.handle_request()
            elapsed += server.timeout
    finally:
        server.server_close()

    if captured.get("error"):
        raise RuntimeError(f"OAuth authorization failed: {captured['error']}")
    if not captured.get("code"):
        raise TimeoutError("Timed out waiting for the Robinhood OAuth callback.")
    return captured["code"], captured.get("state")


def build_oauth_provider(
    server_url: str,
    storage: "TokenStorage",
    callback_port: int = 8765,
    client_name: str = "TradingAgents",
):
    """Construct an :class:`OAuthClientProvider` for the Robinhood MCP.

    Raises ``RuntimeError`` if the ``mcp`` SDK is unavailable. The returned
    provider opens the consent page in the default browser and captures the
    redirect on ``http://localhost:<callback_port>/callback``.
    """
    if not MCP_AVAILABLE:
        raise RuntimeError(
            "The 'mcp' package is required for Robinhood OAuth. "
            "Install it with: pip install langchain-mcp-adapters"
        )

    redirect_uri = f"http://localhost:{callback_port}/callback"

    async def redirect_handler(authorization_url: str) -> None:
        logger.info("Opening browser for Robinhood authorization: %s", authorization_url)
        try:
            webbrowser.open(authorization_url)
        except Exception:  # noqa: BLE001 — headless env; user opens it manually
            pass
        print(
            "\n[Robinhood] Authorize TradingAgents in your browser:\n  "
            f"{authorization_url}\n"
        )

    async def callback_handler() -> Tuple[str, Optional[str]]:
        import asyncio

        # The loopback server is blocking; run it off the event loop.
        return await asyncio.to_thread(_capture_callback, callback_port)

    metadata = OAuthClientMetadata(
        client_name=client_name,
        redirect_uris=[redirect_uri],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",
    )

    return OAuthClientProvider(
        server_url=server_url,
        client_metadata=metadata,
        storage=storage,
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )
