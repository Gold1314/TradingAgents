"""Tests for the per-user-safe v2 order endpoint (web/mobile/orders.py + router).

These cover the multi-user-safety contract without any live broker / network:

* auth is required (401 without a token);
* a run is rejected unless it belongs to the calling user;
* the order is rejected when the user's broker isn't connected;
* dry-run vs live is driven by the *server's* config, not the client;
* notional/quantity are re-clamped and the ticker is forced from the run;
* the per-user :class:`BrokerRegistry` is used, never ``get_broker()``;
* placement is idempotent (a double-submit doesn't fire twice).

The broker and executor MCP layer are faked so nothing reaches Robinhood.
"""

import threading
import types

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from tradingagents.brokers.config import RobinhoodConfig
from tradingagents.brokers.intents import AccountSnapshot
from web.mobile.auth import mint_dev_token
from web.mobile.orders import MobileOrderRequest, place_user_order
from web.mobile.router import build_mobile_router
from web.mobile.settings import load_settings


# ── fakes ──────────────────────────────────────────────────────────────────
class FakeBroker:
    """A broker whose ``place_order`` / ``review_order`` never touch the network."""

    def __init__(self, cfg, *, connected=True, saved=True, snapshot=None):
        self.cfg = cfg
        self._connected = connected
        self._saved = saved
        self._snapshot = snapshot if snapshot is not None else AccountSnapshot(
            buying_power=10_000.0,
            positions=[{"symbol": "NVDA", "quantity": "12"}],
        )
        self.placed = []
        self.reviewed = []
        self.connect_calls = 0

    @property
    def is_connected(self):
        return self._connected

    def has_saved_credentials(self):
        return self._saved

    def connect(self, *a, **k):
        self.connect_calls += 1
        self._connected = self._saved  # only "connects" if creds exist
        return {}

    def get_account_snapshot(self):
        return self._snapshot

    def review_order(self, intent):
        self.reviewed.append(intent)
        return {"quote_data": {"last_trade_price": "123.45"}}

    def place_order(self, intent):
        self.placed.append(intent)
        return ("order-1", {"id": "order-1"})


class FakeRegistry:
    """Stand-in for ``BrokerRegistry`` that hands back one fixed broker and
    records which user ids asked for it (to prove per-user routing)."""

    def __init__(self, broker):
        self.broker = broker
        self.gets = []

    def get(self, user_id, settings=None):
        self.gets.append(user_id)
        return self.broker


def _run(user_id="user-x", ticker="NVDA", pending_order=None, order_ticker=None):
    return types.SimpleNamespace(
        run_id="run-abc",
        analytics={"user_id": user_id, "ticker": ticker},
        order_ticker=order_ticker,
        pending_order=pending_order,
        order_lock=threading.Lock(),
        order_placed=False,
        order_result=None,
    )


def _server(run):
    manager = types.SimpleNamespace(get=lambda rid: run if rid == run.run_id else None)
    return types.SimpleNamespace(manager=manager)


def _cfg(*, dry_run=True, trade_mode="manual", max_notional=1000.0, enabled=True):
    return RobinhoodConfig(
        enabled=enabled,
        trade_mode=trade_mode,
        dry_run=dry_run,
        max_order_notional=max_notional,
        default_order_notional=100.0,
    )


def _user():
    return types.SimpleNamespace(user_id="user-x", email=None, role=None)


# ── direct place_user_order unit tests ─────────────────────────────────────
@pytest.mark.unit
class TestPlaceUserOrder:
    def test_unknown_run_is_404(self):
        broker = FakeBroker(_cfg())
        with pytest.raises(HTTPException) as exc:
            place_user_order(
                _server(_run()), FakeRegistry(broker), load_settings(), _user(),
                "no-such-run", MobileOrderRequest(action="buy", notional=100),
            )
        assert exc.value.status_code == 404

    def test_run_owned_by_other_user_is_404(self):
        run = _run(user_id="someone-else")
        broker = FakeBroker(_cfg())
        with pytest.raises(HTTPException) as exc:
            place_user_order(
                _server(run), FakeRegistry(broker), load_settings(), _user(),
                run.run_id, MobileOrderRequest(action="buy", notional=100),
            )
        assert exc.value.status_code == 404
        assert broker.placed == []  # never reached the broker

    def test_not_connected_is_rejected(self):
        run = _run()
        broker = FakeBroker(_cfg(), connected=False, saved=False)
        with pytest.raises(HTTPException) as exc:
            place_user_order(
                _server(run), FakeRegistry(broker), load_settings(), _user(),
                run.run_id, MobileOrderRequest(action="buy", notional=100),
            )
        assert exc.value.status_code == 409
        assert "not connected" in exc.value.detail.lower()
        assert broker.placed == []

    def test_execution_off_is_rejected(self):
        run = _run()
        broker = FakeBroker(_cfg(trade_mode="off"))
        with pytest.raises(HTTPException) as exc:
            place_user_order(
                _server(run), FakeRegistry(broker), load_settings(), _user(),
                run.run_id, MobileOrderRequest(action="buy", notional=100),
            )
        assert exc.value.status_code == 409

    def test_dry_run_does_not_place_real_order(self):
        run = _run()
        broker = FakeBroker(_cfg(dry_run=True))
        out = place_user_order(
            _server(run), FakeRegistry(broker), load_settings(), _user(),
            run.run_id, MobileOrderRequest(action="buy", notional=200),
        )
        assert out["status"] == "dry_run"
        assert out["dry_run"] is True
        assert out["can_place_real_orders"] is False
        assert broker.placed == []  # nothing actually placed
        assert broker.reviewed  # but it was simulated

    def test_live_places_real_order(self):
        run = _run()
        broker = FakeBroker(_cfg(dry_run=False))
        out = place_user_order(
            _server(run), FakeRegistry(broker), load_settings(), _user(),
            run.run_id, MobileOrderRequest(action="buy", notional=200),
        )
        assert out["status"] == "placed"
        assert out["dry_run"] is False
        assert out["can_place_real_orders"] is True
        assert out["order_id"] == "order-1"
        assert len(broker.placed) == 1
        assert broker.placed[0].notional == 200

    def test_notional_is_reclamped_server_side(self):
        # Client asks for 5000 but the server cap is 500 → clamped to 500.
        run = _run()
        broker = FakeBroker(_cfg(dry_run=False, max_notional=500.0))
        out = place_user_order(
            _server(run), FakeRegistry(broker), load_settings(), _user(),
            run.run_id, MobileOrderRequest(action="buy", notional=5000),
        )
        assert out["status"] == "placed"
        assert broker.placed[0].notional == 500.0
        assert out["intent"]["notional"] == 500.0

    def test_ticker_is_forced_from_run(self):
        # Even with a stale pending_order ticker, the run's ticker wins; the
        # request model has no ticker field at all, so the client can't override.
        run = _run(ticker="NVDA", pending_order={"ticker": "STALE", "action": "buy"})
        broker = FakeBroker(_cfg(dry_run=False))
        out = place_user_order(
            _server(run), FakeRegistry(broker), load_settings(), _user(),
            run.run_id, MobileOrderRequest(notional=150),
        )
        assert broker.placed[0].ticker == "NVDA"
        assert out["intent"]["ticker"] == "NVDA"

    def test_sell_quantity_clamped_to_holdings(self):
        # Held quantity is 12; asking to sell 100 clamps down to 12.
        run = _run()
        broker = FakeBroker(_cfg(dry_run=False))
        out = place_user_order(
            _server(run), FakeRegistry(broker), load_settings(), _user(),
            run.run_id, MobileOrderRequest(action="sell", quantity=100),
        )
        assert out["status"] == "placed"
        assert broker.placed[0].quantity == 12.0

    def test_uses_per_user_registry(self):
        run = _run()
        broker = FakeBroker(_cfg(dry_run=True))
        registry = FakeRegistry(broker)
        place_user_order(
            _server(run), registry, load_settings(), _user(),
            run.run_id, MobileOrderRequest(action="buy", notional=100),
        )
        assert registry.gets == ["user-x"]  # asked the registry for THIS user

    def test_idempotent_double_submit(self):
        run = _run()
        broker = FakeBroker(_cfg(dry_run=False))
        body = MobileOrderRequest(action="buy", notional=100)
        first = place_user_order(
            _server(run), FakeRegistry(broker), load_settings(), _user(),
            run.run_id, body,
        )
        second = place_user_order(
            _server(run), FakeRegistry(broker), load_settings(), _user(),
            run.run_id, body,
        )
        assert first == second
        assert len(broker.placed) == 1  # only fired once

    def test_buy_without_size_is_400(self):
        run = _run()  # no pending_order to supply a size
        broker = FakeBroker(_cfg())
        with pytest.raises(HTTPException) as exc:
            place_user_order(
                _server(run), FakeRegistry(broker), load_settings(), _user(),
                run.run_id, MobileOrderRequest(action="buy"),
            )
        assert exc.value.status_code == 400


# ── HTTP-level tests via the wired router ──────────────────────────────────
def _fake_server_module(run):
    return types.SimpleNamespace(
        manager=types.SimpleNamespace(get=lambda rid: run if rid == run.run_id else None),
    )


def _client(monkeypatch, tmp_path, run, broker, **env):
    monkeypatch.setenv("MOBILE_API_ENABLED", "true")
    monkeypatch.setenv("MOBILE_AUTH_SECRET", "s3cret")
    monkeypatch.setenv("MOBILE_TOKEN_DIR", str(tmp_path))
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    # Route the router's module-level registry to our fake broker.
    monkeypatch.setattr("web.mobile.router.registry", FakeRegistry(broker))
    app = FastAPI()
    app.include_router(build_mobile_router(_fake_server_module(run)))
    return TestClient(app)


def _auth(user_id="user-x"):
    return {"Authorization": f"Bearer {mint_dev_token(user_id, 's3cret')}"}


@pytest.mark.unit
class TestOrderRouter:
    def test_requires_auth(self, monkeypatch, tmp_path):
        run = _run()
        client = _client(monkeypatch, tmp_path, run, FakeBroker(_cfg()))
        r = client.post("/api/v2/runs/run-abc/orders", json={"action": "buy", "notional": 100})
        assert r.status_code == 401

    def test_places_dry_run_with_auth(self, monkeypatch, tmp_path):
        run = _run()
        broker = FakeBroker(_cfg(dry_run=True))
        client = _client(monkeypatch, tmp_path, run, broker)
        r = client.post(
            "/api/v2/runs/run-abc/orders",
            headers=_auth(),
            json={"action": "buy", "notional": 100},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "dry_run"
        assert body["type"] == "trade"
        assert broker.placed == []

    def test_other_users_run_is_404(self, monkeypatch, tmp_path):
        run = _run(user_id="owner")  # run belongs to "owner"
        broker = FakeBroker(_cfg())
        client = _client(monkeypatch, tmp_path, run, broker)
        r = client.post(
            "/api/v2/runs/run-abc/orders",
            headers=_auth("intruder"),
            json={"action": "buy", "notional": 100},
        )
        assert r.status_code == 404
        assert broker.placed == []

    def test_order_rate_limited(self, monkeypatch, tmp_path):
        run = _run()
        broker = FakeBroker(_cfg(dry_run=True))
        client = _client(
            monkeypatch, tmp_path, run, broker,
            MOBILE_ORDERS_PER_HOUR="1", MOBILE_ORDERS_PER_DAY="100",
        )
        payload = {"action": "buy", "notional": 100}
        first = client.post("/api/v2/runs/run-abc/orders", headers=_auth(), json=payload)
        assert first.status_code == 200
        second = client.post("/api/v2/runs/run-abc/orders", headers=_auth(), json=payload)
        assert second.status_code == 429
        assert "Retry-After" in second.headers
