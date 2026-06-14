"""Tests for the env-gated in-memory fake broker (web/mobile/fake_broker.py).

The fake broker exists so the trading UI (connect status, account snapshot,
order ticket + biometric LIVE gate) can be exercised without live Robinhood. It
must be:

* **default OFF** — the registry hands back a real ``RobinhoodBroker`` unless
  ``MOBILE_FAKE_BROKER`` is set, so nothing changes for existing deployments;
* **connected with mock data** — so ``/api/v2/robinhood/status`` reports
  ``connected: true`` and an account snapshot;
* **safe in LIVE mode** — even with ``dry_run=false`` it returns a fake order id
  and never touches the network;
* **subject to the same server-side safety** — re-clamping / ticker-forcing in
  ``place_user_order`` still applies (the executor/orders code is reused as-is).
"""

import threading
import types

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tradingagents.brokers.robinhood_mcp import RobinhoodBroker
from web.mobile.auth import mint_dev_token
from web.mobile.broker_registry import BrokerRegistry, build_user_broker
from web.mobile.fake_broker import FakeBroker
from web.mobile.orders import MobileOrderRequest, place_user_order
from web.mobile.router import build_mobile_router
from web.mobile.settings import load_settings


# ── registry gating (default OFF) ──────────────────────────────────────────
@pytest.mark.unit
class TestFakeBrokerGating:
    def test_default_off_returns_real_broker(self, monkeypatch, tmp_path):
        monkeypatch.delenv("MOBILE_FAKE_BROKER", raising=False)
        monkeypatch.setenv("MOBILE_TOKEN_DIR", str(tmp_path))
        broker = build_user_broker("alice", load_settings())
        assert isinstance(broker, RobinhoodBroker)
        assert not isinstance(broker, FakeBroker)

    def test_flag_on_returns_fake_broker(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MOBILE_FAKE_BROKER", "1")
        monkeypatch.setenv("MOBILE_TOKEN_DIR", str(tmp_path))
        broker = build_user_broker("alice", load_settings())
        assert isinstance(broker, FakeBroker)

    def test_registry_hands_back_fake_when_enabled(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MOBILE_FAKE_BROKER", "true")
        monkeypatch.setenv("MOBILE_TOKEN_DIR", str(tmp_path))
        reg = BrokerRegistry()
        broker = reg.get("alice", load_settings())
        assert isinstance(broker, FakeBroker)
        assert broker.is_connected is True


# ── status payload ─────────────────────────────────────────────────────────
@pytest.mark.unit
class TestFakeBrokerStatus:
    def test_status_reports_connected_with_mock_account(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MOBILE_FAKE_BROKER", "1")
        monkeypatch.setenv("MOBILE_TOKEN_DIR", str(tmp_path))
        broker = build_user_broker("alice", load_settings())
        status = broker.status()
        assert status["connected"] is True
        assert status["has_saved_credentials"] is True
        assert status["available"] is True
        assert status["tools"]  # non-empty mock catalogue
        # Trade-safety gates still come from config (default-safe).
        assert status["trade_mode"] == "manual"
        assert status["dry_run"] is True
        # Additive mock account block.
        assert status["account"]["buying_power"] > 0


# ── order placement via the reused safety pipeline ──────────────────────────
def _run(user_id="user-x", ticker="NVDA"):
    return types.SimpleNamespace(
        run_id="run-abc",
        analytics={"user_id": user_id, "ticker": ticker},
        order_ticker=None,
        pending_order=None,
        order_lock=threading.Lock(),
        order_placed=False,
        order_result=None,
    )


def _server(run):
    manager = types.SimpleNamespace(get=lambda rid: run if rid == run.run_id else None)
    return types.SimpleNamespace(manager=manager)


class _Registry:
    def __init__(self, broker):
        self.broker = broker

    def get(self, user_id, settings=None):
        return self.broker


def _user():
    return types.SimpleNamespace(user_id="user-x", email=None, role=None)


@pytest.mark.unit
class TestFakeBrokerOrders:
    def test_dry_run_order_returns_simulated_trade(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MOBILE_FAKE_BROKER", "1")
        monkeypatch.setenv("MOBILE_TOKEN_DIR", str(tmp_path))
        monkeypatch.setenv("TRADINGAGENTS_ROBINHOOD_DRY_RUN", "true")
        settings = load_settings()
        broker = build_user_broker("user-x", settings)
        run = _run()
        out = place_user_order(
            _server(run), _Registry(broker), settings, _user(),
            run.run_id, MobileOrderRequest(action="buy", notional=200),
        )
        assert out["status"] == "dry_run"
        assert out["dry_run"] is True
        assert out["can_place_real_orders"] is False
        assert broker.placed == []  # nothing actually "placed"
        assert broker.reviewed  # but it was simulated

    def test_live_order_is_safe_and_returns_fake_id(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MOBILE_FAKE_BROKER", "1")
        monkeypatch.setenv("MOBILE_TOKEN_DIR", str(tmp_path))
        # LIVE: enabled + manual + dry_run=false. With a real broker this would
        # send a real order; the fake broker keeps it in-memory.
        monkeypatch.setenv("TRADINGAGENTS_ROBINHOOD_DRY_RUN", "false")
        monkeypatch.setenv("TRADINGAGENTS_ROBINHOOD_TRADE_MODE", "manual")
        settings = load_settings()
        broker = build_user_broker("user-x", settings)
        run = _run()
        out = place_user_order(
            _server(run), _Registry(broker), settings, _user(),
            run.run_id, MobileOrderRequest(action="buy", notional=200),
        )
        assert out["status"] == "placed"
        assert out["dry_run"] is False
        assert out["can_place_real_orders"] is True
        assert out["order_id"].startswith("fake-order-")
        assert len(broker.placed) == 1

    def test_notional_still_reclamped_server_side(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MOBILE_FAKE_BROKER", "1")
        monkeypatch.setenv("MOBILE_TOKEN_DIR", str(tmp_path))
        monkeypatch.setenv("TRADINGAGENTS_ROBINHOOD_DRY_RUN", "false")
        monkeypatch.setenv("TRADINGAGENTS_ROBINHOOD_MAX_ORDER_NOTIONAL", "500")
        settings = load_settings()
        broker = build_user_broker("user-x", settings)
        run = _run()
        out = place_user_order(
            _server(run), _Registry(broker), settings, _user(),
            run.run_id, MobileOrderRequest(action="buy", notional=5000),
        )
        # Clamped to the 500 cap (buying power is 10k, so the cap binds).
        assert out["intent"]["notional"] == 500.0
        assert broker.placed[0].notional == 500.0


# ── HTTP-level: status endpoint reports connected with the flag on ──────────
@pytest.mark.unit
class TestFakeBrokerRouter:
    def test_status_endpoint_connected_with_fake_broker(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MOBILE_API_ENABLED", "true")
        monkeypatch.setenv("MOBILE_AUTH_SECRET", "s3cret")
        monkeypatch.setenv("MOBILE_TOKEN_DIR", str(tmp_path))
        monkeypatch.setenv("MOBILE_FAKE_BROKER", "1")
        # Fresh registry so the new tmp/token-dir + flag take effect.
        monkeypatch.setattr("web.mobile.router.registry", BrokerRegistry())
        app = FastAPI()
        app.include_router(build_mobile_router(types.SimpleNamespace()))
        client = TestClient(app)
        headers = {"Authorization": f"Bearer {mint_dev_token('user-x', 's3cret')}"}
        r = client.get("/api/v2/robinhood/status", headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert body["connected"] is True
        assert body["has_saved_credentials"] is True
