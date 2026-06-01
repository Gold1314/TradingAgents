"""Guardrail: every tool an analyst is told to call must be registered in the
ToolNode that executes its calls.

Regression for the bug where the market analyst bound (and was prompted to
call) ``get_verified_market_snapshot`` while the ``market`` ToolNode only
registered ``get_stock_data`` and ``get_indicators``. The LLM issued the call,
the ToolNode could not execute it, and every run returned
``"get_verified_market_snapshot is not a valid tool"`` — silently disabling the
deterministic anti-hallucination grounding the snapshot exists to provide.
"""

import pytest

from tradingagents.graph.trading_graph import TradingAgentsGraph


def _tool_nodes():
    # _create_tool_nodes only uses module-level tool functions, not instance
    # state, so we can build the nodes without constructing LLM clients.
    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    return TradingAgentsGraph._create_tool_nodes(graph)


@pytest.mark.unit
def test_market_tool_node_registers_verified_snapshot():
    names = set(_tool_nodes()["market"].tools_by_name)
    assert "get_verified_market_snapshot" in names, (
        "market analyst is instructed to call get_verified_market_snapshot; "
        "the market ToolNode must register it or the call fails at runtime"
    )


@pytest.mark.unit
def test_market_tool_node_registers_core_tools():
    names = set(_tool_nodes()["market"].tools_by_name)
    assert {"get_stock_data", "get_indicators"} <= names


@pytest.mark.unit
@pytest.mark.parametrize(
    "analyst,expected",
    [
        ("market", {"get_stock_data", "get_indicators", "get_verified_market_snapshot"}),
        ("social", {"get_news"}),
        ("news", {"get_news", "get_global_news", "get_insider_transactions"}),
        (
            "fundamentals",
            {"get_fundamentals", "get_balance_sheet", "get_cashflow", "get_income_statement"},
        ),
    ],
)
def test_each_analyst_tool_node_registers_expected_tools(analyst, expected):
    names = set(_tool_nodes()[analyst].tools_by_name)
    assert expected <= names, f"{analyst} tool node missing {expected - names}"
