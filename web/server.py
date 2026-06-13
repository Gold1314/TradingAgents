"""FastAPI backend that runs the TradingAgents pipeline and streams every
agent's output to the browser in real time.

Design notes
------------
* This is an *additive* layer — it imports and drives the existing
  ``TradingAgentsGraph`` without modifying the pipeline. It reconstructs the
  same initial state ``TradingAgentsGraph._run_graph`` builds (memory context +
  resolved instrument identity), then streams ``graph.stream(..., stream_mode=
  "updates")`` so we receive one ``{node_name: state_delta}`` per super-step.
* Each run executes in a background thread (the LangGraph stream is blocking);
  events are pushed onto a per-run ``asyncio.Queue`` via
  ``loop.call_soon_threadsafe`` and drained by an SSE endpoint. This keeps the
  event loop responsive and supports multiple concurrent runs (deployable).
* No secrets are read here; the LLM key is loaded from ``.env`` by the
  ``tradingagents`` package exactly as the CLI does.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from tradingagents.brokers import (
    AutoTradeExecutor,
    ExecutionResult,
    OrderIntent,
    RobinhoodBroker,
    build_account_context,
    load_robinhood_config,
)
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.checkpointer import clear_checkpoint, thread_id
from tradingagents.graph.trading_graph import TradingAgentsGraph
from web import db
from web.charts import build_chart_payload
from web.graph_topology import build_graph_blueprint
from web.usage import TokenUsageTracker

logger = logging.getLogger("tradingagents.web")

STATIC_DIR = Path(__file__).parent / "static"

# Admin password gates the 60-minute cache toggle. When unset, admin features
# are disabled (the toggle endpoints return 503).
ADMIN_PASSWORD = os.environ.get("STOCKAGENTS_ADMIN_PASSWORD")

# How recent a stored run must be to be served from cache.
CACHE_WINDOW_MINUTES = 60

# Wire value -> display name for the four analysts.
ANALYST_DISPLAY = {
    "market": "Market Analyst",
    "social": "Sentiment Analyst",
    "news": "News Analyst",
    "fundamentals": "Fundamentals Analyst",
}

# Fixed agents that always run after the analysts, in execution order.
FIXED_NODES = [
    "Bull Researcher",
    "Bear Researcher",
    "Research Manager",
    "Trader",
    "Aggressive Analyst",
    "Conservative Analyst",
    "Neutral Analyst",
    "Portfolio Manager",
]

# Which state field each node writes its human-readable report into.
REPORT_FIELD = {
    "Market Analyst": "market_report",
    "Sentiment Analyst": "sentiment_report",
    "News Analyst": "news_report",
    "Fundamentals Analyst": "fundamentals_report",
    "Research Manager": "investment_plan",
    "Trader": "trader_investment_plan",
    "Portfolio Manager": "final_trade_decision",
}

# Risk debators report through the shared risk_debate_state dict.
RISK_RESPONSE_FIELD = {
    "Aggressive Analyst": "current_aggressive_response",
    "Conservative Analyst": "current_conservative_response",
    "Neutral Analyst": "current_neutral_response",
}


class RunRequest(BaseModel):
    ticker: str
    trade_date: str
    analysts: List[str] = ["market", "social", "news", "fundamentals"]
    asset_type: str = "stock"
    provider: Optional[str] = None
    deep_model: Optional[str] = None
    quick_model: Optional[str] = None
    max_debate_rounds: Optional[int] = None
    max_risk_rounds: Optional[int] = None
    force: bool = False  # bypass the 60-minute cache ("Run fresh anyway")


class CacheToggle(BaseModel):
    enabled: bool


class PlaceOrderRequest(BaseModel):
    """Edits to a proposed order ticket (manual mode). All fields optional —
    omitted fields fall back to the agent's proposed values. The ticker is
    intentionally NOT accepted: an order can only target the run's own ticker."""

    action: Optional[str] = None
    quantity: Optional[float] = None
    notional: Optional[float] = None
    order_type: Optional[str] = None


RUN_RETENTION_SECONDS = 3600  # keep finished runs for replay / reconnect


@dataclass
class Run:
    run_id: str
    queue: "asyncio.Queue[int]"
    loop: asyncio.AbstractEventLoop
    nodes: List[str]
    event_log: List[dict] = field(default_factory=list)
    done: threading.Event = field(default_factory=threading.Event)
    finished_at: Optional[float] = None
    # Manual-mode order awaiting a "Place order" click. ``pending_order`` holds
    # the proposed OrderIntent (as a dict); ``order_lock`` + ``order_placed``
    # make placement idempotent so a double-click can't fire two orders.
    pending_order: Optional[dict] = None
    order_ticker: Optional[str] = None
    order_placed: bool = False
    order_result: Optional[dict] = None
    order_lock: threading.Lock = field(default_factory=threading.Lock)


class RunManager:
    """Holds active runs and bridges worker threads to SSE consumers."""

    def __init__(self) -> None:
        self._runs: Dict[str, Run] = {}

    def create(self, loop: asyncio.AbstractEventLoop, nodes: List[str]) -> Run:
        self._prune_old_runs()
        run = Run(run_id=uuid.uuid4().hex, queue=asyncio.Queue(), loop=loop, nodes=nodes)
        self._runs[run.run_id] = run
        return run

    def get(self, run_id: str) -> Optional[Run]:
        self._prune_old_runs()
        return self._runs.get(run_id)

    def emit(self, run: Run, event: dict) -> None:
        """Append to the replay log and wake any connected SSE consumer."""
        run.event_log.append(event)
        if event.get("type") == "done":
            run.done.set()
            run.finished_at = time.time()

        def _notify() -> None:
            run.queue.put_nowait(len(run.event_log))

        run.loop.call_soon_threadsafe(_notify)

    def _prune_old_runs(self) -> None:
        cutoff = time.time() - RUN_RETENTION_SECONDS
        stale = [
            rid
            for rid, run in self._runs.items()
            if run.finished_at is not None and run.finished_at < cutoff
        ]
        for rid in stale:
            self._runs.pop(rid, None)


manager = RunManager()


# ── Robinhood broker (lazy singleton) ─────────────────────────────────────────
# One broker per process: OAuth tokens are cached, and a single connection is
# reused across runs. Created lazily so the server starts fine when the feature
# is disabled or the optional MCP deps are missing.
_broker_lock = threading.Lock()
_broker: Optional[RobinhoodBroker] = None


def get_broker() -> RobinhoodBroker:
    global _broker
    with _broker_lock:
        if _broker is None:
            _broker = RobinhoodBroker(load_robinhood_config(DEFAULT_CONFIG))
        return _broker


def _trade_event(result: ExecutionResult, cfg) -> dict:
    return {
        "type": "trade",
        "trade_mode": cfg.trade_mode,
        "can_place_real_orders": cfg.can_place_real_orders,
        "dry_run": cfg.dry_run,
        **result.to_dict(),
    }


def _place_pending_order(run: "Run", body: PlaceOrderRequest, cfg) -> dict:
    """Place the run's proposed order (manual mode), applying any ticket edits.

    Runs in a thread (blocking MCP call). Idempotent: a real placement locks the
    run so a double-click returns the first result instead of firing twice.
    Re-clamping happens inside the executor against a fresh account snapshot.
    """
    with run.order_lock:
        if run.order_placed and run.order_result:
            return run.order_result  # already placed — return the first result

        # Start from the agent's proposal; overlay only the provided edits.
        # The ticker is forced to the run's own ticker (never client-supplied).
        intent_dict = dict(run.pending_order or {})
        intent_dict["ticker"] = run.order_ticker
        if body.action:
            intent_dict["action"] = body.action
        if body.order_type:
            intent_dict["order_type"] = body.order_type
        if body.quantity is not None:
            intent_dict["quantity"] = body.quantity
        if body.notional is not None:
            intent_dict["notional"] = body.notional

        intent = OrderIntent.from_dict(intent_dict)
        # One sizing field per action: buys use notional, sells use quantity.
        if intent.action == "buy":
            intent.quantity = None
        elif intent.action == "sell":
            intent.notional = None

        broker = get_broker()
        if not broker.is_connected and broker.has_saved_credentials():
            broker.connect()
        if not broker.is_connected:
            result = ExecutionResult(
                status="error", intent=intent, message="Robinhood not connected."
            )
        else:
            snapshot = broker.get_account_snapshot()
            result = AutoTradeExecutor(broker, cfg).execute_intent(intent, snapshot)

        payload = _trade_event(result, cfg)
        if result.status == "placed":
            run.order_placed = True
            run.order_result = payload
        manager.emit(run, payload)  # record for replay / other tabs
        return payload


def _planned_nodes(analysts: List[str]) -> List[str]:
    ordered = [ANALYST_DISPLAY[a] for a in analysts if a in ANALYST_DISPLAY]
    return ordered + FIXED_NODES


def _tool_names(delta: Any) -> List[str]:
    """Tool names that executed in a ``tools_*`` node update."""
    names: List[str] = []
    if isinstance(delta, dict):
        for msg in delta.get("messages", []) or []:
            name = getattr(msg, "name", None)
            if name:
                names.append(name)
    return names


def _log_tool_calls(log_file: Optional[Path], node: str, delta: Any) -> None:
    """Append executed tool calls to the per-run message_tool.log.

    Tool activity is intentionally kept out of the UI (it's noise for an
    investor); it lives in the log file for debugging, mirroring the CLI's
    ``message_tool.log`` convention.
    """
    names = _tool_names(delta)
    if not names or log_file is None:
        return
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(log_file, "a", encoding="utf-8") as fh:
            for name in names:
                fh.write(f"{ts} [Tool Call] {name} (via {node})\n")
    except OSError as exc:  # noqa: BLE001 — logging must never break a run
        logger.warning("could not write tool log: %s", exc)


def _extract_event(node: str, delta: Any, merged: Optional[Dict[str, Any]] = None) -> Optional[dict]:
    """Turn a LangGraph ``{node: delta}`` update into a UI event.

    Returns ``None`` for internal nodes the UI should ignore (message-clearing
    and tool nodes). Analyst tool rounds (empty report) surface as a lightweight
    ``running`` event; a populated report surfaces as ``done``.
    """
    if not isinstance(delta, dict):
        return None

    # Message-clearing and tool-execution nodes are not shown in the UI.
    if node.startswith("Msg Clear") or node.startswith("tools_"):
        return None

    # Analyst nodes write a *_report; empty means it's still calling tools.
    if node in ("Market Analyst", "Sentiment Analyst", "News Analyst", "Fundamentals Analyst"):
        report = delta.get(REPORT_FIELD[node], "") or ""
        if not report.strip() and merged:
            report = merged.get(REPORT_FIELD[node], "") or ""
        if report.strip():
            return {"type": "agent", "node": node, "status": "done", "content": report}
        return {"type": "agent", "node": node, "status": "running", "content": None}

    # Researchers write into investment_debate_state.current_response.
    if node in ("Bull Researcher", "Bear Researcher"):
        ids = delta.get("investment_debate_state", {}) or {}
        return {
            "type": "agent",
            "node": node,
            "status": "done",
            "content": ids.get("current_response", ""),
        }

    # Risk debators write into risk_debate_state.
    if node in RISK_RESPONSE_FIELD:
        rds = delta.get("risk_debate_state", {}) or {}
        return {
            "type": "agent",
            "node": node,
            "status": "done",
            "content": rds.get(RISK_RESPONSE_FIELD[node], ""),
        }

    # Managers / trader write a dedicated field.
    field_name = REPORT_FIELD.get(node)
    if field_name:
        return {
            "type": "agent",
            "node": node,
            "status": "done",
            "content": delta.get(field_name, "") or "",
        }

    return None


def _run_pipeline(run: Run, req: RunRequest) -> None:
    """Worker-thread body: build the graph, stream it, persist, emit events."""
    try:
        config = DEFAULT_CONFIG.copy()
        if req.provider:
            config["llm_provider"] = req.provider
        if req.deep_model:
            config["deep_think_llm"] = req.deep_model
        if req.quick_model:
            config["quick_think_llm"] = req.quick_model
        if req.max_debate_rounds is not None:
            config["max_debate_rounds"] = req.max_debate_rounds
        if req.max_risk_rounds is not None:
            config["max_risk_discuss_rounds"] = req.max_risk_rounds

        manager.emit(run, {"type": "status", "message": "Initializing agents..."})
        ta = TradingAgentsGraph(
            selected_analysts=req.analysts,
            debug=False,
            config=config,
        )
        ta.ticker = req.ticker

        # Reflect on prior same-ticker runs (cross-run learning), best-effort.
        manager.emit(run, {"type": "status", "message": "Resolving identity & loading memory..."})
        manager.emit(run, {"type": "memory", "phase": "resolve", "status": "start"})
        pending_before = len(
            [e for e in ta.memory_log.get_pending_entries() if e["ticker"] == req.ticker]
        )
        try:
            ta._resolve_pending_entries(req.ticker)
        except Exception as exc:  # noqa: BLE001 — never block a run on memory I/O
            logger.warning("resolve_pending_entries failed: %s", exc)
        pending_after = len(
            [e for e in ta.memory_log.get_pending_entries() if e["ticker"] == req.ticker]
        )
        manager.emit(
            run,
            {
                "type": "memory",
                "phase": "resolve",
                "status": "done",
                "resolved": max(0, pending_before - pending_after),
            },
        )

        past_context = ta.memory_log.get_past_context(req.ticker)
        manager.emit(
            run,
            {
                "type": "memory",
                "phase": "inject",
                "has_context": bool((past_context or "").strip()),
            },
        )

        instrument_context = ta.resolve_instrument_context(req.ticker, req.asset_type)
        manager.emit(run, {"type": "memory", "phase": "identity", "status": "done"})

        # ── Robinhood grounding (opt-in) ──────────────────────────────────
        # When enabled and already authorised, fold the user's real buying
        # power and existing position into the context every agent sees. Never
        # blocks or fails the run; a broker hiccup just means no grounding.
        rh_cfg = load_robinhood_config(config)
        account_snapshot = None
        if rh_cfg.enabled and (rh_cfg.grounding_enabled or rh_cfg.executes_orders):
            try:
                broker = get_broker()
                if not broker.is_connected and broker.has_saved_credentials():
                    broker.connect()
                if broker.is_connected:
                    account_snapshot = broker.get_account_snapshot()
                    if rh_cfg.grounding_enabled:
                        acct_ctx = build_account_context(account_snapshot, req.ticker)
                        if acct_ctx.strip():
                            instrument_context = f"{instrument_context}\n{acct_ctx}"
                    manager.emit(
                        run,
                        {
                            "type": "account",
                            "connected": True,
                            "buying_power": account_snapshot.buying_power,
                            "portfolio_value": account_snapshot.portfolio_value,
                            "position": account_snapshot.position_for(req.ticker),
                        },
                    )
                else:
                    manager.emit(
                        run,
                        {
                            "type": "account",
                            "connected": False,
                            "message": "Robinhood enabled but not connected. "
                            "Use Connect to authorize.",
                        },
                    )
            except Exception as exc:  # noqa: BLE001 — grounding is best-effort
                logger.warning("robinhood grounding failed: %s", exc)

        manager.emit(run, {"type": "identity", "content": instrument_context})

        init_state = ta.propagator.create_initial_state(
            req.ticker,
            req.trade_date,
            asset_type=req.asset_type,
            past_context=past_context,
            instrument_context=instrument_context,
        )
        # get_graph_args() bakes in stream_mode="values"; we want per-node
        # deltas ("updates") so we can attribute output to each agent. The token
        # tracker is a graph-level callback: it sees every nested LLM call and
        # attributes its (exact) token usage to the issuing node via the
        # langgraph_node run metadata. Cost is estimated downstream.
        usage = TokenUsageTracker()
        args = ta.propagator.get_graph_args(callbacks=[usage])
        args.pop("stream_mode", None)

        checkpoint_enabled = bool(config.get("checkpoint_enabled"))
        if checkpoint_enabled:
            tid = thread_id(req.ticker, str(req.trade_date))
            args.setdefault("config", {}).setdefault("configurable", {})["thread_id"] = tid

        # Per-run tool log (mirrors the CLI's message_tool.log). Tool activity
        # is written here instead of being surfaced in the UI.
        log_file: Optional[Path] = None
        try:
            run_dir = Path(config["results_dir"]) / req.ticker / str(req.trade_date)
            run_dir.mkdir(parents=True, exist_ok=True)
            log_file = run_dir / "message_tool.log"
            log_file.touch(exist_ok=True)
        except OSError as exc:  # noqa: BLE001
            logger.warning("could not create tool log: %s", exc)

        merged: Dict[str, Any] = {}
        agent_outputs: List[Dict[str, Any]] = []  # for persistence (one row per agent)
        seq = 0
        manager.emit(run, {"type": "status", "message": "Running pipeline..."})
        for chunk in ta.graph.stream(init_state, stream_mode="updates", **args):
            if not isinstance(chunk, dict):
                continue
            for node, delta in chunk.items():
                if isinstance(delta, dict):
                    merged.update(delta)
                if node.startswith("tools_"):
                    _log_tool_calls(log_file, node, delta)
                    manager.emit(
                        run,
                        {
                            "type": "graph",
                            "node": node,
                            "status": "active",
                            "tools": _tool_names(delta),
                        },
                    )
                    continue
                event = _extract_event(node, delta, merged)
                if event is not None:
                    if event["type"] == "agent" and event.get("status") == "done":
                        agent_outputs.append(
                            {"seq": seq, "agent": event["node"], "content": event.get("content") or ""}
                        )
                        seq += 1
                    manager.emit(run, event)
                    if event["type"] == "agent":
                        idx = run.nodes.index(event["node"]) if event["node"] in run.nodes else -1
                        if idx >= 0:
                            completed = run.nodes[: idx + 1] if event.get("status") == "done" else run.nodes[:idx]
                            active = (
                                run.nodes[idx + 1]
                                if event.get("status") == "done" and idx + 1 < len(run.nodes)
                                else event["node"]
                            )
                            manager.emit(
                                run,
                                {
                                    "type": "progress",
                                    "active": active,
                                    "completed": completed,
                                    "node": event["node"],
                                    "status": event.get("status"),
                                },
                            )
                    # Surface this node's token/cost the moment it finishes, so
                    # the UI's economics panel fills in live alongside the feed.
                    if event["type"] == "agent" and event.get("status") == "done":
                        snapshot = usage.node_snapshot(event["node"])
                        if snapshot:
                            manager.emit(run, {"type": "usage", **snapshot})

        # Authoritative end-of-run breakdown + totals (covers any node we didn't
        # emit incrementally, e.g. an "Ungrouped" bucket).
        manager.emit(run, {"type": "usage_summary", **usage.summary()})

        final_state = merged
        decision = ta.process_signal(final_state.get("final_trade_decision", ""))

        # Persist exactly like _run_graph does (logs + pending memory entry).
        try:
            ta._log_state(req.trade_date, final_state)
            ta.memory_log.store_decision(
                ticker=req.ticker,
                trade_date=req.trade_date,
                final_trade_decision=final_state.get("final_trade_decision", ""),
            )
            manager.emit(run, {"type": "memory", "phase": "store", "status": "done"})
        except Exception as exc:  # noqa: BLE001 — persistence must not fail the response
            logger.warning("persistence failed: %s", exc)

        if checkpoint_enabled:
            try:
                clear_checkpoint(config["data_cache_dir"], req.ticker, str(req.trade_date))
            except Exception as exc:  # noqa: BLE001
                logger.warning("clear_checkpoint failed: %s", exc)

        # Persist the run + per-agent outputs to Supabase (fail-open).
        db.store_run(
            meta={
                "ticker": req.ticker,
                "trade_date": req.trade_date,
                "asset_type": req.asset_type,
                "provider": config.get("llm_provider"),
                "deep_model": config.get("deep_think_llm"),
                "quick_model": config.get("quick_think_llm"),
                "decision": decision,
                "final_content": final_state.get("final_trade_decision", ""),
                "identity": instrument_context,
            },
            agents=agent_outputs,
        )

        manager.emit(
            run,
            {
                "type": "final",
                "decision": decision,
                "content": final_state.get("final_trade_decision", ""),
            },
        )

        # ── Robinhood execution (opt-in) ──────────────────────────────────
        # Translate the rating into an order. In "manual" mode this only
        # *proposes* the order (the UI places it on a button click); in "auto"
        # mode it places immediately. dry_run simulates in either case; a real
        # order needs trade_mode in {manual,auto} AND dry_run=False.
        if rh_cfg.executes_orders:
            try:
                broker = get_broker()
                if broker.is_connected:
                    result = AutoTradeExecutor(broker, rh_cfg).execute(
                        decision, req.ticker, account_snapshot
                    )
                    # Stash a proposed order so the button can place it later.
                    if result.status == "proposed" and result.intent is not None:
                        run.pending_order = result.intent.to_dict()
                        run.order_ticker = req.ticker
                    manager.emit(
                        run,
                        {
                            "type": "trade",
                            "trade_mode": rh_cfg.trade_mode,
                            "can_place_real_orders": rh_cfg.can_place_real_orders,
                            "dry_run": rh_cfg.dry_run,
                            **result.to_dict(),
                        },
                    )
                else:
                    manager.emit(
                        run,
                        {
                            "type": "trade",
                            "status": "error",
                            "message": "Robinhood not connected — connect first.",
                        },
                    )
            except Exception as exc:  # noqa: BLE001 — never fail the response
                logger.exception("trade execution failed")
                manager.emit(
                    run,
                    {"type": "trade", "status": "error", "message": str(exc)},
                )
    except Exception as exc:  # noqa: BLE001 — surface any failure to the UI
        logger.exception("run failed")
        manager.emit(
            run,
            {"type": "error", "message": str(exc), "trace": traceback.format_exc()},
        )
    finally:
        manager.emit(run, {"type": "done"})


app = FastAPI(title="StockAgents")


# Loose RFC-5322-ish check; intentionally permissive (we only gate access, we
# don't verify deliverability). Avoids pulling in the email-validator dependency.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# ── blueprint access tokens (allowlist-gated, fail-closed) ─────────────────────
BLUEPRINT_TOKEN_TTL = 7 * 24 * 3600  # 7 days


def _blueprint_secret() -> Optional[str]:
    """Server-side signing secret for blueprint tokens.

    Prefers an explicit secret; falls back to the Supabase service key (already
    secret and stable across restarts). Returns None when nothing is available,
    in which case access is denied (fail-closed)."""
    return (
        os.environ.get("STOCKAGENTS_BLUEPRINT_SECRET")
        or os.environ.get("SUPABASE_KEY")
        or os.environ.get("STOCKAGENTS_ADMIN_PASSWORD")
        or None
    )


def _mint_blueprint_token(email: str, secret: str, ttl: int = BLUEPRINT_TOKEN_TTL) -> str:
    exp = int(time.time()) + ttl
    msg = f"{email}|{exp}"
    sig = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{msg}|{sig}".encode()).decode()


def _verify_blueprint_token(token: str, secret: str) -> Optional[str]:
    """Return the email embedded in a valid, unexpired token, else None."""
    if not token or not secret:
        return None
    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode()
        email, exp, sig = raw.rsplit("|", 2)
        expected = hmac.new(secret.encode(), f"{email}|{exp}".encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        if int(exp) < int(time.time()):
            return None
        return email
    except Exception:  # noqa: BLE001 — any malformed token is simply invalid
        return None


def _graph_payload(analysts: str, max_debate_rounds: int, max_risk_rounds: int, include_internal: bool) -> dict:
    selected = [a.strip() for a in analysts.split(",") if a.strip()]
    if not selected:
        raise HTTPException(status_code=400, detail="select at least one analyst")
    try:
        return build_graph_blueprint(
            selected_analysts=selected,
            max_debate_rounds=max(1, max_debate_rounds),
            max_risk_rounds=max(1, max_risk_rounds),
            include_internal=include_internal,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/graph/blueprint")
async def get_graph_blueprint(
    analysts: str = "market,social,news,fundamentals",
    max_debate_rounds: int = DEFAULT_CONFIG["max_debate_rounds"],
    max_risk_rounds: int = DEFAULT_CONFIG["max_risk_discuss_rounds"],
    include_internal: int = 1,
    token: str = "",
) -> dict:
    """Full LangGraph blueprint — gated to allowlisted emails (fail-closed).

    Requires a valid token from /api/blueprint/access AND re-checks that the
    token's email is still on the allowlist, so removing a row revokes access."""
    secret = _blueprint_secret()
    email = _verify_blueprint_token(token, secret) if secret else None
    if not email:
        raise HTTPException(status_code=403, detail="blueprint access requires a verified email")
    loop = asyncio.get_running_loop()
    allowed = await loop.run_in_executor(None, db.is_blueprint_email_allowed, email)
    if allowed is not True:  # False (removed) or None (cannot verify) → deny
        raise HTTPException(status_code=403, detail="this email is no longer authorized")
    return _graph_payload(analysts, max_debate_rounds, max_risk_rounds, bool(include_internal))


@app.get("/blueprint")
def blueprint_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "blueprint.html")


class BlueprintAccess(BaseModel):
    email: str
    analysts: Optional[str] = None


@app.post("/api/blueprint/access")
async def blueprint_access(req: BlueprintAccess, request: Request) -> dict:
    """Allowlist gate for the blueprint (fail-closed).

    Grants access only when the email is already present in blueprint_leads.
    The app never inserts — the allowlist is managed manually in Supabase. On
    success, returns a signed token the client sends with blueprint requests."""
    email = (req.email or "").strip().lower()
    if not _EMAIL_RE.match(email) or len(email) > 254:
        raise HTTPException(status_code=400, detail="enter a valid email address")
    secret = _blueprint_secret()
    if not secret:
        raise HTTPException(status_code=503, detail="blueprint access is not configured")
    loop = asyncio.get_running_loop()
    allowed = await loop.run_in_executor(None, db.is_blueprint_email_allowed, email)
    if allowed is not True:  # False (not listed) or None (cannot verify) → deny
        raise HTTPException(status_code=403, detail="this email is not authorized for the blueprint")
    token = _mint_blueprint_token(email, secret)
    return {"ok": True, "token": token, "expires_in": BLUEPRINT_TOKEN_TTL}


@app.get("/api/config")
def get_config() -> dict:
    """Defaults used to pre-fill the form."""
    return {
        "provider": DEFAULT_CONFIG["llm_provider"],
        "deep_model": DEFAULT_CONFIG["deep_think_llm"],
        "quick_model": DEFAULT_CONFIG["quick_think_llm"],
        "max_debate_rounds": DEFAULT_CONFIG["max_debate_rounds"],
        "max_risk_rounds": DEFAULT_CONFIG["max_risk_discuss_rounds"],
        "analysts": list(ANALYST_DISPLAY.keys()),
        "analyst_display": ANALYST_DISPLAY,
        "supabase_configured": db.is_configured(),
        "admin_available": bool(ADMIN_PASSWORD),
        "cache_window_minutes": CACHE_WINDOW_MINUTES,
        "robinhood": load_robinhood_config(DEFAULT_CONFIG).public_status(),
    }


@app.get("/api/robinhood/status")
async def robinhood_status() -> dict:
    """Current Robinhood integration state (config gates + connection)."""
    cfg = load_robinhood_config(DEFAULT_CONFIG)
    if not cfg.enabled:
        # Don't construct/connect a broker when the feature is off.
        return {**cfg.public_status(), "connected": False, "available": True}
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: get_broker().status())


@app.post("/api/robinhood/connect")
async def robinhood_connect() -> dict:
    """Authorize the Robinhood MCP (opens a browser for OAuth on first use).

    Blocking OAuth/handshake work runs in the thread pool so the event loop
    stays responsive. Returns the resulting status (never raises on auth
    failure — the error is reported in the payload)."""
    cfg = load_robinhood_config(DEFAULT_CONFIG)
    if not cfg.enabled:
        raise HTTPException(
            status_code=503,
            detail="Robinhood integration is disabled. Set TRADINGAGENTS_ROBINHOOD_ENABLED=true.",
        )
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: get_broker().connect())


@app.post("/api/robinhood/orders/{run_id}")
async def place_order(run_id: str, body: PlaceOrderRequest) -> dict:
    """Place the order proposed by a finished run (manual mode, button click).

    Applies optional ticket edits, re-clamps server-side, honors dry_run, and is
    idempotent. Blocking broker work runs in the thread pool."""
    cfg = load_robinhood_config(DEFAULT_CONFIG)
    if not cfg.enabled or cfg.trade_mode == "off":
        raise HTTPException(status_code=409, detail="Robinhood execution is off.")
    run = manager.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="unknown or expired run")
    if not run.pending_order:
        raise HTTPException(status_code=409, detail="no pending order for this run")
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, lambda: _place_pending_order(run, body, cfg)
    )


@app.post("/api/runs")
async def start_run(req: RunRequest) -> dict:
    if not req.ticker.strip():
        raise HTTPException(status_code=400, detail="ticker is required")
    if not req.analysts:
        raise HTTPException(status_code=400, detail="select at least one analyst")

    loop = asyncio.get_running_loop()

    # 60-minute cache: if enabled and a recent run exists, serve it instantly.
    if not req.force:
        enabled = await loop.run_in_executor(None, db.get_cache_enabled)
        if enabled:
            recent = await loop.run_in_executor(
                None, db.get_recent_run, req.ticker, req.trade_date, CACHE_WINDOW_MINUTES
            )
            if recent:
                return {"cached": True, "run": recent}

    nodes = _planned_nodes(req.analysts)
    run = manager.create(loop, nodes)
    manager.emit(run, {"type": "nodes", "nodes": nodes})

    thread = threading.Thread(target=_run_pipeline, args=(run, req), daemon=True)
    thread.start()

    return {"cached": False, "run_id": run.run_id, "nodes": nodes}


@app.get("/api/chart")
def get_chart(ticker: str, trade_date: str, asset_type: str = "stock", lookback: int = 180) -> dict:
    """Candlestick + indicator data for the Market Analyst card. Defined as a
    sync endpoint so FastAPI runs the blocking yfinance/stockstats work in its
    thread pool rather than on the event loop."""
    if not ticker.strip():
        raise HTTPException(status_code=400, detail="ticker is required")
    try:
        return build_chart_payload(ticker, trade_date, lookback=lookback)
    except Exception as exc:  # noqa: BLE001 — surface as a clean 404 for the UI
        raise HTTPException(status_code=404, detail=f"no chart data: {exc}")


def _require_admin(password: Optional[str]) -> None:
    if not ADMIN_PASSWORD:
        raise HTTPException(status_code=503, detail="admin features are not configured")
    if password != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="invalid admin password")


@app.get("/api/admin/settings")
def admin_settings(x_admin_password: Optional[str] = Header(default=None)) -> dict:
    _require_admin(x_admin_password)
    return {
        "cache_enabled": db.get_cache_enabled(),
        "supabase_configured": db.is_configured(),
        "cache_window_minutes": CACHE_WINDOW_MINUTES,
    }


@app.post("/api/admin/cache")
def admin_set_cache(
    body: CacheToggle, x_admin_password: Optional[str] = Header(default=None)
) -> dict:
    _require_admin(x_admin_password)
    if not db.is_configured():
        raise HTTPException(status_code=503, detail="Supabase is not configured")
    ok = db.set_cache_enabled(body.enabled)
    if not ok:
        raise HTTPException(status_code=500, detail="could not update setting")
    return {"cache_enabled": body.enabled}


@app.get("/api/runs/{run_id}")
def run_status(run_id: str) -> dict:
    """Whether a run is still active or replayable (for session restore)."""
    run = manager.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="unknown or expired run")
    return {
        "run_id": run_id,
        "active": not run.done.is_set(),
        "finished": run.done.is_set(),
        "nodes": run.nodes,
        "events": len(run.event_log),
    }


@app.get("/api/runs/{run_id}/events")
async def run_events(run_id: str, request: Request) -> StreamingResponse:
    run = manager.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="unknown or expired run")

    # Resume from the client's last-seen event index. EventSource auto-reconnect
    # sends Last-Event-ID, so a dropped connection (tab backgrounded, network
    # blip) picks up exactly where it left off instead of replaying or losing
    # events. A fresh connection (no header) replays from the start — which is
    # what a page reload / session-restore wants.
    last_id = request.headers.get("last-event-id")
    start = 0
    if last_id:
        try:
            start = max(0, int(last_id) + 1)
        except ValueError:
            start = 0

    async def event_stream():
        cursor = start
        while True:
            while cursor < len(run.event_log):
                event = run.event_log[cursor]
                yield f"id: {cursor}\ndata: {json.dumps(event)}\n\n"
                is_done = event.get("type") == "done"
                cursor += 1
                if is_done:
                    return
            if run.done.is_set():
                return
            try:
                await asyncio.wait_for(run.queue.get(), timeout=15)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


# Serve the static assets (index.html and anything alongside it).
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
