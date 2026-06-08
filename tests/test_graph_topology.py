"""Tests for the web blueprint topology builder (web/graph_topology.py).

These guard the contract consumed by web/static/graph.js + the HTML pages, and
verify the topology matches tradingagents/graph/setup.py.
"""

import pytest

from web.graph_topology import build_graph_blueprint


def _ids(blueprint):
    return {n["id"] for n in blueprint["nodes"]}


def test_all_analysts_full_topology():
    bp = build_graph_blueprint(["market", "social", "news", "fundamentals"])
    ids = _ids(bp)
    # Core downstream agents always present
    for name in [
        "Bull Researcher", "Bear Researcher", "Research Manager", "Trader",
        "Aggressive Analyst", "Conservative Analyst", "Neutral Analyst",
        "Portfolio Manager",
    ]:
        assert name in ids
    # Selected analysts present
    for name in ["Market Analyst", "Sentiment Analyst", "News Analyst", "Fundamentals Analyst"]:
        assert name in ids
    assert bp["stats"]["nodes"] == len(bp["nodes"])
    assert bp["stats"]["edges"] == len(bp["edges"])


def test_required_top_level_keys():
    bp = build_graph_blueprint(["market"])
    for key in ["nodes", "edges", "legend", "memory", "data_sources",
                "tool_matrix", "state_timeline", "selected_analysts"]:
        assert key in bp


def test_include_internal_toggles_tool_and_clear_nodes():
    full = build_graph_blueprint(["market"], include_internal=True)
    collapsed = build_graph_blueprint(["market"], include_internal=False)
    assert "tools_market" in _ids(full)
    assert "Msg Clear Market" in _ids(full)
    # Collapsed view hides internal nodes
    assert "tools_market" not in _ids(collapsed)
    assert "Msg Clear Market" not in _ids(collapsed)
    assert not any(n["internal"] for n in collapsed["nodes"])
    # Memory + identity stay visible so the live animation works
    assert "Memory Log" in _ids(collapsed)
    assert "Instrument Identity" in _ids(collapsed)


def test_memory_edges_have_animation_labels():
    bp = build_graph_blueprint(["market"], include_internal=False)
    labels = {e["label"] for e in bp["edges"]}
    assert "past_context" in labels       # inject / consume pulse
    assert "store pending" in labels      # store pulse
    assert "instrument_context" in labels  # identity pulse


def test_debate_and_risk_loops_present():
    bp = build_graph_blueprint(["market"])
    pairs = {(e["source"], e["target"]) for e in bp["edges"]}
    # Bull/Bear two-way loop
    assert ("Bull Researcher", "Bear Researcher") in pairs
    assert ("Bear Researcher", "Bull Researcher") in pairs
    # Risk trio cycle
    assert ("Aggressive Analyst", "Conservative Analyst") in pairs
    assert ("Conservative Analyst", "Neutral Analyst") in pairs
    assert ("Neutral Analyst", "Aggressive Analyst") in pairs


def test_round_count_reflected_in_edge_labels():
    bp = build_graph_blueprint(["market"], max_debate_rounds=2, max_risk_rounds=2)
    labels = " ".join(e["label"] for e in bp["edges"])
    assert "after 4 turns" in labels  # 2 * max_debate_rounds
    assert "after 6 turns" in labels  # 3 * max_risk_rounds


def test_invalid_analyst_raises():
    with pytest.raises(ValueError):
        build_graph_blueprint(["not_a_real_analyst"])


def test_empty_selection_raises():
    with pytest.raises(ValueError):
        build_graph_blueprint([])


def test_tool_matrix_and_state_timeline_track_selection():
    bp = build_graph_blueprint(["market", "news"])
    analysts_in_matrix = {row["analyst"] for row in bp["tool_matrix"]}
    assert analysts_in_matrix == {"Market Analyst", "News Analyst"}
    fields = {item["field"] for item in bp["state_timeline"]}
    assert "market_report" in fields
    assert "news_report" in fields
    assert "sentiment_report" not in fields
