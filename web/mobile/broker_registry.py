"""Per-user Robinhood broker isolation for the mobile (v2) API.

The legacy web app uses a single process-wide broker
(``web.server.get_broker()``) over one shared token file. That is fine for a
single-user desktop deployment but unsafe for many users: one user's tokens
would serve another, and one ``connect()`` would mutate shared connection
state for everyone.

This registry adds per-user isolation **alongside** (never replacing) the
legacy singleton:

* each ``user_id`` gets its own :class:`RobinhoodBroker` instance, and
* each user's OAuth tokens live in their own file
  (``<MOBILE_TOKEN_DIR>/<safe_user_id>.json``), so users never share
  credentials.

Only the v2 endpoints use this registry; the web app's ``get_broker()`` path is
untouched.
"""

from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

from tradingagents.brokers import RobinhoodBroker, load_robinhood_config
from tradingagents.default_config import DEFAULT_CONFIG
from web.mobile.fake_broker import build_fake_broker
from web.mobile.settings import MobileSettings, load_settings

# Evict brokers untouched for this long to bound memory on a long-lived process.
IDLE_TTL_SECONDS = 3600

_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_.-]")


def _safe_user_id(user_id: str) -> str:
    """Sanitise a user id for use as a filename component (no path traversal)."""
    cleaned = _SAFE_ID_RE.sub("_", user_id or "")
    return cleaned or "anonymous"


def user_token_path(user_id: str, settings: Optional[MobileSettings] = None) -> str:
    settings = settings or load_settings()
    return os.path.join(settings.token_dir, f"{_safe_user_id(user_id)}.json")


def build_user_broker(user_id: str, settings: Optional[MobileSettings] = None) -> RobinhoodBroker:
    """Construct a broker whose token storage is scoped to ``user_id``.

    Resolution mirrors the legacy path (packaged defaults → DEFAULT_CONFIG →
    env), but the token path is then **forced** to the per-user file so a global
    ``TRADINGAGENTS_ROBINHOOD_TOKEN_PATH`` env can't collapse all users onto one
    shared token store.
    """
    settings = settings or load_settings()
    cfg = load_robinhood_config(DEFAULT_CONFIG)
    cfg.token_storage_path = user_token_path(user_id, settings)
    # The mobile broker layer is itself opt-in (MOBILE_API_ENABLED). Enable the
    # broker for this per-user instance so the server-mediated OAuth connect can
    # proceed without also requiring the global TRADINGAGENTS_ROBINHOOD_ENABLED
    # flag (which governs the legacy web/singleton path). Trade-execution safety
    # gates (dry_run / trade_mode) are unaffected and still default safe.
    cfg.enabled = True
    # Test/dev only (default OFF): swap in an in-memory fake broker so the
    # trading UI can be exercised without live Robinhood. The cfg — and thus the
    # trade-mode/dry_run safety gates — is identical; only the MCP transport is
    # faked, and even a LIVE order never leaves the building (see fake_broker.py).
    if settings.fake_broker:
        return build_fake_broker(cfg)
    return RobinhoodBroker(cfg)


@dataclass
class _Entry:
    broker: RobinhoodBroker
    last_access: float


@dataclass
class BrokerRegistry:
    """Lock-guarded ``user_id -> RobinhoodBroker`` map with idle eviction."""

    idle_ttl: float = IDLE_TTL_SECONDS
    _entries: Dict[str, _Entry] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _evict_stale(self, now: float) -> None:
        stale = [uid for uid, e in self._entries.items() if (now - e.last_access) > self.idle_ttl]
        for uid in stale:
            self._entries.pop(uid, None)

    def get(self, user_id: str, settings: Optional[MobileSettings] = None) -> RobinhoodBroker:
        now = time.time()
        with self._lock:
            self._evict_stale(now)
            entry = self._entries.get(user_id)
            if entry is None:
                entry = _Entry(broker=build_user_broker(user_id, settings), last_access=now)
                self._entries[user_id] = entry
            else:
                entry.last_access = now
            return entry.broker

    def drop(self, user_id: str) -> None:
        with self._lock:
            self._entries.pop(user_id, None)


# Process-wide registry used by the v2 endpoints (separate from the legacy
# singleton in web/server.py).
registry = BrokerRegistry()
