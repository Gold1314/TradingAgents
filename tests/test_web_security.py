"""Web-layer access control: operator-only Robinhood endpoints, owner-only cost overrides,
fail-closed quota, and blueprint tokens that are not valid mobile tokens."""

import threading

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

import web.server as server  # noqa: E402
from web.mobile.auth import _verify_dev_token  # noqa: E402


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(server, "ADMIN_PASSWORD", "pw")
    monkeypatch.setenv("TRADINGAGENTS_ROBINHOOD_ENABLED", "true")
    return TestClient(server.app)


@pytest.mark.unit
@pytest.mark.parametrize(
    "method,path,body",
    [
        ("post", "/api/robinhood/connect", None),
        ("get", "/api/robinhood/account", None),
        ("post", "/api/robinhood/orders/some-run", {}),
    ],
)
def test_robinhood_endpoints_require_admin(client, method, path, body):
    kw = {"json": body} if body is not None else {}
    assert getattr(client, method)(path, **kw).status_code == 401
    assert getattr(client, method)(path, headers={"X-Admin-Password": "nope"}, **kw).status_code == 401


@pytest.mark.unit
def test_non_owner_overrides_are_dropped(client, monkeypatch):
    seen, ran = {}, threading.Event()
    monkeypatch.setattr(server.db, "get_cache_enabled", lambda: False)
    monkeypatch.setattr(server, "_quota_state", lambda meta, owner: {"allowed": True})
    monkeypatch.setattr(
        server, "_run_pipeline", lambda run, req: (seen.update(req=req, owner=run.owner), ran.set())
    )
    r = client.post(
        "/api/runs",
        json={"ticker": " nvda ", "trade_date": "2026-01-15", "deep_model": "huge", "max_debate_rounds": 50},
    )
    assert r.status_code == 200
    assert ran.wait(5)
    assert seen["req"].ticker == "NVDA"
    assert seen["req"].deep_model is None and seen["req"].max_debate_rounds is None
    assert seen["owner"] is False


@pytest.mark.unit
def test_quota_fails_closed_on_db_error(monkeypatch):
    monkeypatch.setattr(server, "RUN_LIMIT_PER_USER", 1)

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(server.db, "count_quota_consumed", boom)
    assert server._quota_state({"visitor_id": "v"}, owner=False)["allowed"] is False


@pytest.mark.unit
def test_blueprint_token_is_not_a_mobile_token():
    token = server._mint_blueprint_token("a@b.co", "secret")
    assert server._verify_blueprint_token(token, "secret") == "a@b.co"
    assert _verify_dev_token(token, "secret") is None


@pytest.mark.unit
def test_filter_values_are_restricted():
    from web.db import _FILTER_SAFE

    assert _FILTER_SAFE.fullmatch("203.0.113.7")
    assert _FILTER_SAFE.fullmatch("2001:db8::1")
    assert not _FILTER_SAFE.fullmatch("x,ip_address.neq.0")
    assert not _FILTER_SAFE.fullmatch("v)")


@pytest.mark.unit
def test_non_ascii_admin_header_is_401_not_500(client):
    r = client.get("/api/robinhood/account", headers={"X-Admin-Password": b"p\xe9"})
    assert r.status_code == 401
