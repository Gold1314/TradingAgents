"""Environment-gated settings for the additive mobile (iOS) API.

Everything the mobile layer does is gated behind :func:`mobile_api_enabled`
(``MOBILE_API_ENABLED``), which defaults to **OFF**. With the flag unset the
web app behaves byte-for-byte as it does today: the mobile router is never
registered, no auth is enforced, no rate limiting runs, and the legacy
``get_broker()`` singleton path is the only broker path in play.

All values are read from the environment **at call time** (not import time) so
tests and operators can flip them without reimporting the process.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

# Master switch. When false (default) nothing in web/mobile/ is wired up.
ENV_ENABLED = "MOBILE_API_ENABLED"

# Auth mode: "dev" (minimal HMAC tokens, default) or "supabase" (verify a
# Supabase-issued JWT). See web/mobile/auth.py.
ENV_AUTH_MODE = "MOBILE_AUTH_MODE"
ENV_AUTH_SECRET = "MOBILE_AUTH_SECRET"  # HMAC secret for dev-mode tokens

# Supabase Auth (GoTrue) JWT verification — the production IdP path.
#   SUPABASE_URL          — project base URL (reused from web/db.py convention);
#                           the JWT issuer and JWKS endpoint are derived from it.
#   SUPABASE_JWT_SECRET   — legacy symmetric (HS256) signing secret.
#   SUPABASE_JWT_AUD      — expected audience (default "authenticated").
#   SUPABASE_JWT_ISSUER   — expected issuer; default "<SUPABASE_URL>/auth/v1".
#   SUPABASE_JWKS_ENABLED — enable asymmetric (RS256/ES256) verification via the
#                           project JWKS endpoint (default ON when SUPABASE_URL
#                           is set; required for newer Supabase signing keys).
#   SUPABASE_JWKS_URL     — explicit JWKS URL override (otherwise derived).
ENV_SUPABASE_URL = "SUPABASE_URL"
ENV_SUPABASE_JWT_SECRET = "SUPABASE_JWT_SECRET"  # HS256 secret for supabase mode
ENV_SUPABASE_JWT_AUD = "SUPABASE_JWT_AUD"
ENV_SUPABASE_JWT_ISSUER = "SUPABASE_JWT_ISSUER"
ENV_SUPABASE_JWKS_ENABLED = "SUPABASE_JWKS_ENABLED"
ENV_SUPABASE_JWKS_URL = "SUPABASE_JWKS_URL"

# Supabase mounts GoTrue under /auth/v1; the issuer and the JWKS document live
# at well-known paths beneath it.
SUPABASE_AUTH_PATH = "/auth/v1"
SUPABASE_JWKS_PATH = "/auth/v1/.well-known/jwks.json"
DEFAULT_JWT_AUD = "authenticated"

# Per-user run quota (LLM-consuming endpoint). Fixed-window counters.
ENV_RUNS_PER_HOUR = "MOBILE_RUNS_PER_HOUR"
ENV_RUNS_PER_DAY = "MOBILE_RUNS_PER_DAY"

# Per-user order quota (LIVE-trading endpoint). Independent of the run quota so
# order spam can't be used to drain credits or hammer the broker.
ENV_ORDERS_PER_HOUR = "MOBILE_ORDERS_PER_HOUR"
ENV_ORDERS_PER_DAY = "MOBILE_ORDERS_PER_DAY"

# Server-mediated Robinhood OAuth.
ENV_PUBLIC_BASE_URL = "MOBILE_PUBLIC_BASE_URL"  # e.g. https://api.example.com
ENV_APP_REDIRECT = "MOBILE_OAUTH_APP_REDIRECT"  # app deep link after callback
ENV_TOKEN_DIR = "MOBILE_TOKEN_DIR"  # per-user broker token storage directory

# Test-only in-memory fake broker (default OFF). When on, the per-user broker
# registry hands back an in-memory :class:`~web.mobile.fake_broker.FakeBroker`
# instead of a live ``RobinhoodBroker`` — so the trading UI (connect status,
# account snapshot, order ticket + biometric gate) can be exercised in the
# Simulator / tests **without** live Robinhood. It never touches the network
# and, even with ``dry_run=false``, never places a real order. Intended for
# local/dev only; do NOT set it in production.
ENV_FAKE_BROKER = "MOBILE_FAKE_BROKER"

_TRUTHY = ("1", "true", "yes", "on")

# Path the v2 callback is mounted at; kept here so the redirect URI and the
# route stay in lockstep.
CALLBACK_PATH = "/api/v2/robinhood/callback"


def _env(name: str) -> Optional[str]:
    raw = os.environ.get(name)
    if raw is None:
        return None
    raw = raw.strip()
    return raw or None


def mobile_api_enabled() -> bool:
    """Master gate. Default OFF — the web app is unchanged unless flipped on."""
    return (os.environ.get(ENV_ENABLED, "").strip().lower() in _TRUTHY)


def _int_env(name: str, default: int) -> int:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _bool_env(name: str, default: bool) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    return raw.lower() in _TRUTHY


@dataclass(frozen=True)
class MobileSettings:
    """Resolved snapshot of the mobile-API configuration."""

    enabled: bool
    auth_mode: str
    auth_secret: Optional[str]
    supabase_url: Optional[str]
    supabase_jwt_secret: Optional[str]
    supabase_jwt_aud: str
    supabase_jwt_issuer: Optional[str]
    supabase_jwks_enabled: bool
    supabase_jwks_url: Optional[str]
    runs_per_hour: int
    runs_per_day: int
    orders_per_hour: int
    orders_per_day: int
    public_base_url: Optional[str]
    app_redirect: str
    token_dir: str
    fake_broker: bool

    @property
    def supabase_issuer(self) -> Optional[str]:
        """Expected ``iss`` claim for Supabase tokens.

        Prefers an explicit ``SUPABASE_JWT_ISSUER``; otherwise derived from
        ``SUPABASE_URL`` as ``<url>/auth/v1`` (GoTrue's issuer). ``None`` when
        neither is configured, in which case the issuer is not asserted.
        """
        if self.supabase_jwt_issuer:
            return self.supabase_jwt_issuer
        if self.supabase_url:
            return self.supabase_url.rstrip("/") + SUPABASE_AUTH_PATH
        return None

    @property
    def supabase_jwks_effective_url(self) -> Optional[str]:
        """The JWKS endpoint used for asymmetric (RS256/ES256) verification.

        An explicit ``SUPABASE_JWKS_URL`` always wins. Otherwise, when JWKS is
        enabled and ``SUPABASE_URL`` is set, it is derived from the project URL.
        ``None`` disables the asymmetric path entirely.
        """
        if self.supabase_jwks_url:
            return self.supabase_jwks_url
        if self.supabase_jwks_enabled and self.supabase_url:
            return self.supabase_url.rstrip("/") + SUPABASE_JWKS_PATH
        return None

    @property
    def supabase_auth_configured(self) -> bool:
        """True when at least one verification path (HS256 secret or JWKS) is
        usable. Used to distinguish a misconfiguration (503) from a bad token."""
        return bool(self.supabase_jwt_secret) or bool(self.supabase_jwks_effective_url)

    @property
    def callback_url(self) -> Optional[str]:
        """The remote-HTTPS redirect URI Robinhood redirects back to.

        ``None`` when ``MOBILE_PUBLIC_BASE_URL`` is unset — the authorize
        endpoint then returns 503 rather than guessing a redirect.
        """
        if not self.public_base_url:
            return None
        return self.public_base_url.rstrip("/") + CALLBACK_PATH


def _default_token_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".tradingagents", "tokens")


def load_settings() -> MobileSettings:
    """Read the current mobile-API settings from the environment."""
    return MobileSettings(
        enabled=mobile_api_enabled(),
        auth_mode=(_env(ENV_AUTH_MODE) or "dev").lower(),
        auth_secret=_env(ENV_AUTH_SECRET),
        supabase_url=_env(ENV_SUPABASE_URL),
        supabase_jwt_secret=_env(ENV_SUPABASE_JWT_SECRET),
        supabase_jwt_aud=_env(ENV_SUPABASE_JWT_AUD) or DEFAULT_JWT_AUD,
        supabase_jwt_issuer=_env(ENV_SUPABASE_JWT_ISSUER),
        # JWKS defaults ON whenever a project URL is present, so newer Supabase
        # projects that use asymmetric signing keys verify out of the box.
        supabase_jwks_enabled=_bool_env(ENV_SUPABASE_JWKS_ENABLED, True),
        supabase_jwks_url=_env(ENV_SUPABASE_JWKS_URL),
        runs_per_hour=_int_env(ENV_RUNS_PER_HOUR, 10),
        runs_per_day=_int_env(ENV_RUNS_PER_DAY, 50),
        orders_per_hour=_int_env(ENV_ORDERS_PER_HOUR, 30),
        orders_per_day=_int_env(ENV_ORDERS_PER_DAY, 200),
        public_base_url=_env(ENV_PUBLIC_BASE_URL),
        app_redirect=_env(ENV_APP_REDIRECT) or "stockagents://oauth/robinhood",
        token_dir=os.path.expanduser(_env(ENV_TOKEN_DIR) or _default_token_dir()),
        fake_broker=_bool_env(ENV_FAKE_BROKER, False),
    )
