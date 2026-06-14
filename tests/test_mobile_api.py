"""Tests for the additive mobile (v2) API layer (web/mobile/).

These cover the parts that matter without any network/live-broker access:
the default-OFF gating, the dev auth token layer + ``current_user`` dependency,
the per-user rate limiter, per-user broker/token isolation, and the v2 router
wiring (auth enforcement, 429 quota, run creation delegation, Robinhood
status/authorize/callback) against a *fake* server module so the heavy pipeline
and live OAuth are never invoked.
"""

import os
import types

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from web.mobile import include_mobile_api, mobile_api_enabled
from web.mobile.auth import mint_dev_token, resolve_user
from web.mobile.broker_registry import (
    BrokerRegistry,
    build_user_broker,
    user_token_path,
)
from web.mobile.oauth_flow import ConnectSessionManager, _parse_state, sessions
from web.mobile.rate_limit import RateLimiter
from web.mobile.router import build_mobile_router
from web.mobile.settings import load_settings


# ── master gate (default OFF) ──────────────────────────────────────────────
@pytest.mark.unit
class TestGating:
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("MOBILE_API_ENABLED", raising=False)
        assert mobile_api_enabled() is False

    def test_include_is_noop_when_disabled(self, monkeypatch):
        monkeypatch.delenv("MOBILE_API_ENABLED", raising=False)
        app = FastAPI()
        before = len(app.routes)
        assert include_mobile_api(app) is False
        assert len(app.routes) == before  # nothing registered

    @pytest.mark.parametrize("val", ["true", "1", "YES", "on"])
    def test_enabled_values(self, monkeypatch, val):
        monkeypatch.setenv("MOBILE_API_ENABLED", val)
        assert mobile_api_enabled() is True


# ── dev auth tokens ────────────────────────────────────────────────────────
@pytest.mark.unit
class TestAuth:
    def test_dev_token_roundtrip(self, monkeypatch):
        monkeypatch.setenv("MOBILE_AUTH_SECRET", "s3cret")
        token = mint_dev_token("user-123", "s3cret")
        user = resolve_user(f"Bearer {token}", load_settings())
        assert user.user_id == "user-123"

    def test_missing_token_is_401(self, monkeypatch):
        monkeypatch.setenv("MOBILE_AUTH_SECRET", "s3cret")
        with pytest.raises(HTTPException) as exc:
            resolve_user(None, load_settings())
        assert exc.value.status_code == 401

    def test_bad_signature_is_401(self, monkeypatch):
        monkeypatch.setenv("MOBILE_AUTH_SECRET", "s3cret")
        token = mint_dev_token("user-123", "different-secret")
        with pytest.raises(HTTPException) as exc:
            resolve_user(f"Bearer {token}", load_settings())
        assert exc.value.status_code == 401

    def test_expired_token_is_401(self, monkeypatch):
        monkeypatch.setenv("MOBILE_AUTH_SECRET", "s3cret")
        token = mint_dev_token("user-123", "s3cret", ttl=-10)
        with pytest.raises(HTTPException) as exc:
            resolve_user(f"Bearer {token}", load_settings())
        assert exc.value.status_code == 401

    def test_no_secret_configured_is_503(self, monkeypatch):
        for var in ("MOBILE_AUTH_SECRET", "SUPABASE_KEY", "STOCKAGENTS_ADMIN_PASSWORD"):
            monkeypatch.delenv(var, raising=False)
        with pytest.raises(HTTPException) as exc:
            resolve_user("Bearer whatever", load_settings())
        assert exc.value.status_code == 503

    def test_supabase_mode_without_secret_is_503(self, monkeypatch):
        monkeypatch.setenv("MOBILE_AUTH_MODE", "supabase")
        # Neither verification path configured (no HS256 secret, no JWKS).
        for var in (
            "SUPABASE_JWT_SECRET",
            "SUPABASE_URL",
            "SUPABASE_JWKS_URL",
        ):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("SUPABASE_JWKS_ENABLED", "false")
        with pytest.raises(HTTPException) as exc:
            resolve_user("Bearer token", load_settings())
        assert exc.value.status_code == 503


# ── rate limiter ───────────────────────────────────────────────────────────
@pytest.mark.unit
class TestRateLimiter:
    def test_allows_up_to_hourly_quota_then_blocks(self):
        rl = RateLimiter(per_hour=2, per_day=100)
        assert rl.check_and_consume("u") is None
        assert rl.check_and_consume("u") is None
        retry = rl.check_and_consume("u")
        assert retry is not None and retry > 0

    def test_daily_quota_independent_of_hour(self):
        rl = RateLimiter(per_hour=100, per_day=1)
        assert rl.check_and_consume("u") is None
        assert rl.check_and_consume("u") is not None  # day exhausted

    def test_per_user_isolation(self):
        rl = RateLimiter(per_hour=1, per_day=10)
        assert rl.check_and_consume("a") is None
        assert rl.check_and_consume("b") is None  # different user, own bucket
        assert rl.check_and_consume("a") is not None

    def test_zero_means_unlimited(self):
        rl = RateLimiter(per_hour=0, per_day=0)
        for _ in range(50):
            assert rl.check_and_consume("u") is None

    def test_reset(self):
        rl = RateLimiter(per_hour=1, per_day=10)
        rl.check_and_consume("u")
        assert rl.check_and_consume("u") is not None
        rl.reset("u")
        assert rl.check_and_consume("u") is None


# ── per-user broker / token isolation ──────────────────────────────────────
@pytest.mark.unit
class TestBrokerRegistry:
    def test_token_path_is_per_user_and_sanitised(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MOBILE_TOKEN_DIR", str(tmp_path))
        s = load_settings()
        p1 = user_token_path("alice", s)
        p2 = user_token_path("bob", s)
        assert p1 != p2
        assert p1.startswith(str(tmp_path))
        # path traversal is neutralised: the result stays a direct child of the
        # token dir (separators in the id are replaced, so no escape upward).
        evil = user_token_path("../../etc/passwd", s)
        assert os.path.dirname(os.path.abspath(evil)) == os.path.abspath(str(tmp_path))

    def test_build_user_broker_uses_per_user_token_path(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MOBILE_TOKEN_DIR", str(tmp_path))
        s = load_settings()
        broker = build_user_broker("alice", s)
        assert broker.cfg.token_storage_path == user_token_path("alice", s)
        assert broker.cfg.enabled is True  # mobile flow self-enables the broker

    def test_registry_returns_stable_instance_per_user(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MOBILE_TOKEN_DIR", str(tmp_path))
        s = load_settings()
        reg = BrokerRegistry()
        a1 = reg.get("alice", s)
        a2 = reg.get("alice", s)
        b1 = reg.get("bob", s)
        assert a1 is a2
        assert a1 is not b1

    def test_idle_eviction(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MOBILE_TOKEN_DIR", str(tmp_path))
        s = load_settings()
        reg = BrokerRegistry(idle_ttl=-1)  # everything is immediately stale
        a1 = reg.get("alice", s)
        a2 = reg.get("alice", s)  # prior entry evicted before this get
        assert a1 is not a2


# ── server-mediated OAuth session plumbing (no network) ────────────────────
@pytest.mark.unit
class TestOAuthFlow:
    def test_parse_state(self):
        url = "https://robinhood.com/oauth?response_type=code&state=abc123&client_id=x"
        assert _parse_state(url) == "abc123"
        assert _parse_state("https://x/y?no=state") is None
        assert _parse_state("not a url") is None

    def test_complete_callback_unknown_state(self):
        mgr = ConnectSessionManager()
        out = mgr.complete_callback("code", "no-such-state", None)
        assert out["ok"] is False
        assert out["connected"] is False

    def test_complete_callback_missing_state(self):
        mgr = ConnectSessionManager()
        out = mgr.complete_callback("code", None, None)
        assert out["ok"] is False


# ── v2 router wired onto a fake server module (no pipeline, no network) ─────
def _fake_server():
    class FakeManager:
        def __init__(self):
            self.emitted = []

        def create(self, loop, nodes):
            return types.SimpleNamespace(run_id="run-abc", analytics=None, nodes=nodes)

        def emit(self, run, event):
            self.emitted.append(event)

    return types.SimpleNamespace(
        RunRequest=lambda **kw: types.SimpleNamespace(**kw),
        _planned_nodes=lambda analysts: ["Market Analyst", "Portfolio Manager"],
        manager=FakeManager(),
        _run_pipeline=lambda run, req: None,  # never actually runs in tests
    )


def _client(monkeypatch, tmp_path, **env):
    monkeypatch.setenv("MOBILE_API_ENABLED", "true")
    monkeypatch.setenv("MOBILE_AUTH_SECRET", "s3cret")
    monkeypatch.setenv("MOBILE_TOKEN_DIR", str(tmp_path))
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    app = FastAPI()
    app.include_router(build_mobile_router(_fake_server()))
    return TestClient(app)


def _auth():
    return {"Authorization": f"Bearer {mint_dev_token('user-x', 's3cret')}"}


@pytest.mark.unit
class TestRouter:
    def test_runs_requires_auth(self, monkeypatch, tmp_path):
        client = _client(monkeypatch, tmp_path)
        r = client.post("/api/v2/runs", json={"ticker": "NVDA", "trade_date": "2026-06-12"})
        assert r.status_code == 401

    def test_runs_creates_run_with_auth(self, monkeypatch, tmp_path):
        client = _client(monkeypatch, tmp_path)
        r = client.post(
            "/api/v2/runs",
            headers=_auth(),
            json={"ticker": "NVDA", "trade_date": "2026-06-12"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["run_id"] == "run-abc"
        assert body["cached"] is False

    def test_runs_validates_ticker(self, monkeypatch, tmp_path):
        client = _client(monkeypatch, tmp_path)
        r = client.post(
            "/api/v2/runs", headers=_auth(), json={"ticker": "  ", "trade_date": "2026-06-12"}
        )
        assert r.status_code == 400

    def test_runs_rate_limited(self, monkeypatch, tmp_path):
        client = _client(monkeypatch, tmp_path, MOBILE_RUNS_PER_HOUR="1", MOBILE_RUNS_PER_DAY="100")
        payload = {"ticker": "NVDA", "trade_date": "2026-06-12"}
        first = client.post("/api/v2/runs", headers=_auth(), json=payload)
        assert first.status_code == 200
        second = client.post("/api/v2/runs", headers=_auth(), json=payload)
        assert second.status_code == 429
        assert "Retry-After" in second.headers

    def test_robinhood_status_per_user(self, monkeypatch, tmp_path):
        client = _client(monkeypatch, tmp_path)
        r = client.get("/api/v2/robinhood/status", headers=_auth())
        assert r.status_code == 200
        body = r.json()
        # public_status fields are present; not connected, no saved creds.
        assert body["connected"] is False
        assert body["has_saved_credentials"] is False

    def test_authorize_requires_public_base_url(self, monkeypatch, tmp_path):
        client = _client(monkeypatch, tmp_path)  # MOBILE_PUBLIC_BASE_URL unset
        r = client.get("/api/v2/robinhood/authorize", headers=_auth())
        assert r.status_code == 503

    def test_authorize_returns_url(self, monkeypatch, tmp_path):
        client = _client(
            monkeypatch, tmp_path, MOBILE_PUBLIC_BASE_URL="https://api.example.com"
        )
        # Stub the (network-bound) session start so we don't hit Robinhood.
        monkeypatch.setattr(
            sessions,
            "start_authorize",
            lambda user_id, settings: {
                "authorization_url": "https://robinhood.com/oauth?state=xyz",
                "connect_session": "sess-1",
            },
        )
        r = client.get("/api/v2/robinhood/authorize", headers=_auth())
        assert r.status_code == 200
        assert r.json()["connect_session"] == "sess-1"

    def test_callback_redirects_to_app_on_unknown_session(self, monkeypatch, tmp_path):
        client = _client(
            monkeypatch,
            tmp_path,
            MOBILE_OAUTH_APP_REDIRECT="stockagents://oauth/robinhood",
        )
        r = client.get(
            "/api/v2/robinhood/callback?code=c&state=unknown",
            follow_redirects=False,
        )
        assert r.status_code == 302
        loc = r.headers["location"]
        assert loc.startswith("stockagents://oauth/robinhood")
        assert "status=error" in loc

    def test_callback_redirects_ok_on_success(self, monkeypatch, tmp_path):
        client = _client(monkeypatch, tmp_path)
        monkeypatch.setattr(
            sessions,
            "complete_callback",
            lambda code, state, error: {"ok": True, "connected": True},
        )
        r = client.get(
            "/api/v2/robinhood/callback?code=c&state=s", follow_redirects=False
        )
        assert r.status_code == 302
        assert "status=ok" in r.headers["location"]
