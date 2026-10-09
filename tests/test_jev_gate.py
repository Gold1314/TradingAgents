"""Shadow-mode Jev gate: disabled without a key, never raises, sends typed questions."""

from unittest.mock import MagicMock, patch

import pytest

from tradingagents.graph import jev_gate

STATE = {
    "company_of_interest": "NVDA",
    "trade_date": "2026-01-15",
    "investment_plan": "**Recommendation**: Buy",
    "trader_investment_plan": "**Action**: Buy",
    "final_trade_decision": "**Rating**: Overweight",
}


@pytest.mark.unit
def test_disabled_without_key(monkeypatch):
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    with patch("tradingagents.graph.jev_gate.requests.post") as post:
        assert jev_gate.assess(STATE) is None
        post.assert_not_called()


@pytest.mark.unit
def test_sends_state_and_returns_answers(monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", "k")
    resp = MagicMock()
    resp.json.return_value = {"answers": {"rating": {"choice": "Overweight"}}}
    with patch("tradingagents.graph.jev_gate.requests.post", return_value=resp) as post:
        assert jev_gate.assess(STATE) == {"rating": {"choice": "Overweight"}}
    body = post.call_args.kwargs["json"]
    assert body["questions"]["rating"]["options"] == ["Buy", "Overweight", "Hold", "Underweight", "Sell"]
    assert "**Rating**: Overweight" in body["state"]
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer k"


@pytest.mark.unit
def test_errors_return_none(monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", "k")
    with patch("tradingagents.graph.jev_gate.requests.post", side_effect=OSError("down")):
        assert jev_gate.assess(STATE) is None


@pytest.mark.unit
def test_state_is_capped():
    big = {**STATE, "investment_plan": "x" * 200_000}
    assert len(jev_gate.build_state(big)) == jev_gate._MAX_STATE_CHARS
