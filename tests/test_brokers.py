"""Tests for the Robinhood MCP brokerage layer.

These cover the parts that matter for correctness and *safety* without touching
the network: the rating→order mapping, sizing/clamping, the dry-run gate (no
real order leaves the building unless every gate is open), config/env
resolution, adaptive order-arg mapping against a discovered tool schema, and
account-result parsing. A fake broker stands in for the live MCP connection.
"""

import pytest

from tradingagents.brokers.config import RobinhoodConfig, load_robinhood_config
from tradingagents.brokers.executor import AutoTradeExecutor, rating_to_intent
from tradingagents.brokers.grounding import build_account_context
from tradingagents.brokers.intents import AccountSnapshot, OrderIntent
from tradingagents.brokers.robinhood_mcp import (
    _coerce_result,
    _envelope,
    _first_record,
    _map_order_args,
)


def _cfg(**overrides) -> RobinhoodConfig:
    base = dict(
        enabled=True,
        trade_mode="auto",
        dry_run=True,
        default_order_notional=100.0,
        max_order_notional=1000.0,
    )
    base.update(overrides)
    return load_robinhood_config({"robinhood": base})


# ── rating → intent ───────────────────────────────────────────────────────
@pytest.mark.unit
class TestRatingToIntent:
    def test_buy_uses_full_default_notional(self):
        intent = rating_to_intent("Buy", "nvda", AccountSnapshot(), _cfg())
        assert intent.action == "buy"
        assert intent.ticker == "NVDA"
        assert intent.notional == 100.0
        assert intent.quantity is None

    def test_overweight_uses_half_notional(self):
        intent = rating_to_intent("Overweight", "AAPL", AccountSnapshot(), _cfg())
        assert intent.action == "buy"
        assert intent.notional == 50.0

    def test_hold_is_no_action(self):
        intent = rating_to_intent("Hold", "AAPL", AccountSnapshot(), _cfg())
        assert intent.action == "none"
        assert not intent.is_actionable

    def test_sell_full_position(self):
        snap = AccountSnapshot(positions=[{"symbol": "AAPL", "quantity": "8"}])
        intent = rating_to_intent("Sell", "AAPL", snap, _cfg())
        assert intent.action == "sell"
        assert intent.quantity == 8.0

    def test_underweight_sells_half_position(self):
        snap = AccountSnapshot(positions=[{"symbol": "AAPL", "quantity": "10"}])
        intent = rating_to_intent("Underweight", "AAPL", snap, _cfg())
        assert intent.action == "sell"
        assert intent.quantity == 5.0

    def test_sell_without_position_is_no_action(self):
        intent = rating_to_intent("Sell", "AAPL", AccountSnapshot(), _cfg())
        assert intent.action == "none"
        assert "no position" in intent.reason.lower()

    def test_buy_clamped_to_max_notional(self):
        cfg = _cfg(default_order_notional=5000.0, max_order_notional=250.0)
        intent = rating_to_intent("Buy", "AAPL", AccountSnapshot(), cfg)
        assert intent.notional == 250.0

    def test_buy_clamped_to_buying_power(self):
        snap = AccountSnapshot(buying_power=42.0)
        intent = rating_to_intent("Buy", "AAPL", snap, _cfg())
        assert intent.notional == 42.0

    def test_buy_with_zero_buying_power_is_no_action(self):
        snap = AccountSnapshot(buying_power=0.0)
        intent = rating_to_intent("Buy", "AAPL", snap, _cfg())
        assert intent.action == "none"

    def test_underweight_rounds_down_fractional(self):
        snap = AccountSnapshot(positions=[{"symbol": "X", "quantity": "1"}])
        intent = rating_to_intent("Underweight", "X", snap, _cfg())
        # 0.5 shares is allowed (fractional); rounds down to 6dp.
        assert intent.quantity == 0.5


# ── config / env resolution & safety gates ────────────────────────────────
@pytest.mark.unit
class TestConfig:
    def test_defaults_are_safe(self):
        cfg = load_robinhood_config({})
        assert cfg.enabled is False
        assert cfg.dry_run is True
        assert cfg.trade_mode == "manual"  # waits for a human click by default
        assert cfg.can_place_real_orders is False
        assert cfg.auto_places_real_orders is False

    def test_can_place_real_orders_gates(self):
        # Real money needs: enabled + mode != off + dry_run False.
        assert _cfg(dry_run=True).can_place_real_orders is False
        assert _cfg(trade_mode="off", dry_run=False).can_place_real_orders is False
        assert _cfg(enabled=False, dry_run=False).can_place_real_orders is False
        assert _cfg(trade_mode="manual", dry_run=False).can_place_real_orders is True
        assert _cfg(trade_mode="auto", dry_run=False).can_place_real_orders is True

    def test_auto_places_real_orders_only_in_auto(self):
        # The dangerous, no-click case is specifically auto + not dry_run.
        assert _cfg(trade_mode="manual", dry_run=False).auto_places_real_orders is False
        assert _cfg(trade_mode="auto", dry_run=False).auto_places_real_orders is True
        assert _cfg(trade_mode="auto", dry_run=True).auto_places_real_orders is False

    def test_legacy_auto_trade_maps_to_mode(self):
        assert load_robinhood_config(
            {"robinhood": {"enabled": True, "auto_trade": True}}
        ).trade_mode == "auto"

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("TRADINGAGENTS_ROBINHOOD_ENABLED", "true")
        monkeypatch.setenv("TRADINGAGENTS_ROBINHOOD_TRADE_MODE", "auto")
        monkeypatch.setenv("TRADINGAGENTS_ROBINHOOD_MAX_ORDER_NOTIONAL", "777")
        cfg = load_robinhood_config({})
        assert cfg.enabled is True
        assert cfg.trade_mode == "auto"
        assert cfg.max_order_notional == 777.0

    def test_malformed_env_is_ignored(self, monkeypatch):
        monkeypatch.setenv("TRADINGAGENTS_ROBINHOOD_MAX_ORDER_NOTIONAL", "not-a-number")
        cfg = load_robinhood_config({"robinhood": {"max_order_notional": 123.0}})
        assert cfg.max_order_notional == 123.0

    def test_invalid_trade_mode_falls_back_to_manual(self):
        cfg = load_robinhood_config({"robinhood": {"enabled": True, "trade_mode": "yolo"}})
        assert cfg.trade_mode == "manual"

    def test_public_status_has_no_secrets(self):
        status = _cfg().public_status()
        assert "token_storage_path" not in status
        assert status["enabled"] is True
        assert status["trade_mode"] == "auto"


# ── executor gating (the money-safety boundary) ───────────────────────────
class _FakeBroker:
    """Stands in for RobinhoodBroker; records whether a real order was sent."""

    def __init__(self, snapshot=None, connected=True):
        self._snapshot = snapshot or AccountSnapshot()
        self.is_connected = connected
        self.placed = []

    def get_account_snapshot(self):
        return self._snapshot

    def place_order(self, intent: OrderIntent):
        self.placed.append(intent)
        return "ORDER-123", {"id": "ORDER-123", "state": "queued"}


@pytest.mark.unit
class TestExecutorAuto:
    def test_disabled_when_feature_off(self):
        broker = _FakeBroker()
        res = AutoTradeExecutor(broker, _cfg(enabled=False)).execute("Buy", "AAPL")
        assert res.status == "disabled"
        assert broker.placed == []

    def test_disabled_when_mode_off(self):
        broker = _FakeBroker()
        res = AutoTradeExecutor(broker, _cfg(trade_mode="off")).execute("Buy", "AAPL")
        assert res.status == "disabled"
        assert broker.placed == []

    def test_dry_run_never_places_order(self):
        broker = _FakeBroker(AccountSnapshot(buying_power=500))
        res = AutoTradeExecutor(broker, _cfg(dry_run=True)).execute("Buy", "AAPL")
        assert res.status == "dry_run"
        assert res.intent.action == "buy"
        assert broker.placed == []  # critical: no real order sent in dry-run

    def test_hold_is_skipped(self):
        broker = _FakeBroker(AccountSnapshot(buying_power=500))
        res = AutoTradeExecutor(broker, _cfg(dry_run=False)).execute("Hold", "AAPL")
        assert res.status == "skipped"
        assert broker.placed == []

    def test_live_buy_places_order(self):
        broker = _FakeBroker(AccountSnapshot(buying_power=500))
        res = AutoTradeExecutor(broker, _cfg(dry_run=False)).execute("Buy", "AAPL")
        assert res.status == "placed"
        assert res.order_id == "ORDER-123"
        assert len(broker.placed) == 1
        assert broker.placed[0].action == "buy"

    def test_error_is_captured_not_raised(self):
        broker = _FakeBroker(AccountSnapshot(buying_power=500))

        def boom(_intent):
            raise RuntimeError("mcp exploded")

        broker.place_order = boom
        res = AutoTradeExecutor(broker, _cfg(dry_run=False)).execute("Buy", "AAPL")
        assert res.status == "error"
        assert "mcp exploded" in res.message


@pytest.mark.unit
class TestExecutorManual:
    def test_manual_proposes_without_placing(self):
        # Even with dry_run off, manual mode must NOT place from execute().
        broker = _FakeBroker(AccountSnapshot(buying_power=500))
        res = AutoTradeExecutor(broker, _cfg(trade_mode="manual", dry_run=False)).execute(
            "Buy", "AAPL"
        )
        assert res.status == "proposed"
        assert res.intent.action == "buy"
        assert broker.placed == []  # critical: nothing placed until a click

    def test_manual_hold_is_skipped_not_proposed(self):
        broker = _FakeBroker(AccountSnapshot(buying_power=500))
        res = AutoTradeExecutor(broker, _cfg(trade_mode="manual")).execute("Hold", "AAPL")
        assert res.status == "skipped"

    def test_execute_intent_dry_run_does_not_place(self):
        broker = _FakeBroker(AccountSnapshot(buying_power=500))
        intent = OrderIntent("AAPL", "buy", "Buy", notional=100.0)
        res = AutoTradeExecutor(broker, _cfg(trade_mode="manual", dry_run=True)).execute_intent(
            intent
        )
        assert res.status == "dry_run"
        assert broker.placed == []

    def test_execute_intent_places_when_live(self):
        broker = _FakeBroker(AccountSnapshot(buying_power=500))
        intent = OrderIntent("AAPL", "buy", "Buy", notional=100.0)
        res = AutoTradeExecutor(broker, _cfg(trade_mode="manual", dry_run=False)).execute_intent(
            intent
        )
        assert res.status == "placed"
        assert len(broker.placed) == 1

    def test_execute_intent_reclamps_edited_notional(self):
        # A tampered/edited ticket asking for $9999 is clamped to buying power.
        broker = _FakeBroker(AccountSnapshot(buying_power=250.0))
        intent = OrderIntent("AAPL", "buy", "Buy", notional=9999.0)
        cfg = _cfg(trade_mode="manual", dry_run=False, max_order_notional=1000.0)
        res = AutoTradeExecutor(broker, cfg).execute_intent(intent)
        assert res.status == "placed"
        assert broker.placed[0].notional == 250.0  # clamped to buying power

    def test_execute_intent_reclamps_sell_to_held(self):
        broker = _FakeBroker(
            AccountSnapshot(positions=[{"symbol": "AAPL", "quantity": "4"}])
        )
        intent = OrderIntent("AAPL", "sell", "Sell", quantity=100.0)
        cfg = _cfg(trade_mode="manual", dry_run=False)
        res = AutoTradeExecutor(broker, cfg).execute_intent(intent)
        assert res.status == "placed"
        assert broker.placed[0].quantity == 4.0  # never sell more than held


# ── adaptive order-arg mapping against a discovered schema ─────────────────
class _FakeTool:
    def __init__(self, name, args):
        self.name = name
        self.args = args


@pytest.mark.unit
class TestOrderArgMapping:
    def test_maps_to_schema_property_names(self):
        tool = _FakeTool(
            "place_equity_order",
            {
                "account_number": {},
                "symbol": {},
                "side": {},
                "type": {},
                "dollar_amount": {},
            },
        )
        intent = OrderIntent("AAPL", "buy", "Buy", notional=100.0, order_type="market")
        args = _map_order_args(tool, intent, account_number="694284670")
        assert args["account_number"] == "694284670"
        assert args["symbol"] == "AAPL"
        assert args["side"] == "buy"
        assert args["type"] == "market"
        # Robinhood requires numeric fields as strings.
        assert args["dollar_amount"] == "100.00"

    def test_quantity_for_sell_is_stringified(self):
        tool = _FakeTool(
            "place_equity_order",
            {"account_number": {}, "symbol": {}, "side": {}, "quantity": {}},
        )
        intent = OrderIntent("AAPL", "sell", "Sell", quantity=3.0)
        args = _map_order_args(tool, intent, account_number="694284670")
        assert args["symbol"] == "AAPL"
        assert args["side"] == "sell"
        assert args["quantity"] == "3"

    def test_unknown_schema_falls_back_to_canonical(self):
        tool = _FakeTool("place_order", {})
        intent = OrderIntent("AAPL", "buy", "Buy", notional=100.0)
        args = _map_order_args(tool, intent)
        assert args["symbol"] == "AAPL"
        assert args["side"] == "buy"
        assert args["dollar_amount"] == "100.00"


# ── account-result parsing ────────────────────────────────────────────────
@pytest.mark.unit
class TestResultParsing:
    def test_coerce_json_string(self):
        assert _coerce_result('{"a": 1}') == {"a": 1}

    def test_coerce_tuple_content(self):
        assert _coerce_result(('{"a": 1}', {"artifact": True})) == {"a": 1}

    def test_coerce_plain_string(self):
        assert _coerce_result("hello") == "hello"

    def test_coerce_mcp_content_blocks(self):
        # Robinhood returns content blocks wrapping a JSON string.
        blocks = [{"type": "text", "text": '{"data": {"total_value": "500"}}'}]
        assert _coerce_result(blocks) == {"data": {"total_value": "500"}}

    def test_envelope_strips_data_wrapper(self):
        assert _envelope({"data": {"x": 1}, "guide": "..."}) == {"x": 1}
        assert _envelope({"x": 1}) == {"x": 1}

    def test_first_record_unwraps_results(self):
        data = {"results": [{"buying_power": "100"}]}
        assert _first_record(data) == {"buying_power": "100"}


# ── grounding context ─────────────────────────────────────────────────────
@pytest.mark.unit
class TestGrounding:
    def test_includes_buying_power_and_position(self):
        snap = AccountSnapshot(
            buying_power=500.0,
            positions=[{"symbol": "NVDA", "quantity": "10", "average_buy_price": "100"}],
        )
        ctx = build_account_context(snap, "NVDA")
        assert "buying power" in ctx.lower()
        assert "NVDA" in ctx
        assert "10 shares" in ctx

    def test_reports_no_position(self):
        ctx = build_account_context(AccountSnapshot(buying_power=10.0), "TSLA")
        assert "No existing position in TSLA" in ctx

    def test_empty_snapshot_returns_no_position_line(self):
        ctx = build_account_context(AccountSnapshot(), "TSLA")
        # Even with nothing else, it states there's no position (useful signal).
        assert "TSLA" in ctx
