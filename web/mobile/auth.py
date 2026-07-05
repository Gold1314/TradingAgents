"""Per-user identity for the mobile (v2) API.

Provides a FastAPI ``current_user`` dependency that resolves a stable
``user_id`` from an ``Authorization: Bearer <token>`` header. Two verification
modes are supported, selected by ``MOBILE_AUTH_MODE``:

* ``"dev"`` (default) — a **minimal, self-contained** HMAC-signed token layer.
  Tokens are minted by :func:`mint_dev_token` and verified locally. This is
  intentionally simple so the v2 surface can be exercised end-to-end without a
  real identity provider wired up.

  The production identity provider is the ``"supabase"`` mode below; switching
  to it is a pure config change (``MOBILE_AUTH_MODE=supabase`` + Supabase env).
  Dev mode must NOT be used in production: anyone who knows the signing secret
  can mint a token for any ``user_id``.

* ``"supabase"`` — the production identity provider. Verifies a Supabase Auth
  (GoTrue) access token: signature, ``exp``/``nbf``/``iat``, audience (``aud``,
  typically ``authenticated``) and issuer (``iss``). Both signing schemes
  Supabase uses are supported:

  - **HS256** with the project's legacy symmetric JWT secret
    (``SUPABASE_JWT_SECRET``).
  - **RS256 / ES256** with the project's asymmetric signing keys, fetched and
    cached from the JWKS endpoint
    (``<SUPABASE_URL>/auth/v1/.well-known/jwks.json``). Keys are cached by
    ``kid`` with a TTL and refreshed on rotation / unknown ``kid``.

  The verification algorithm is selected from configuration, not blindly from
  the token header: HS256 tokens are only ever checked against the symmetric
  secret and asymmetric tokens only against JWKS public keys. This closes the
  classic key-confusion attack (signing an HS256 token with a public key).
  ``alg: none`` and unknown algorithms are always rejected.

  Requires PyJWT with the crypto extra (``pip install 'pyjwt[crypto]'``); if it
  is not installed the dependency fails closed with a 503.

This module is only imported when the mobile API is enabled, but it has no
import-time side effects and is safe to import in tests.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import threading
import time
import urllib.request
from dataclasses import dataclass
from typing import Dict, Optional

import anyio
from fastapi import Header, HTTPException

from web.mobile.settings import MobileSettings, load_settings

logger = logging.getLogger("tradingagents.web.mobile.auth")

try:  # optional — only needed for MOBILE_AUTH_MODE=supabase
    import jwt as _pyjwt  # PyJWT

    PYJWT_AVAILABLE = True
except Exception:  # noqa: BLE001 — absence simply disables the supabase path
    PYJWT_AVAILABLE = False

DEV_TOKEN_TTL = 30 * 24 * 3600  # 30 days — long-lived dev convenience token

# Asymmetric algorithms accepted on the JWKS path. Symmetric (HS*) tokens never
# reach this list, and "none" is never accepted on any path.
_ASYMMETRIC_ALGS = ("RS256", "RS384", "RS512", "ES256", "ES384", "ES512")

# Small leeway (seconds) for exp/nbf/iat to tolerate minor clock skew.
_JWT_LEEWAY = 30

# JWKS document cache (keyed by URL → kid → key) with a TTL. Public keys rotate
# rarely, so a short TTL plus an on-miss refresh is enough to follow rotation
# without hammering the endpoint on every request.
JWKS_CACHE_TTL = 600.0  # 10 minutes
JWKS_FETCH_TIMEOUT = 5.0  # seconds


@dataclass
class _JWKSEntry:
    keys: Dict[str, object]  # kid -> PyJWK
    fetched_at: float


_jwks_cache: Dict[str, _JWKSEntry] = {}
_jwks_lock = threading.Lock()


@dataclass(frozen=True)
class User:
    """The authenticated principal a v2 endpoint operates on behalf of."""

    user_id: str
    email: Optional[str] = None
    role: Optional[str] = None


def _dev_secret(settings: MobileSettings) -> Optional[str]:
    """Signing secret for dev tokens.

    Requires a dedicated secret: an explicit ``MOBILE_AUTH_SECRET`` or, failing
    that, the admin password. The Supabase *service-role* key
    (``SUPABASE_KEY``) is deliberately NOT reused — it grants full database
    access, so a token-signing bug must not be able to expose it. Returns
    ``None`` when nothing is available, in which case dev auth fails closed.
    """
    return (
        settings.auth_secret
        or os.environ.get("STOCKAGENTS_ADMIN_PASSWORD")
        or None
    )


def mint_dev_token(user_id: str, secret: str, ttl: int = DEV_TOKEN_TTL) -> str:
    """Mint a dev-mode bearer token for ``user_id`` (HMAC-SHA256, base64url)."""
    exp = int(time.time()) + ttl
    msg = f"{user_id}|{exp}"
    sig = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{msg}|{sig}".encode()).decode()


def _verify_dev_token(token: str, secret: str) -> Optional[User]:
    if not token or not secret:
        return None
    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode()
        user_id, exp, sig = raw.rsplit("|", 2)
        expected = hmac.new(secret.encode(), f"{user_id}|{exp}".encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        if int(exp) < int(time.time()):
            return None
        if not user_id:
            return None
        return User(user_id=user_id)
    except Exception:  # noqa: BLE001 — any malformed token is simply invalid
        return None


def _fetch_jwks(url: str, timeout: float = JWKS_FETCH_TIMEOUT) -> dict:
    """Fetch the raw JWKS document. Isolated so tests can stub it (no network).

    The JWKS endpoint is a fixed, public Supabase URL. We first try the default
    opener (which honours a genuinely-required corporate/HTTP proxy), and on any
    failure retry once through a proxy-disabled opener. This keeps token
    verification working on machines where a VPN/proxy 403s the CONNECT tunnel to
    ``*.supabase.co`` but direct egress is available — the failure mode observed
    as ``Tunnel connection failed: 403 Forbidden``.
    """
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — fixed config URL
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 — retry once without any proxy
        logger.info("JWKS fetch via default opener failed (%s); retrying without proxy", exc)
        no_proxy_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with no_proxy_opener.open(req, timeout=timeout) as resp:  # noqa: S310 — fixed config URL
            return json.loads(resp.read().decode("utf-8"))


def _load_jwks_entry(url: str) -> _JWKSEntry:
    raw = _fetch_jwks(url)
    jwk_set = _pyjwt.PyJWKSet.from_dict(raw)
    keys: Dict[str, object] = {}
    for key in jwk_set.keys:
        if key.key_id:
            keys[key.key_id] = key
    return _JWKSEntry(keys=keys, fetched_at=time.time())


def _select_key(entry: _JWKSEntry, kid: Optional[str]):
    if kid is not None:
        return entry.keys[kid]  # KeyError → caller treats as unknown key
    # No kid in the header: only safe when the set has exactly one key.
    if len(entry.keys) == 1:
        return next(iter(entry.keys.values()))
    raise KeyError("jwt has no kid and JWKS has multiple keys")


def _get_signing_key(url: str, kid: Optional[str], ttl: float = JWKS_CACHE_TTL):
    """Return the PyJWK for ``kid`` from ``url``, using the TTL cache.

    Refreshes the cache when it is stale or when ``kid`` is not present (key
    rotation). Raises ``KeyError`` if the key is still unknown after a refresh.
    """
    now = time.time()
    with _jwks_lock:
        entry = _jwks_cache.get(url)
        if entry is not None and (now - entry.fetched_at) < ttl:
            try:
                return _select_key(entry, kid)
            except KeyError:
                pass  # fall through to a refresh: the key may have rotated in
    # Refresh outside/after the fast path. Fetch under the lock to avoid a
    # stampede; request volume here is low (one network call per rotation/TTL).
    with _jwks_lock:
        entry = _load_jwks_entry(url)
        _jwks_cache[url] = entry
        return _select_key(entry, kid)


def reset_jwks_cache() -> None:
    """Clear the JWKS cache (used by tests; cheap and side-effect free)."""
    with _jwks_lock:
        _jwks_cache.clear()


def _verify_supabase_jwt(token: str, settings: MobileSettings) -> Optional[User]:
    """Verify a Supabase access token. Returns ``None`` for any invalid token.

    Raises ``RuntimeError`` only for a misconfiguration the caller surfaces as a
    503 (PyJWT not installed).
    """
    if not PYJWT_AVAILABLE:
        raise RuntimeError(
            "MOBILE_AUTH_MODE=supabase requires PyJWT with the crypto extra. "
            "Install it with: pip install 'pyjwt[crypto]'"
        )

    try:
        header = _pyjwt.get_unverified_header(token)
    except Exception as exc:  # noqa: BLE001 — malformed token
        logger.info("supabase jwt header parse failed: %s", exc)
        return None

    alg = header.get("alg")
    if not alg or alg.lower() == "none":
        # Never accept unsigned tokens.
        return None

    if alg == "HS256":
        secret = settings.supabase_jwt_secret
        if not secret:
            # An HS256 token but no symmetric secret configured: we have no safe
            # key for it. Reject rather than fall back to a public key (which
            # would enable the HS/RS key-confusion attack).
            logger.info("supabase HS256 token received but SUPABASE_JWT_SECRET is unset")
            return None
        key: object = secret
        algorithms = ["HS256"]
    elif alg in _ASYMMETRIC_ALGS:
        jwks_url = settings.supabase_jwks_effective_url
        if not jwks_url:
            logger.info("supabase %s token received but JWKS is not configured", alg)
            return None
        try:
            signing_key = _get_signing_key(jwks_url, header.get("kid"))
        except KeyError:
            logger.info("supabase jwt kid not found in JWKS: %s", header.get("kid"))
            return None
        except Exception as exc:  # noqa: BLE001 — JWKS fetch/parse failure
            logger.warning("supabase JWKS fetch failed: %s", exc)
            return None
        key = signing_key.key
        algorithms = [alg]  # asymmetric only — never HS* on this path
    else:
        logger.info("supabase jwt uses unsupported alg: %s", alg)
        return None

    aud = settings.supabase_jwt_aud or None
    issuer = settings.supabase_issuer
    options = {
        "require": ["exp"],
        "verify_aud": bool(aud),
    }
    try:
        claims = _pyjwt.decode(
            token,
            key,
            algorithms=algorithms,
            audience=aud,
            issuer=issuer,
            leeway=_JWT_LEEWAY,
            options=options,
        )
    except Exception as exc:  # noqa: BLE001 — invalid/expired/wrong-aud/wrong-iss
        logger.info("supabase jwt verification failed: %s", exc)
        return None

    user_id = claims.get("sub")
    if not user_id:
        return None
    return User(user_id=str(user_id), email=claims.get("email"), role=claims.get("role"))


def _extract_bearer(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


def resolve_user(authorization: Optional[str], settings: Optional[MobileSettings] = None) -> User:
    """Verify the bearer token and return the :class:`User`, or raise 401/503.

    Factored out of the FastAPI dependency so it can be unit-tested directly.
    """
    settings = settings or load_settings()
    token = _extract_bearer(authorization)
    if not token:
        raise HTTPException(
            status_code=401,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if settings.auth_mode == "supabase":
        if not settings.supabase_auth_configured:
            # Neither an HS256 secret nor a JWKS endpoint is available — this is
            # a server misconfiguration, not a bad token.
            raise HTTPException(status_code=503, detail="supabase auth is not configured")
        try:
            user = _verify_supabase_jwt(token, settings)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    else:  # "dev" (default)
        secret = _dev_secret(settings)
        if not secret:
            raise HTTPException(status_code=503, detail="mobile auth is not configured")
        user = _verify_dev_token(token, secret)

    if user is None:
        raise HTTPException(
            status_code=401,
            detail="invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def current_user(authorization: Optional[str] = Header(default=None)) -> User:
    """FastAPI dependency: the authenticated user for a v2 request.

    ``resolve_user`` can perform a blocking JWKS HTTP GET (supabase mode, on a
    cache miss), so run it in a worker thread to keep the event loop responsive.
    """
    return await anyio.to_thread.run_sync(resolve_user, authorization)
