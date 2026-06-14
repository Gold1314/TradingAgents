"""Server-mediated Robinhood OAuth for the mobile (v2) API.

Implements the *primary* flow from the iOS plan (§5.1.3): the redirect URI is a
remote-HTTPS endpoint **we** own (``<PUBLIC_BASE_URL>/api/v2/robinhood/callback``),
the server completes the PKCE token exchange, and tokens are persisted
per-user. The phone only opens the authorization URL in an
``ASWebAuthenticationSession`` and observes the result (or polls
``/api/v2/robinhood/status``); it never holds a brokerage token.

Mechanics — we reuse the MCP SDK's ``OAuthClientProvider`` (via
``build_oauth_provider`` with injected handlers) exactly as the desktop loopback
flow does, but swap the two I/O collaborators:

* ``redirect_handler`` — instead of opening a server browser, it *captures* the
  authorization URL so the API can return it to the phone.
* ``callback_handler`` — instead of a loopback HTTP server, it blocks until our
  ``/callback`` endpoint feeds it the ``code``/``state`` Robinhood redirected
  back with.

The broker's ``connect()`` runs on a worker thread (it bridges to its own event
loop internally), so the handlers — which run on that loop — coordinate with the
FastAPI request threads via plain :class:`threading.Event`/``to_thread`` waits,
mirroring the existing ``_capture_callback`` design.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from tradingagents.brokers.oauth import build_oauth_provider
from web.mobile.broker_registry import registry
from web.mobile.settings import MobileSettings, load_settings

logger = logging.getLogger("tradingagents.web.mobile.oauth")

# How long to wait for the provider to produce an authorization URL (metadata
# discovery + dynamic client registration). Fast in practice.
AUTH_URL_TIMEOUT = 60.0
# How long the callback_handler waits for the user to finish authorizing.
CALLBACK_TIMEOUT = 300.0
# How long /callback waits for the token exchange to finish after feeding code.
EXCHANGE_TIMEOUT = 60.0
# Drop connect sessions that were never completed after this long.
SESSION_TTL = 600.0


@dataclass
class ConnectSession:
    """One in-flight server-mediated OAuth attempt for a single user."""

    session_id: str
    user_id: str
    created_at: float = field(default_factory=time.time)

    # redirect_handler -> these (authorization URL captured from the SDK).
    auth_url: Optional[str] = None
    auth_url_event: threading.Event = field(default_factory=threading.Event)

    # /callback -> these (code/state from Robinhood's redirect).
    code: Optional[str] = None
    state: Optional[str] = None
    cb_error: Optional[str] = None
    callback_event: threading.Event = field(default_factory=threading.Event)

    # worker -> these (final outcome of broker.connect()).
    connected: bool = False
    error: Optional[str] = None
    done: threading.Event = field(default_factory=threading.Event)

    def make_redirect_handler(self):
        async def redirect_handler(authorization_url: str) -> None:
            self.auth_url = authorization_url
            self.auth_url_event.set()

        return redirect_handler

    def make_callback_handler(self):
        async def callback_handler() -> Tuple[str, Optional[str]]:
            got = await asyncio.to_thread(self.callback_event.wait, CALLBACK_TIMEOUT)
            if not got:
                raise TimeoutError("Timed out waiting for the Robinhood OAuth callback.")
            if self.cb_error:
                raise RuntimeError(f"OAuth authorization failed: {self.cb_error}")
            if not self.code:
                raise RuntimeError("No authorization code received.")
            return self.code, self.state

        return callback_handler


class ConnectSessionManager:
    """Tracks in-flight :class:`ConnectSession`s, indexed by id and by state."""

    def __init__(self) -> None:
        self._by_id: Dict[str, ConnectSession] = {}
        self._by_state: Dict[str, ConnectSession] = {}
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        stale = [
            sid for sid, s in self._by_id.items()
            if s.done.is_set() or (now - s.created_at) > SESSION_TTL
        ]
        for sid in stale:
            sess = self._by_id.pop(sid, None)
            if sess and sess.state:
                self._by_state.pop(sess.state, None)

    def _register(self, session: ConnectSession) -> None:
        with self._lock:
            self._prune(time.time())
            self._by_id[session.session_id] = session

    def _bind_state(self, session: ConnectSession, state: str) -> None:
        with self._lock:
            session.state = state
            self._by_state[state] = session

    def get_by_state(self, state: str) -> Optional[ConnectSession]:
        with self._lock:
            return self._by_state.get(state)

    def get_by_id(self, session_id: str) -> Optional[ConnectSession]:
        with self._lock:
            return self._by_id.get(session_id)

    def start_authorize(
        self, user_id: str, settings: Optional[MobileSettings] = None
    ) -> Dict[str, str]:
        """Begin a connect attempt and return ``{authorization_url, connect_session}``.

        Raises ``RuntimeError`` (mapped to HTTP errors by the router) when the
        public base URL is unconfigured, the MCP SDK is unavailable, or the
        provider fails to produce an authorization URL.
        """
        settings = settings or load_settings()
        callback_url = settings.callback_url
        if not callback_url:
            raise RuntimeError(
                "MOBILE_PUBLIC_BASE_URL is not set; cannot build a remote-HTTPS "
                "redirect for the server-mediated OAuth flow."
            )

        broker = registry.get(user_id, settings)
        session = ConnectSession(session_id=uuid.uuid4().hex, user_id=user_id)
        self._register(session)

        provider = build_oauth_provider(
            server_url=broker.cfg.mcp_url,
            storage=broker.storage,
            redirect_uri=callback_url,
            redirect_handler=session.make_redirect_handler(),
            callback_handler=session.make_callback_handler(),
        )

        def _worker() -> None:
            try:
                status = broker.connect(oauth_provider=provider, timeout=CALLBACK_TIMEOUT + 60)
                session.connected = bool(status.get("connected"))
                if not session.connected:
                    session.error = status.get("error") or "connection failed"
            except Exception as exc:  # noqa: BLE001 — surface, don't crash the thread
                session.error = str(exc)
                logger.warning("mobile OAuth connect failed for %s: %s", user_id, exc)
            finally:
                session.done.set()

        threading.Thread(
            target=_worker, name=f"mobile-oauth-{session.session_id[:8]}", daemon=True
        ).start()

        # Wait for the authorization URL, but fail fast if the worker errors out
        # before producing one (disabled, SDK missing, metadata/DCR error).
        deadline = time.time() + AUTH_URL_TIMEOUT
        while not session.auth_url_event.wait(0.1):
            if session.done.is_set():
                raise RuntimeError(
                    session.error or "could not start Robinhood authorization"
                )
            if time.time() > deadline:
                raise RuntimeError("timed out starting Robinhood authorization")

        auth_url = session.auth_url or ""
        state = _parse_state(auth_url)
        if state:
            self._bind_state(session, state)

        return {"authorization_url": auth_url, "connect_session": session.session_id}

    def complete_callback(
        self, code: Optional[str], state: Optional[str], error: Optional[str]
    ) -> Dict[str, object]:
        """Feed Robinhood's redirect into the waiting session and await exchange.

        Returns ``{ok, connected, error?}``. Unknown/expired ``state`` yields
        ``ok=False`` so the router can still bounce the app back with an error.
        """
        if not state:
            return {"ok": False, "connected": False, "error": "missing state"}
        session = self.get_by_state(state)
        if session is None:
            return {"ok": False, "connected": False, "error": "unknown or expired session"}

        session.code = code
        session.cb_error = error
        session.callback_event.set()

        session.done.wait(timeout=EXCHANGE_TIMEOUT)
        if session.connected:
            return {"ok": True, "connected": True}
        return {"ok": False, "connected": False, "error": session.error or "exchange pending"}


def _parse_state(authorization_url: str) -> Optional[str]:
    try:
        qs = parse_qs(urlparse(authorization_url).query)
        vals = qs.get("state")
        return vals[0] if vals else None
    except Exception:  # noqa: BLE001
        return None


# Process-wide manager used by the v2 router.
sessions = ConnectSessionManager()
