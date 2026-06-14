"""Tests for the production Supabase Auth (GoTrue) JWT path in web/mobile/auth.py.

Covers symmetric (HS256) verification and asymmetric (RS256 / JWKS) verification
with claim validation (signature, exp/nbf, aud, iss), the fail-closed behaviour
on bad/missing/unsigned tokens, the HS/RS key-confusion guard, and JWKS caching
+ key rotation. All crypto material is generated locally and the JWKS endpoint is
mocked — these tests never touch the network or require real Supabase creds.
"""

import base64
import json
import time

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from jwt.algorithms import RSAAlgorithm

from web.mobile import auth as auth_mod
from web.mobile.auth import User, resolve_user
from web.mobile.settings import load_settings

PROJECT_URL = "https://proj.supabase.co"
ISSUER = "https://proj.supabase.co/auth/v1"
JWKS_URL = "https://proj.supabase.co/auth/v1/.well-known/jwks.json"
HS_SECRET = "super-secret-jwt-signing-key"


def _claims(sub="user-uuid-123", *, iss=ISSUER, aud="authenticated", exp_in=3600, **extra):
    now = int(time.time())
    claims = {"sub": sub, "aud": aud, "iss": iss, "iat": now, "exp": now + exp_in}
    claims.update(extra)
    return claims


def _hs_token(claims, secret=HS_SECRET):
    return pyjwt.encode(claims, secret, algorithm="HS256")


def _unsigned_token(claims):
    """An alg=none token with an empty signature (must always be rejected)."""

    def b64(obj):
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64(claims)}."


@pytest.fixture(autouse=True)
def _clean_jwks_cache():
    auth_mod.reset_jwks_cache()
    yield
    auth_mod.reset_jwks_cache()


@pytest.fixture
def hs_env(monkeypatch):
    monkeypatch.setenv("MOBILE_AUTH_MODE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", PROJECT_URL)
    monkeypatch.setenv("SUPABASE_JWT_SECRET", HS_SECRET)
    # Keep the asymmetric path inert for the symmetric tests.
    monkeypatch.setenv("SUPABASE_JWKS_ENABLED", "false")
    monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    return load_settings


# ── HS256 (legacy symmetric secret) ────────────────────────────────────────
@pytest.mark.unit
class TestSupabaseHS256:
    def test_valid_token_accepted(self, hs_env):
        token = _hs_token(_claims(email="a@b.com", role="authenticated"))
        user = resolve_user(f"Bearer {token}", hs_env())
        assert isinstance(user, User)
        assert user.user_id == "user-uuid-123"
        assert user.email == "a@b.com"
        assert user.role == "authenticated"

    def test_missing_token_is_401(self, hs_env):
        with pytest.raises(HTTPException) as exc:
            resolve_user(None, hs_env())
        assert exc.value.status_code == 401

    def test_expired_token_is_401(self, hs_env):
        token = _hs_token(_claims(exp_in=-60))
        with pytest.raises(HTTPException) as exc:
            resolve_user(f"Bearer {token}", hs_env())
        assert exc.value.status_code == 401

    def test_not_yet_valid_token_is_401(self, hs_env):
        claims = _claims()
        claims["nbf"] = int(time.time()) + 3600
        token = _hs_token(claims)
        with pytest.raises(HTTPException) as exc:
            resolve_user(f"Bearer {token}", hs_env())
        assert exc.value.status_code == 401

    def test_bad_signature_is_401(self, hs_env):
        token = _hs_token(_claims(), secret="a-different-secret")
        with pytest.raises(HTTPException) as exc:
            resolve_user(f"Bearer {token}", hs_env())
        assert exc.value.status_code == 401

    def test_wrong_audience_is_401(self, hs_env):
        token = _hs_token(_claims(aud="not-authenticated"))
        with pytest.raises(HTTPException) as exc:
            resolve_user(f"Bearer {token}", hs_env())
        assert exc.value.status_code == 401

    def test_wrong_issuer_is_401(self, hs_env):
        token = _hs_token(_claims(iss="https://evil.example.com/auth/v1"))
        with pytest.raises(HTTPException) as exc:
            resolve_user(f"Bearer {token}", hs_env())
        assert exc.value.status_code == 401

    def test_alg_none_is_rejected(self, hs_env):
        token = _unsigned_token(_claims())
        with pytest.raises(HTTPException) as exc:
            resolve_user(f"Bearer {token}", hs_env())
        assert exc.value.status_code == 401

    def test_missing_sub_is_401(self, hs_env):
        claims = _claims()
        claims.pop("sub")
        token = _hs_token(claims)
        with pytest.raises(HTTPException) as exc:
            resolve_user(f"Bearer {token}", hs_env())
        assert exc.value.status_code == 401

    def test_custom_aud_and_issuer(self, monkeypatch):
        monkeypatch.setenv("MOBILE_AUTH_MODE", "supabase")
        monkeypatch.setenv("SUPABASE_JWT_SECRET", HS_SECRET)
        monkeypatch.setenv("SUPABASE_JWT_AUD", "my-app")
        monkeypatch.setenv("SUPABASE_JWT_ISSUER", "https://custom.issuer/auth")
        monkeypatch.setenv("SUPABASE_JWKS_ENABLED", "false")
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        token = _hs_token(_claims(aud="my-app", iss="https://custom.issuer/auth"))
        user = resolve_user(f"Bearer {token}", load_settings())
        assert user.user_id == "user-uuid-123"


# ── RS256 (asymmetric / JWKS) ──────────────────────────────────────────────
def _rsa_keypair():
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return priv, priv.public_key()


def _jwk_for(public_key, kid):
    jwk = json.loads(RSAAlgorithm.to_jwk(public_key))
    jwk.update({"kid": kid, "alg": "RS256", "use": "sig"})
    return jwk


def _rs_token(claims, private_key, kid):
    return pyjwt.encode(claims, private_key, algorithm="RS256", headers={"kid": kid})


@pytest.fixture
def rs_env(monkeypatch):
    monkeypatch.setenv("MOBILE_AUTH_MODE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", PROJECT_URL)
    monkeypatch.setenv("SUPABASE_JWKS_ENABLED", "true")
    # No symmetric secret: this is a JWKS-only (asymmetric) project.
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    return load_settings


@pytest.mark.unit
class TestSupabaseRS256:
    def test_valid_rs256_token_accepted(self, rs_env, monkeypatch):
        priv, pub = _rsa_keypair()
        jwks = {"keys": [_jwk_for(pub, "kid-1")]}
        captured = {"calls": 0}

        def fake_fetch(url, timeout=5.0):
            captured["calls"] += 1
            assert url == JWKS_URL
            return jwks

        monkeypatch.setattr(auth_mod, "_fetch_jwks", fake_fetch)
        token = _rs_token(_claims(email="rs@b.com"), priv, "kid-1")
        user = resolve_user(f"Bearer {token}", rs_env())
        assert user.user_id == "user-uuid-123"
        assert user.email == "rs@b.com"

        # Second request reuses the cache — no extra fetch.
        resolve_user(f"Bearer {token}", rs_env())
        assert captured["calls"] == 1

    def test_wrong_key_is_401(self, rs_env, monkeypatch):
        priv, _ = _rsa_keypair()
        _, other_pub = _rsa_keypair()  # JWKS publishes a DIFFERENT public key
        jwks = {"keys": [_jwk_for(other_pub, "kid-1")]}
        monkeypatch.setattr(auth_mod, "_fetch_jwks", lambda url, timeout=5.0: jwks)
        token = _rs_token(_claims(), priv, "kid-1")
        with pytest.raises(HTTPException) as exc:
            resolve_user(f"Bearer {token}", rs_env())
        assert exc.value.status_code == 401

    def test_unknown_kid_is_401(self, rs_env, monkeypatch):
        priv, pub = _rsa_keypair()
        jwks = {"keys": [_jwk_for(pub, "kid-1")]}
        monkeypatch.setattr(auth_mod, "_fetch_jwks", lambda url, timeout=5.0: jwks)
        token = _rs_token(_claims(), priv, "kid-does-not-exist")
        with pytest.raises(HTTPException) as exc:
            resolve_user(f"Bearer {token}", rs_env())
        assert exc.value.status_code == 401

    def test_expired_rs256_token_is_401(self, rs_env, monkeypatch):
        priv, pub = _rsa_keypair()
        jwks = {"keys": [_jwk_for(pub, "kid-1")]}
        monkeypatch.setattr(auth_mod, "_fetch_jwks", lambda url, timeout=5.0: jwks)
        token = _rs_token(_claims(exp_in=-60), priv, "kid-1")
        with pytest.raises(HTTPException) as exc:
            resolve_user(f"Bearer {token}", rs_env())
        assert exc.value.status_code == 401

    def test_key_rotation_triggers_refetch(self, rs_env, monkeypatch):
        priv_old, pub_old = _rsa_keypair()
        priv_new, pub_new = _rsa_keypair()
        state = {"jwks": {"keys": [_jwk_for(pub_old, "kid-old")]}, "calls": 0}

        def fake_fetch(url, timeout=5.0):
            state["calls"] += 1
            return state["jwks"]

        monkeypatch.setattr(auth_mod, "_fetch_jwks", fake_fetch)

        old_token = _rs_token(_claims(), priv_old, "kid-old")
        assert resolve_user(f"Bearer {old_token}", rs_env()).user_id == "user-uuid-123"
        assert state["calls"] == 1

        # Rotate: a token signed by a brand-new kid not yet in the cache must
        # force a refetch rather than fail outright.
        state["jwks"] = {"keys": [_jwk_for(pub_new, "kid-new")]}
        new_token = _rs_token(_claims(), priv_new, "kid-new")
        assert resolve_user(f"Bearer {new_token}", rs_env()).user_id == "user-uuid-123"
        assert state["calls"] == 2

    def test_hs256_token_rejected_when_only_jwks_configured(self, rs_env, monkeypatch):
        # Key-confusion guard: with no symmetric secret configured, an HS256
        # token (e.g. forged using the public key as the HMAC secret) must be
        # rejected, never verified against a JWKS public key.
        monkeypatch.setattr(
            auth_mod, "_fetch_jwks", lambda url, timeout=5.0: {"keys": []}
        )
        token = _hs_token(_claims())
        with pytest.raises(HTTPException) as exc:
            resolve_user(f"Bearer {token}", rs_env())
        assert exc.value.status_code == 401

    def test_jwks_fetch_failure_is_401(self, rs_env, monkeypatch):
        priv, _ = _rsa_keypair()

        def boom(url, timeout=5.0):
            raise OSError("network down")

        monkeypatch.setattr(auth_mod, "_fetch_jwks", boom)
        token = _rs_token(_claims(), priv, "kid-1")
        with pytest.raises(HTTPException) as exc:
            resolve_user(f"Bearer {token}", rs_env())
        assert exc.value.status_code == 401
