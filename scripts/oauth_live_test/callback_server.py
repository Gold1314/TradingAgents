"""Local callback listener for the tunnel-based live OAuth test.

Robinhood (post-login) redirects the browser to the registered redirect URI.
For a zero-infrastructure live test that URI is a **public HTTPS tunnel**
(cloudflared / ngrok) which forwards the request to this loopback listener. We
capture ``?code=...&state=...`` off the redirect, validate ``state``, show a
plain success page, and shut down after the first callback (or a timeout).

Why a dedicated listener instead of ``tradingagents.brokers.oauth._capture_callback``?

* That helper is hard-wired to the *loopback* redirect shape and silently
  captures any GET regardless of path. Behind a tunnel we want to (a) match the
  exact configured callback path (so a tunnel pre-flight / ``/favicon.ico`` hit
  does not get mistaken for the OAuth redirect) and (b) keep this harness
  additive/isolated (per ios-app-plan §1.6) without modifying ``tradingagents/``.

Safety
------
* Binds to ``127.0.0.1`` — local-only *beyond the tunnel*. The tunnel daemon
  runs on the same machine and connects over loopback; nothing else can reach it.
* **Never logs the authorization code or any token.** The HTTP access log is
  silenced and the success page does not echo the ``code``.
* Handles **only** the callback path. There is no order/trade code path here.
* One-shot: returns after the first real callback or when ``timeout`` elapses.
"""

from __future__ import annotations

from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Dict, Optional
from urllib.parse import parse_qs, urlparse


@dataclass
class CallbackCapture:
    """Outcome of one (or zero) callback received by the local listener."""

    code: Optional[str]
    state: Optional[str]
    error: Optional[str]
    path: Optional[str]
    timed_out: bool
    state_matches: Optional[bool]

    @property
    def ok(self) -> bool:
        return bool(self.code) and not self.error and not self.timed_out


_PAGE = (
    "<!doctype html><html><head><meta charset='utf-8'>"
    "<meta name='viewport' content='width=device-width, initial-scale=1'>"
    "<title>TradingAgents OAuth</title></head>"
    "<body style='font-family:-apple-system,Segoe UI,sans-serif;text-align:center;"
    "margin-top:80px;color:#1c1c1e'>"
    "<h2>{heading}</h2><p style='color:#555'>{body}</p>"
    "</body></html>"
)


def _render(heading: str, body: str) -> bytes:
    return _PAGE.format(heading=heading, body=body).encode("utf-8")


def _normalise_path(callback_path: str) -> str:
    path = callback_path.strip()
    if not path.startswith("/"):
        path = "/" + path
    return path.rstrip("/") or "/"


def serve_one_callback(
    port: int,
    callback_path: str,
    *,
    expected_state: Optional[str] = None,
    host: str = "127.0.0.1",
    timeout: float = 300.0,
    poll_interval: float = 1.0,
) -> CallbackCapture:
    """Block until the configured callback path is hit once, then return.

    Only a request whose path matches ``callback_path`` (or which carries a
    ``code``/``error`` query param) is treated as the OAuth redirect; anything
    else (``/``, ``/favicon.ico``, tunnel health checks) gets a benign 404 and
    the listener keeps waiting. Returns a :class:`CallbackCapture`; on timeout
    its ``timed_out`` flag is ``True`` and ``code`` is ``None``.
    """
    target_path = _normalise_path(callback_path)
    captured: Dict[str, Optional[str]] = {}
    done = {"flag": False}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 — required name
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            req_path = parsed.path.rstrip("/") or "/"
            has_oauth_params = bool(params.get("code") or params.get("error"))

            if req_path != target_path and not has_oauth_params:
                # Not the OAuth redirect (favicon / tunnel preflight / root).
                self.send_response(404)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(_render("Not found", "This listener only serves the OAuth callback."))
                return

            captured["code"] = params.get("code", [None])[0]
            captured["state"] = params.get("state", [None])[0]
            captured["error"] = params.get("error", [None])[0]
            captured["path"] = req_path

            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            if captured["error"]:
                self.wfile.write(_render(
                    "Authorization failed",
                    "Robinhood returned an error on the callback. "
                    "Return to the terminal for details.",
                ))
            else:
                self.wfile.write(_render(
                    "Robinhood connected.",
                    "You can close this tab and return to the TradingAgents harness.",
                ))
            done["flag"] = True

        def log_message(self, *_args):  # silence default stderr logging (no secrets)
            return

    server = HTTPServer((host, port), Handler)
    server.timeout = poll_interval
    try:
        elapsed = 0.0
        while not done["flag"] and elapsed < timeout:
            server.handle_request()  # returns after poll_interval if no request
            elapsed += poll_interval
    finally:
        server.server_close()

    if not done["flag"]:
        return CallbackCapture(
            code=None, state=None, error=None, path=None,
            timed_out=True, state_matches=None,
        )

    state = captured.get("state")
    state_matches: Optional[bool]
    if expected_state is None:
        state_matches = None
    else:
        state_matches = bool(state) and state == expected_state

    return CallbackCapture(
        code=captured.get("code"),
        state=state,
        error=captured.get("error"),
        path=captured.get("path"),
        timed_out=False,
        state_matches=state_matches,
    )
