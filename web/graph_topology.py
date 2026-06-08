"""Static, self-contained description of the TradingAgents LangGraph topology.

This module powers the web *Agent Graph Blueprint* (``/blueprint``) and the live
agent graph on the runner. It deliberately does **not** import
``tradingagents.graph`` (which pulls in heavy data libraries like yfinance at
import time); instead it re-derives the topology from the same facts encoded in:

* ``tradingagents/graph/setup.py``            — nodes & edges
* ``tradingagents/graph/analyst_execution.py``— analyst node naming
* ``tradingagents/graph/conditional_logic.py``— debate / risk routing & limits
* ``tradingagents/graph/trading_graph.py``    — per-analyst ToolNodes
* ``tradingagents/dataflows/interface.py``    — tool→vendor routing
* ``tradingagents/agents/utils/memory.py``    — cross-run memory flow
* ``tradingagents/agents/utils/agent_states.py`` — AgentState fields (timeline)

``build_graph_blueprint`` returns a JSON-serializable dict consumed by
``web/static/graph.js`` (Cytoscape) and the two HTML pages.
"""

from __future__ import annotations

from typing import Dict, List

try:  # light import; falls back to literals if config is unavailable
    from tradingagents.default_config import DEFAULT_CONFIG

    DEEP_MODEL = DEFAULT_CONFIG.get("deep_think_llm", "deep LLM")
    QUICK_MODEL = DEFAULT_CONFIG.get("quick_think_llm", "quick LLM")
    MEMORY_LOG_PATH = DEFAULT_CONFIG.get(
        "memory_log_path", "~/.tradingagents/memory/trading_memory.md"
    )
except Exception:  # noqa: BLE001 — blueprint must never fail on config import
    DEEP_MODEL, QUICK_MODEL = "deep LLM", "quick LLM"
    MEMORY_LOG_PATH = "~/.tradingagents/memory/trading_memory.md"


# ── colors / legend ───────────────────────────────────────────────────────────
C_QUICK = "#38bdf8"   # quick-LLM agents (analysts, debators, trader)
C_DEEP = "#fb7185"    # deep-LLM managers (judges)
C_TOOL = "#34d399"    # deterministic ToolNodes
C_MEMORY = "#a78bfa"  # cross-run memory log
C_DATA = "#fb923c"    # external data vendors
C_IDENT = "#fbbf24"   # instrument identity
C_CLEAR = "#64748b"   # message-pruning nodes

LEGEND = [
    {"label": "Quick-LLM agent", "color": C_QUICK},
    {"label": "Deep-LLM manager", "color": C_DEEP},
    {"label": "ToolNode", "color": C_TOOL},
    {"label": "Memory log", "color": C_MEMORY},
    {"label": "Data vendor", "color": C_DATA},
    {"label": "Instrument identity", "color": C_IDENT},
    {"label": "Msg clear", "color": C_CLEAR},
]


# ── per-analyst facts (mirrors analyst_execution + trading_graph tool nodes) ────
ANALYSTS: Dict[str, dict] = {
    "market": {
        "agent_node": "Market Analyst",
        "clear_node": "Msg Clear Market",
        "tool_node": "tools_market",
        "report_key": "market_report",
        "icon": "📈",
        "color": C_QUICK,
        "blurb": "Price action, technical indicators, and a verified ground-truth snapshot.",
        "tools": [
            {"tool": "get_stock_data", "vendors": ["alpha_vantage", "yfinance"]},
            {"tool": "get_indicators", "vendors": ["alpha_vantage", "yfinance"]},
            {"tool": "get_verified_market_snapshot", "vendors": ["verified snapshot"]},
        ],
    },
    "social": {
        "agent_node": "Sentiment Analyst",
        "clear_node": "Msg Clear Sentiment",
        "tool_node": "tools_social",
        "report_key": "sentiment_report",
        "icon": "💬",
        "color": "#22d3ee",
        "blurb": "Retail & social sentiment — news plus pre-fetched StockTwits and Reddit.",
        "tools": [
            {"tool": "get_news", "vendors": ["alpha_vantage", "yfinance"]},
        ],
    },
    "news": {
        "agent_node": "News Analyst",
        "clear_node": "Msg Clear News",
        "tool_node": "tools_news",
        "report_key": "news_report",
        "icon": "📰",
        "color": "#818cf8",
        "blurb": "Macro & company headlines plus insider transactions.",
        "tools": [
            {"tool": "get_news", "vendors": ["alpha_vantage", "yfinance"]},
            {"tool": "get_global_news", "vendors": ["yfinance", "alpha_vantage"]},
            {"tool": "get_insider_transactions", "vendors": ["alpha_vantage", "yfinance"]},
        ],
    },
    "fundamentals": {
        "agent_node": "Fundamentals Analyst",
        "clear_node": "Msg Clear Fundamentals",
        "tool_node": "tools_fundamentals",
        "report_key": "fundamentals_report",
        "icon": "📊",
        "color": "#0ea5e9",
        "blurb": "Financial statements and valuation.",
        "tools": [
            {"tool": "get_fundamentals", "vendors": ["alpha_vantage", "yfinance"]},
            {"tool": "get_balance_sheet", "vendors": ["alpha_vantage", "yfinance"]},
            {"tool": "get_cashflow", "vendors": ["alpha_vantage", "yfinance"]},
            {"tool": "get_income_statement", "vendors": ["alpha_vantage", "yfinance"]},
        ],
    },
}

ANALYST_ORDER = ["market", "social", "news", "fundamentals"]


# ── downstream (always-on) agents ──────────────────────────────────────────────
DOWNSTREAM = {
    "Bull Researcher": {
        "type": "researcher", "stage": "Research Debate", "icon": "🐂", "color": C_QUICK,
        "model": QUICK_MODEL, "pattern": "Debate turn (loops with Bear)",
        "blurb": "Argues the case to buy.",
        "reads": ["market_report", "sentiment_report", "news_report", "fundamentals_report", "investment_debate_state"],
        "writes": ["investment_debate_state"],
    },
    "Bear Researcher": {
        "type": "researcher", "stage": "Research Debate", "icon": "🐻", "color": C_QUICK,
        "model": QUICK_MODEL, "pattern": "Debate turn (loops with Bull)",
        "blurb": "Argues the case to sell.",
        "reads": ["market_report", "sentiment_report", "news_report", "fundamentals_report", "investment_debate_state"],
        "writes": ["investment_debate_state"],
    },
    "Research Manager": {
        "type": "manager", "stage": "Research Debate", "icon": "🧭", "color": C_DEEP,
        "model": DEEP_MODEL, "pattern": "Judge (deep model)",
        "blurb": "Judges the bull/bear debate and writes the investment plan.",
        "reads": ["investment_debate_state"],
        "writes": ["investment_plan", "investment_debate_state"],
    },
    "Trader": {
        "type": "trader", "stage": "Trading Plan", "icon": "💼", "color": "#fbbf24",
        "model": QUICK_MODEL, "pattern": "Single pass",
        "blurb": "Turns the plan into a concrete trade proposal.",
        "reads": ["investment_plan"],
        "writes": ["trader_investment_plan"],
    },
    "Aggressive Analyst": {
        "type": "risk_debator", "stage": "Risk Debate", "icon": "🚀", "color": "#fb923c",
        "model": QUICK_MODEL, "pattern": "Risk turn (loops trio)",
        "blurb": "Risk-on perspective.",
        "reads": ["trader_investment_plan", "risk_debate_state"],
        "writes": ["risk_debate_state"],
    },
    "Conservative Analyst": {
        "type": "risk_debator", "stage": "Risk Debate", "icon": "🛡️", "color": C_QUICK,
        "model": QUICK_MODEL, "pattern": "Risk turn (loops trio)",
        "blurb": "Risk-off perspective.",
        "reads": ["trader_investment_plan", "risk_debate_state"],
        "writes": ["risk_debate_state"],
    },
    "Neutral Analyst": {
        "type": "risk_debator", "stage": "Risk Debate", "icon": "⚖️", "color": "#94a3b8",
        "model": QUICK_MODEL, "pattern": "Risk turn (loops trio)",
        "blurb": "Balanced perspective.",
        "reads": ["trader_investment_plan", "risk_debate_state"],
        "writes": ["risk_debate_state"],
    },
    "Portfolio Manager": {
        "type": "manager", "stage": "Decision", "icon": "🎯", "color": "#34d399",
        "model": DEEP_MODEL, "pattern": "Judge (deep model) + memory consumer",
        "blurb": "Makes the final call. The only agent injected with past_context from the memory log.",
        "reads": ["risk_debate_state", "past_context"],
        "writes": ["final_trade_decision"],
    },
}


# ── memory section (mirrors memory.py + trading_graph resolve/inject/store) ─────
MEMORY_SECTION = {
    "title": "Cross-run memory",
    "subtitle": "An append-only decision log that lets the system learn from how its past calls actually played out.",
    "storage": MEMORY_LOG_PATH,
    "consumer": "Portfolio Manager (past_context)",
    "types": [
        {"label": "Cross-run log", "color": C_MEMORY},
        {"label": "In-run AgentState", "color": C_QUICK},
        {"label": "Debate histories", "color": "#a78bfa"},
    ],
    "flow": [
        {
            "step": 1, "phase": "store", "title": "Store pending decision",
            "detail": "At the end of a run the final decision is appended to the log tagged 'pending' (no LLM call).",
            "actor": "store_decision()", "graph_nodes": ["Portfolio Manager", "Memory Log"],
        },
        {
            "step": 2, "phase": "resolve", "title": "Resolve prior outcomes",
            "detail": "On the next same-ticker run, pending entries are scored against realized market returns (raw + alpha).",
            "actor": "_resolve_pending_entries()", "graph_nodes": ["Memory Log"],
        },
        {
            "step": 3, "phase": "reflect", "title": "Reflect",
            "detail": "Resolved entries get a REFLECTION appended, capturing what the outcome implies.",
            "actor": "update_with_outcome()", "graph_nodes": ["Memory Log"],
        },
        {
            "step": 4, "phase": "inject", "title": "Inject past context",
            "detail": "get_past_context() formats up to 5 same-ticker decisions + 3 cross-ticker lessons.",
            "actor": "get_past_context()", "graph_nodes": ["Memory Log", "Portfolio Manager"],
        },
        {
            "step": 5, "phase": "consume", "title": "Consume in decision",
            "detail": "Only the Portfolio Manager reads past_context when making the final call.",
            "actor": "Portfolio Manager", "graph_nodes": ["Portfolio Manager"],
        },
    ],
}


# ── data sources (mirrors interface.VENDOR_METHODS + sentiment pre-fetch) ───────
DATA_SOURCES = [
    {
        "id": "yfinance", "icon": "🟣", "name": "yfinance", "kind": "market+news+fundamentals",
        "provides": "Prices, indicators, news, statements, insider trades.",
        "routing": "route_to_vendor() when data_vendors maps a category to 'yfinance'.",
        "source_file": "tradingagents/dataflows/y_finance.py",
        "used_by_agents": ["Market Analyst", "News Analyst", "Fundamentals Analyst", "Sentiment Analyst"],
        "graph_nodes": ["tools_market", "tools_news", "tools_fundamentals", "tools_social"],
    },
    {
        "id": "alpha_vantage", "icon": "🟧", "name": "Alpha Vantage", "kind": "market+news+fundamentals",
        "provides": "Prices, indicators, news sentiment, statements, insider trades.",
        "routing": "route_to_vendor() when data_vendors maps a category to 'alpha_vantage'.",
        "source_file": "tradingagents/dataflows/alpha_vantage_common.py",
        "used_by_agents": ["Market Analyst", "News Analyst", "Fundamentals Analyst", "Sentiment Analyst"],
        "graph_nodes": ["tools_market", "tools_news", "tools_fundamentals", "tools_social"],
    },
    {
        "id": "stocktwits", "icon": "💬", "name": "StockTwits", "kind": "social",
        "provides": "Retail message-stream sentiment.",
        "routing": "Pre-fetched by the Sentiment Analyst before it writes (not a tool loop).",
        "source_file": "tradingagents/agents/analysts/sentiment_analyst.py",
        "used_by_agents": ["Sentiment Analyst"],
        "graph_nodes": ["Sentiment Analyst"],
    },
    {
        "id": "reddit", "icon": "👽", "name": "Reddit", "kind": "social",
        "provides": "Subreddit discussion (JSON with RSS fallback on 403).",
        "routing": "Pre-fetched by the Sentiment Analyst before it writes (not a tool loop).",
        "source_file": "tradingagents/agents/analysts/social_media_analyst.py",
        "used_by_agents": ["Sentiment Analyst"],
        "graph_nodes": ["Sentiment Analyst"],
    },
    {
        "id": "verified_snapshot", "icon": "✅", "name": "Verified snapshot", "kind": "ground-truth",
        "provides": "Deterministic price/return snapshot the market analyst must cite before writing.",
        "routing": "get_verified_market_snapshot tool in the market ToolNode.",
        "source_file": "tradingagents/graph/trading_graph.py",
        "used_by_agents": ["Market Analyst"],
        "graph_nodes": ["tools_market"],
    },
    {
        "id": "identity", "icon": "🪪", "name": "Instrument identity", "kind": "grounding",
        "provides": "Resolves the ticker to a canonical instrument + benchmark before the run.",
        "routing": "resolve_instrument_context() at run start; written to AgentState.instrument_context.",
        "source_file": "tradingagents/graph/trading_graph.py",
        "used_by_agents": ["all agents"],
        "graph_nodes": ["Instrument Identity"],
    },
]


def _state_timeline(selected: List[str]) -> List[dict]:
    """AgentState fields in roughly the order they're populated during a run."""
    timeline = [
        {"field": "instrument_context", "by": "pre-run identity"},
        {"field": "past_context", "by": "pre-run memory"},
    ]
    for key in selected:
        a = ANALYSTS[key]
        timeline.append({"field": a["report_key"], "by": a["agent_node"]})
    timeline += [
        {"field": "investment_debate_state", "by": "Bull / Bear"},
        {"field": "investment_plan", "by": "Research Manager"},
        {"field": "trader_investment_plan", "by": "Trader"},
        {"field": "risk_debate_state", "by": "Risk trio"},
        {"field": "final_trade_decision", "by": "Portfolio Manager"},
    ]
    return timeline


def _tool_matrix(selected: List[str]) -> List[dict]:
    rows = []
    for key in selected:
        a = ANALYSTS[key]
        tools = [
            {"tool": t["tool"], "vendors": t["vendors"], "tool_node": a["tool_node"]}
            for t in a["tools"]
        ]
        row = {
            "analyst": a["agent_node"],
            "icon": a["icon"],
            "pattern": "ReAct tool loop",
            "tools": tools,
        }
        if key == "social":
            row["prefetch"] = [
                {"source": "StockTwits", "via": "pre-fetch"},
                {"source": "Reddit", "via": "pre-fetch (RSS fallback)"},
            ]
        rows.append(row)
    return rows


# ── geometry ────────────────────────────────────────────────────────────────
COL_W = 260
ROW_H = 130


def build_graph_blueprint(
    selected_analysts: List[str],
    max_debate_rounds: int = 1,
    max_risk_rounds: int = 1,
    include_internal: bool = True,
) -> dict:
    """Build the full topology for the requested configuration.

    Raises ``ValueError`` for unknown / empty analyst selections so the API can
    surface a 400.
    """
    selected = [a for a in selected_analysts if a]
    for key in selected:
        if key not in ANALYSTS:
            raise ValueError(f"unknown analyst key: {key}")
    if not selected:
        raise ValueError("at least one analyst must be selected")
    # Preserve canonical order regardless of how the caller passed them.
    selected = [k for k in ANALYST_ORDER if k in selected]

    max_debate_rounds = max(1, int(max_debate_rounds))
    max_risk_rounds = max(1, int(max_risk_rounds))

    nodes: List[dict] = []
    edges: List[dict] = []

    def add_node(node_id, label, ntype, stage, color, x, y, internal=False, meta=None):
        nodes.append({
            "id": node_id, "label": label, "type": ntype, "stage": stage,
            "color": color, "internal": internal, "x": int(x), "y": int(y),
            "meta": {"icon": (meta or {}).get("icon", "•"), **(meta or {})},
        })

    def add_edge(source, target, label="", conditional=False, kind="flow", internal=False):
        edges.append({
            "id": f"{source}->{target}:{label}",
            "source": source, "target": target, "label": label,
            "conditional": conditional, "kind": kind, "internal": internal,
        })

    # col 0 — pre-run grounding (always shown so live memory animation works)
    add_node("Instrument Identity", "Instrument Identity", "identity", "Memory",
             C_IDENT, 0, -ROW_H, internal=False,
             meta={"icon": "🪪", "blurb": "Resolves the ticker to a canonical instrument + benchmark before the run.",
                   "writes": ["instrument_context"], "source": "tradingagents/graph/trading_graph.py"})
    add_node("Memory Log", "Memory Log", "memory", "Memory",
             C_MEMORY, 0, ROW_H, internal=False,
             meta={"icon": "💾", "blurb": "Append-only cross-run decision log.",
                   "reads": ["(prior runs)"], "writes": ["past_context"], "source": MEMORY_LOG_PATH})

    # analyst columns
    first_agent = ANALYSTS[selected[0]]["agent_node"]
    last_idx = len(selected) - 1
    for i, key in enumerate(selected):
        a = ANALYSTS[key]
        col = i + 1
        ax, ay = col * COL_W, 0
        add_node(a["agent_node"], a["agent_node"], "analyst", "Analysis", a["color"], ax, ay,
                 internal=False,
                 meta={"icon": a["icon"], "blurb": a["blurb"], "model": QUICK_MODEL,
                       "pattern": "ReAct tool loop", "report_key": a["report_key"],
                       "reads": ["instrument_context"], "writes": [a["report_key"]],
                       "tools": [t["tool"] for t in a["tools"]],
                       "source": "tradingagents/agents/analysts/"})

        if include_internal:
            # tool node below, clear node above
            add_node(a["tool_node"], a["tool_node"], "tool", "Analysis", C_TOOL, ax, ay + ROW_H + 30,
                     internal=True,
                     meta={"icon": "🛠️", "blurb": "Deterministic ToolNode — runs the analyst's tool calls.",
                           "tools": [t["tool"] for t in a["tools"]],
                           "source": "tradingagents/graph/trading_graph.py"})
            add_node(a["clear_node"], a["clear_node"], "msg_clear", "Analysis", C_CLEAR, ax, ay - ROW_H - 30,
                     internal=True,
                     meta={"icon": "🧹", "blurb": "Prunes tool-call messages before handing off.",
                           "source": "create_msg_delete()"})
            # ReAct loop + clear handoff
            add_edge(a["agent_node"], a["tool_node"], "tool_calls", conditional=True)
            add_edge(a["tool_node"], a["agent_node"], "results")
            add_edge(a["agent_node"], a["clear_node"], "report ready", conditional=True)
            nxt = ANALYSTS[selected[i + 1]]["agent_node"] if i < last_idx else "Bull Researcher"
            add_edge(a["clear_node"], nxt, "")
        else:
            # collapsed: analyst -> next analyst (or Bull) directly
            nxt = ANALYSTS[selected[i + 1]]["agent_node"] if i < last_idx else "Bull Researcher"
            add_edge(a["agent_node"], nxt, "report")

    analyst_cols = len(selected)

    def place(name, col_offset, y):
        meta = DOWNSTREAM[name]
        add_node(name, name, meta["type"], meta["stage"], meta["color"],
                 (analyst_cols + col_offset) * COL_W, y, internal=False,
                 meta={"icon": meta["icon"], "blurb": meta["blurb"], "model": meta["model"],
                       "pattern": meta["pattern"], "reads": meta["reads"], "writes": meta["writes"],
                       "source": "tradingagents/agents/"})

    # research debate
    place("Bull Researcher", 1, -90)
    place("Bear Researcher", 1, 90)
    place("Research Manager", 2, 0)
    add_edge("Bull Researcher", "Bear Researcher", "continue", conditional=True)
    add_edge("Bear Researcher", "Bull Researcher", "continue", conditional=True)
    add_edge("Bull Researcher", "Research Manager",
             f"after {2 * max_debate_rounds} turns", conditional=True)
    add_edge("Bear Researcher", "Research Manager",
             f"after {2 * max_debate_rounds} turns", conditional=True)

    # trader
    place("Trader", 3, 0)
    add_edge("Research Manager", "Trader", "investment_plan")

    # risk debate trio
    place("Aggressive Analyst", 4, -130)
    place("Conservative Analyst", 4, 0)
    place("Neutral Analyst", 4, 130)
    add_edge("Trader", "Aggressive Analyst", "trader_investment_plan")
    add_edge("Aggressive Analyst", "Conservative Analyst", "continue", conditional=True)
    add_edge("Conservative Analyst", "Neutral Analyst", "continue", conditional=True)
    add_edge("Neutral Analyst", "Aggressive Analyst", "continue", conditional=True)
    risk_turns = 3 * max_risk_rounds
    for name in ("Aggressive Analyst", "Conservative Analyst", "Neutral Analyst"):
        add_edge(name, "Portfolio Manager", f"after {risk_turns} turns", conditional=True)

    # decision
    place("Portfolio Manager", 5, 0)

    # memory edges (always present — drive the purple live animation)
    add_edge("Instrument Identity", first_agent, "instrument_context", kind="memory")
    add_edge("Memory Log", "Portfolio Manager", "past_context", kind="memory")
    add_edge("Portfolio Manager", "Memory Log", "store pending", kind="memory")

    # data vendor nodes + edges (internal only)
    if include_internal:
        vendor_defs = [
            ("yfinance", "🟣", ["market", "social", "news", "fundamentals"]),
            ("alpha_vantage", "🟧", ["market", "social", "news", "fundamentals"]),
        ]
        vy = (analyst_cols + 1) * 0  # vendors sit along the bottom under analysts
        base_y = ROW_H * 2 + 80
        for vi, (vid, vicon, applies) in enumerate(vendor_defs):
            vx = (1) * COL_W + vi * COL_W
            add_node(vid, vid, "data_vendor", "Data", C_DATA, vx, base_y + vi * (ROW_H - 20),
                     internal=True,
                     meta={"icon": vicon, "blurb": f"External data vendor ({vid}).",
                           "source": "tradingagents/dataflows/interface.py"})
            for key in selected:
                if key in applies:
                    add_edge(vid, ANALYSTS[key]["tool_node"], "route_to_vendor", kind="data", internal=True)

    # filter (defensive — server already passes the right include_internal)
    if not include_internal:
        keep = {n["id"] for n in nodes if not n["internal"]}
        nodes = [n for n in nodes if n["id"] in keep]
        edges = [e for e in edges if e["source"] in keep and e["target"] in keep and not e["internal"]]

    # memory section with only graph_nodes that exist in this view
    node_ids = {n["id"] for n in nodes}
    memory = {**MEMORY_SECTION, "flow": [
        {**step, "graph_nodes": [g for g in step["graph_nodes"] if g in node_ids]}
        for step in MEMORY_SECTION["flow"]
    ]}
    data_sources = [
        {**ds, "graph_nodes": [g for g in ds["graph_nodes"] if g in node_ids]}
        for ds in DATA_SOURCES
    ]

    return {
        "selected_analysts": selected,
        "max_debate_rounds": max_debate_rounds,
        "max_risk_rounds": max_risk_rounds,
        "include_internal": include_internal,
        "nodes": nodes,
        "edges": edges,
        "legend": LEGEND,
        "memory": memory,
        "data_sources": data_sources,
        "tool_matrix": _tool_matrix(selected),
        "state_timeline": _state_timeline(selected),
        "stats": {"nodes": len(nodes), "edges": len(edges)},
    }
