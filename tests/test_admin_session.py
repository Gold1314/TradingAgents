"""Tests for the admin session-cookie layer (web/server.py).

The admin password is exchanged (once) for a short-lived, signed, HttpOnly
session cookie via ``/api/admin/login`` — it is never stored client-side. These
tests cover the token mint/verify round trip and the login/cookie/logout flow,
including that the legacy ``X-Admin-Password`` header still works for
programmatic callers.

``web.server`` imports the full pipeline; skip cleanly where those heavy deps
(pandas/yfinance/langgraph/…) aren't installed, matching the rest of the suite
which never imports the full server.
"""

import importlib

import pytest

pytest.importorskip("pandas")
pytest.importorskip("yfinance")
pytest.importorskip("langgraph")


ADMIN_PW = "unit-test-admin-pw"


@pytest.fixture()
def server(monkeypatch):
    monkeypatch.setenv("STOCKAGENTS_ADMIN_PASSWORD", ADMIN_PW)
    # MOBILE_API defaults off; keep it off so the v2 layer isn't wired in.
    monkeypatch.delenv("MOBILE_API_ENABLED", raising=False)
    import web.server as srv

    importlib.reload(srv)  # re-read ADMIN_PASSWORD from the patched env
    return srv


@pytest.fixture()
def client(server):
    from fastapi.testclient import TestClient

    return TestClient(server.app)


def test_mint_verify_round_trip(server):
    tok = server._mint_admin_session()
    assert tok and server._verify_admin_session(tok)


def test_verify_rejects_garbage_and_tampering(server):
    tok = server._mint_admin_session()
    assert not server._verify_admin_session(None)
    assert not server._verify_admin_session("garbage")
    assert not server._verify_admin_session(tok + "x")  # signature no longer matches


def test_expired_session_is_rejected(server):
    expired = server._mint_admin_session(ttl=-10)
    assert not server._verify_admin_session(expired)


def test_session_invalidated_when_password_rotates(server, monkeypatch):
    tok = server._mint_admin_session()
    assert server._verify_admin_session(tok)
    monkeypatch.setattr(server, "ADMIN_PASSWORD", "a-different-password")
    assert not server._verify_admin_session(tok)


def test_login_wrong_password_401_no_cookie(client):
    r = client.post("/api/admin/login", json={"password": "nope"})
    assert r.status_code == 401
    assert "sa_admin_session" not in r.cookies


def test_login_sets_httponly_strict_cookie_and_unlocks(client):
    r = client.post("/api/admin/login", json={"password": ADMIN_PW})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and "cache_enabled" in body
    set_cookie = r.headers.get("set-cookie", "").lower()
    assert "httponly" in set_cookie
    assert "samesite=strict" in set_cookie

    # The cookie alone (no header) now authorizes admin endpoints.
    assert client.get("/api/admin/settings").status_code == 200


def test_cookie_grants_owner_quota_exemption(client):
    client.post("/api/admin/login", json={"password": ADMIN_PW})
    q = client.get("/api/runs/quota").json()
    assert q["owner"] is True


def test_logout_clears_session(client):
    client.post("/api/admin/login", json={"password": ADMIN_PW})
    assert client.get("/api/admin/settings").status_code == 200
    client.post("/api/admin/logout")
    client.cookies.clear()
    assert client.get("/api/admin/settings").status_code == 401


def test_legacy_header_still_authorizes(client):
    # Backward compat for CLI / programmatic callers.
    r = client.get("/api/admin/settings", headers={"X-Admin-Password": ADMIN_PW})
    assert r.status_code == 200
    r = client.get("/api/admin/settings", headers={"X-Admin-Password": "wrong"})
    assert r.status_code == 401


def test_admin_features_unconfigured_is_503(monkeypatch):
    monkeypatch.delenv("STOCKAGENTS_ADMIN_PASSWORD", raising=False)
    import web.server as srv

    importlib.reload(srv)
    from fastapi.testclient import TestClient

    c = TestClient(srv.app)
    assert c.post("/api/admin/login", json={"password": "anything"}).status_code == 503
    assert c.get("/api/admin/settings").status_code == 503
    # Restore for any later import of the module.
    monkeypatch.setenv("STOCKAGENTS_ADMIN_PASSWORD", ADMIN_PW)
    importlib.reload(srv)
