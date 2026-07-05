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
import ipaddress
import json
import logging
import os
import re
import threading
import time
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Header, HTTPException, Request, Response
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
from tradingagents.dataflows.utils import safe_ticker_component
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

# ── Per-user run quota ─────────────────────────────────────────────────────────
# A per-user cap on how many analyses can be triggered in a rolling window.
# Each run burns ~$30 of LLM tokens, so without a cap a few users can rack up
# real spend overnight. Tunable via env so the operator can loosen/tighten the
# limit without a redeploy. Owner (admin-password unlocked) is always exempt.
RUN_LIMIT_PER_USER = int(os.environ.get("STOCKAGENTS_RUN_LIMIT_PER_USER", "1"))
RUN_LIMIT_WINDOW_HOURS = int(os.environ.get("STOCKAGENTS_RUN_LIMIT_WINDOW_HOURS", "24"))

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


class AdminLogin(BaseModel):
    password: str


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
    # Requester analytics context (visitor/session/geo/device) for outcome events.
    analytics: Optional[dict] = None


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
    _t0 = time.time()
    run_summary: Dict[str, Any] = {}
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
            # ticker/trade_date are raw client input; validate both before they
            # flow into a filesystem path so they can't traverse out of
            # results_dir (e.g. "../../etc"). ``safe_ticker_component`` rejects
            # path separators; trade_date must be a plain ISO date.
            safe_ticker = safe_ticker_component(req.ticker)
            date.fromisoformat(str(req.trade_date))
            results_root = Path(config["results_dir"]).resolve()
            run_dir = (results_root / safe_ticker / str(req.trade_date)).resolve()
            if not run_dir.is_relative_to(results_root):
                raise ValueError("resolved run directory escapes results_dir")
            run_dir.mkdir(parents=True, exist_ok=True)
            log_file = run_dir / "message_tool.log"
            log_file.touch(exist_ok=True)
        except (OSError, ValueError) as exc:  # noqa: BLE001
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
        run_summary = usage.summary()
        manager.emit(run, {"type": "usage_summary", **run_summary})

        # ``stream_mode="updates"`` only yields each node's *delta*, so ``merged``
        # never carries the init-state-only keys (``company_of_interest``,
        # ``trade_date``, ``asset_type``, ``instrument_context``, ``past_context``).
        # ``_log_state`` reads ``final_state["company_of_interest"]`` directly, so
        # layer the initial state underneath the streamed deltas to reconstruct the
        # full state the CLI/values path would have produced.
        final_state = {**init_state, **merged}
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

        # Persist the run + per-agent outputs to Supabase (fail-open). Pass the
        # in-memory ``run_id`` so the Supabase ``runs.id`` matches the id the
        # client and the voice worker key on. Without this the row gets a fresh
        # ``gen_random_uuid()`` the out-of-process voice worker can never find,
        # so every Talk attempt loads no context and disconnects.
        db.store_run(
            meta={
                "run_id": run.run_id,
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

        # Analytics: a run finished successfully (latency + token economics).
        totals = (run_summary or {}).get("totals", {})
        db.store_event(
            {
                **(run.analytics or {}),
                "event_type": "run_completed",
                "props": {
                    "ticker": req.ticker,
                    "asset_type": req.asset_type,
                    "analysts": req.analysts,
                    "provider": config.get("llm_provider"),
                    "deep_model": config.get("deep_think_llm"),
                    "decision": decision,
                    "latency_ms": int((time.time() - _t0) * 1000),
                    "total_tokens": totals.get("total_tokens"),
                    "cost": totals.get("cost"),
                },
            }
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
        # Full traceback is logged server-side only; the client-facing event
        # carries a generic message so we don't leak internal stack frames.
        logger.exception("run failed")
        manager.emit(
            run,
            {"type": "error", "message": str(exc)},
        )
        db.store_event(
            {
                **(run.analytics or {}),
                "event_type": "run_failed",
                "props": {
                    "ticker": req.ticker,
                    "asset_type": req.asset_type,
                    "latency_ms": int((time.time() - _t0) * 1000),
                    "error": str(exc)[:300],
                },
            }
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

    Requires a dedicated secret (``STOCKAGENTS_BLUEPRINT_SECRET`` or, failing
    that, the admin password). The Supabase *service-role* key is deliberately
    NOT reused here: it grants full database access, so leaking it via a
    token-signing bug would be far worse than a signing key. Returns None when
    nothing is available, in which case access is denied (fail-closed)."""
    return (
        os.environ.get("STOCKAGENTS_BLUEPRINT_SECRET")
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


# ── admin session cookie (replaces client-held password) ──────────────────────
# The browser must never persist the raw admin password (it previously lived in
# localStorage, readable by any XSS). Instead, a successful password check mints
# a short-lived, signed session token delivered as an HttpOnly cookie: JS can't
# read it, and it's signed with the admin password itself, so rotating the
# password invalidates every outstanding session.
ADMIN_SESSION_COOKIE = "sa_admin_session"
ADMIN_SESSION_TTL = 12 * 3600  # 12 hours


def _mint_admin_session(ttl: int = ADMIN_SESSION_TTL) -> Optional[str]:
    """Sign an admin session token, or None when no admin password is set."""
    if not ADMIN_PASSWORD:
        return None
    exp = int(time.time()) + ttl
    msg = f"admin|{exp}"
    sig = hmac.new(ADMIN_PASSWORD.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{msg}|{sig}".encode()).decode()


def _verify_admin_session(token: Optional[str]) -> bool:
    """True iff the token is a valid, unexpired admin session for the current password."""
    if not token or not ADMIN_PASSWORD:
        return False
    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode()
        label, exp, sig = raw.rsplit("|", 2)
        if label != "admin":
            return False
        expected = hmac.new(ADMIN_PASSWORD.encode(), f"{label}|{exp}".encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return False
        return int(exp) >= int(time.time())
    except Exception:  # noqa: BLE001 — any malformed token is simply invalid
        return False


def _cookie_secure(request: Optional[Request]) -> bool:
    """Whether to set the Secure flag — on for HTTPS (incl. behind a TLS proxy)."""
    if request is None:
        return False
    if request.url.scheme == "https":
        return True
    proto = request.headers.get("x-forwarded-proto", "")
    return proto.split(",")[0].strip().lower() == "https"


def _set_admin_cookie(request: Request, response: Response, token: str) -> None:
    response.set_cookie(
        ADMIN_SESSION_COOKIE,
        token,
        max_age=ADMIN_SESSION_TTL,
        httponly=True,
        samesite="strict",
        secure=_cookie_secure(request),
        path="/",
    )


def _clear_admin_cookie(response: Response) -> None:
    response.delete_cookie(ADMIN_SESSION_COOKIE, path="/")


def _admin_authed(request: Optional[Request], password: Optional[str]) -> bool:
    """True if the caller is the operator, via a valid session cookie OR the
    legacy ``X-Admin-Password`` header (kept for CLI/programmatic callers).

    Returns False when no admin password is configured, so a misconfiguration
    can't accidentally grant everyone owner access."""
    if not ADMIN_PASSWORD:
        return False
    if password and hmac.compare_digest(password, ADMIN_PASSWORD):
        return True
    cookie = request.cookies.get(ADMIN_SESSION_COOKIE) if request is not None else None
    return _verify_admin_session(cookie)


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
async def robinhood_connect(
    request: Request,
    x_admin_password: Optional[str] = Header(default=None),
) -> dict:
    """Authorize the Robinhood MCP (opens a browser for OAuth on first use).

    These legacy endpoints drive the operator's single, process-wide broker, so
    they are admin-gated (fail closed when no admin password is configured) —
    per-user broker operations live under the v2 ``/api/v2/*`` surface.

    Blocking OAuth/handshake work runs in the thread pool so the event loop
    stays responsive. Returns the resulting status (never raises on auth
    failure — the error is reported in the payload)."""
    _require_admin(request, x_admin_password)
    cfg = load_robinhood_config(DEFAULT_CONFIG)
    if not cfg.enabled:
        raise HTTPException(
            status_code=503,
            detail="Robinhood integration is disabled. Set TRADINGAGENTS_ROBINHOOD_ENABLED=true.",
        )
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: get_broker().connect())


def _account_payload() -> dict:
    """Read-only account snapshot, connecting headlessly from saved creds if needed."""
    broker = get_broker()
    if not broker.is_connected and broker.has_saved_credentials():
        try:
            broker.connect()
        except Exception:  # noqa: BLE001 — report as "not connected", never raise
            pass
    if not broker.is_connected:
        return {
            "connected": False,
            "message": "Robinhood not connected. Use Connect to authorize.",
        }
    snap = broker.get_account_snapshot()
    holdings = broker.get_holdings()
    return {
        "connected": True,
        **snap.to_dict(),
        # Full portfolio across all accounts (main brokerage holds the stock; the
        # agentic account is usually just cash for trading).
        "holdings": holdings,
        "holdings_count": len(holdings),
    }


@app.get("/api/robinhood/account")
async def robinhood_account(
    request: Request,
    x_admin_password: Optional[str] = Header(default=None),
) -> dict:
    """Live account snapshot — buying power, portfolio value, positions.

    Discloses the operator's account, so it is admin-gated (fail closed when no
    admin password is configured). Read-only (never places an order). Returns
    ``{connected: false, ...}`` instead of raising when the broker isn't
    authorized yet. Blocking broker work runs in the thread pool."""
    _require_admin(request, x_admin_password)
    cfg = load_robinhood_config(DEFAULT_CONFIG)
    if not cfg.enabled:
        raise HTTPException(
            status_code=503,
            detail="Robinhood integration is disabled. Set TRADINGAGENTS_ROBINHOOD_ENABLED=true.",
        )
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _account_payload)


@app.post("/api/robinhood/orders/{run_id}")
async def place_order(
    run_id: str,
    body: PlaceOrderRequest,
    request: Request,
    x_admin_password: Optional[str] = Header(default=None),
) -> dict:
    """Place the order proposed by a finished run (manual mode, button click).

    Places a live order through the operator's process-wide broker, so it is
    admin-gated (fail closed when no admin password is configured) in addition
    to requiring the run to still exist with a pending order. Applies optional
    ticket edits, re-clamps server-side, honors dry_run, and is idempotent.
    Blocking broker work runs in the thread pool."""
    _require_admin(request, x_admin_password)
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


# Privacy: by default we store an anonymized IP (drop the last IPv4 octet /
# zero the IPv6 host bits) — still useful for geo/abuse, far less identifying.
# Set ANALYTICS_ANONYMIZE_IP=false to retain full IPs.
_ANONYMIZE_IP = os.environ.get("ANALYTICS_ANONYMIZE_IP", "true").strip().lower() != "false"

# ``X-Forwarded-For`` / ``X-Real-IP`` are client-supplied and trivially
# spoofable, so trusting them lets an attacker rotate the IP used for the run
# quota. Only honour them when we're explicitly told we sit behind a trusted
# edge proxy (Railway/Cloud PaaS). Default OFF: use the direct socket peer.
_TRUST_PROXY_HEADERS = os.environ.get("TRUST_PROXY_HEADERS", "").strip().lower() in (
    "1",
    "true",
    "yes",
)


def _client_ip(request: Request) -> tuple[Optional[str], Optional[str]]:
    """Best-effort client IP, proxy-aware. Returns (ip, raw_forwarded_for).

    When ``TRUST_PROXY_HEADERS`` is enabled (the app sits behind a known edge
    proxy, e.g. Railway), the real client is the first hop in
    ``X-Forwarded-For`` (falling back to ``X-Real-IP``). Otherwise these headers
    are attacker-controlled and are ignored in favour of the direct socket
    peer, so IP-based quota/geo can't be spoofed by header injection.
    """
    direct = request.client.host if request.client else None
    if not _TRUST_PROXY_HEADERS:
        return direct, None
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        ip = fwd.split(",")[0].strip()
        return (ip or None), fwd
    real = request.headers.get("x-real-ip")
    if real:
        return real.strip(), None
    return direct, None


def _anonymize_ip(ip: Optional[str]) -> Optional[str]:
    """Mask the host portion: IPv4 keeps /24, IPv6 keeps /48."""
    if not ip:
        return ip
    if "." in ip:  # IPv4
        parts = ip.split(".")
        if len(parts) == 4:
            return ".".join(parts[:3] + ["0"])
        return ip
    if ":" in ip:  # IPv6 — keep first 3 hextets
        segs = ip.split(":")
        return ":".join(segs[:3]) + "::"
    return ip


def _parse_user_agent(ua: Optional[str]) -> Dict[str, Optional[str]]:
    """Lightweight UA → {device_type, os, browser}. No external dependency."""
    if not ua:
        return {"device_type": None, "os": None, "browser": None}
    low = ua.lower()
    if any(b in low for b in ("bot", "spider", "crawler", "headless")):
        device = "bot"
    elif "ipad" in low or ("tablet" in low and "mobile" not in low):
        device = "tablet"
    elif any(m in low for m in ("mobi", "iphone", "android")):
        device = "mobile"
    else:
        device = "desktop"
    if "iphone" in low or "ipad" in low or "ios" in low:
        os_name = "iOS"
    elif "android" in low:
        os_name = "Android"
    elif "mac os" in low or "macintosh" in low:
        os_name = "macOS"
    elif "windows" in low:
        os_name = "Windows"
    elif "linux" in low:
        os_name = "Linux"
    else:
        os_name = None
    if "edg" in low:
        browser = "Edge"
    elif "chrome" in low or "crios" in low:
        browser = "Chrome"
    elif "firefox" in low or "fxios" in low:
        browser = "Firefox"
    elif "safari" in low:
        browser = "Safari"
    else:
        browser = None
    return {"device_type": device, "os": os_name, "browser": browser}


def _geo_from_request(request: Request) -> Dict[str, Optional[str]]:
    """Read geo from common CDN/edge headers (populated when fronted by
    Cloudflare/Vercel/Fastly). No IP is sent to any third party; absent headers
    simply yield None."""
    h = request.headers
    country = h.get("cf-ipcountry") or h.get("x-vercel-ip-country") or h.get("x-geo-country")
    region = h.get("x-vercel-ip-country-region") or h.get("cf-region") or h.get("x-geo-region")
    city = h.get("x-vercel-ip-city") or h.get("cf-ipcity") or h.get("x-geo-city")
    return {
        "country": (country or None),
        "region": (region or None),
        "city": (city or None),
    }


# ── server-side GeoIP fallback ────────────────────────────────────────────────
# When no edge/CDN geo headers are present (e.g. on bare Railway), resolve
# country/region/city from the *real* client IP via a free GeoIP API. The lookup
# is best-effort and must never slow down or block a request:
#   • cached in-memory per IP with a long TTL (most visitors repeat),
#   • performed in a daemon thread on a miss — the current request returns empty
#     geo and the cache is warmed for subsequent events from that IP,
#   • fully fail-open: any error/timeout yields empty geo.
# The lookup uses the un-anonymized IP for accuracy, but only the anonymized IP
# is ever stored (see ``_client_meta``).
_EMPTY_GEO: Dict[str, Optional[str]] = {"country": None, "region": None, "city": None}
_GEO_ENABLED = os.environ.get("ANALYTICS_GEOIP", "true").strip().lower() != "false"
_GEO_TTL = float(os.environ.get("ANALYTICS_GEOIP_TTL", "86400"))  # 24h
_GEO_PROVIDER_URL = os.environ.get("ANALYTICS_GEOIP_URL", "https://ipapi.co/{ip}/json/")
_GEO_MAX_CACHE = 10000
_geo_cache: Dict[str, tuple[float, Dict[str, Optional[str]]]] = {}
_geo_inflight: set[str] = set()
_geo_lock = threading.Lock()


def _is_public_ip(ip: str) -> bool:
    """True only for routable, non-private addresses worth geolocating."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def _geo_fetch(ip: str) -> Dict[str, Optional[str]]:
    """Blocking GeoIP request. Only ever called from a background thread."""
    try:
        req = urllib.request.Request(
            _GEO_PROVIDER_URL.format(ip=ip),
            headers={"User-Agent": "TradingAgents/1.0"},
        )
        with urllib.request.urlopen(req, timeout=2.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if not isinstance(data, dict) or data.get("error"):
            return dict(_EMPTY_GEO)
        return {
            "country": data.get("country_code") or data.get("country") or None,
            "region": data.get("region") or data.get("region_code") or None,
            "city": data.get("city") or None,
        }
    except Exception:  # noqa: BLE001 — geo enrichment must never raise
        return dict(_EMPTY_GEO)


def _geo_worker(ip: str) -> None:
    result = _geo_fetch(ip)
    with _geo_lock:
        if len(_geo_cache) >= _GEO_MAX_CACHE:
            _geo_cache.clear()
        _geo_cache[ip] = (time.time(), result)
        _geo_inflight.discard(ip)


def _geo_from_ip(ip: Optional[str]) -> Dict[str, Optional[str]]:
    """Cached, non-blocking GeoIP lookup.

    Returns cached geo when warm; on a miss returns empty geo immediately and
    kicks off a one-shot background lookup to warm the cache for next time.
    """
    if not _GEO_ENABLED or not ip or not _is_public_ip(ip):
        return dict(_EMPTY_GEO)
    now = time.time()
    with _geo_lock:
        hit = _geo_cache.get(ip)
        if hit and (now - hit[0]) < _GEO_TTL:
            return dict(hit[1])
        if ip not in _geo_inflight:
            _geo_inflight.add(ip)
            threading.Thread(target=_geo_worker, args=(ip,), daemon=True).start()
    return dict(_EMPTY_GEO)


def _client_meta(request: Request) -> Dict[str, Any]:
    """Unified, privacy-aware requester context for analytics rows."""
    raw_ip, fwd = _client_ip(request)
    ua = request.headers.get("user-agent")
    meta: Dict[str, Any] = {
        "ip_address": _anonymize_ip(raw_ip) if _ANONYMIZE_IP else raw_ip,
        # forwarded_for holds full IPs; drop it when anonymizing.
        "forwarded_for": None if _ANONYMIZE_IP else fwd,
        "user_agent": ua,
        "referrer": request.headers.get("referer"),
        "language": (request.headers.get("accept-language") or "")[:40] or None,
        "visitor_id": (request.headers.get("x-visitor-id") or "")[:64] or None,
        "session_id": (request.headers.get("x-session-id") or "")[:64] or None,
    }
    meta.update(_parse_user_agent(ua))
    geo = _geo_from_request(request)
    # Fall back to a server-side GeoIP lookup (real IP) when the edge gave us
    # nothing. Cached + background-warmed, so this never blocks the event loop.
    if not any(geo.values()):
        geo = _geo_from_ip(raw_ip)
    meta.update(geo)
    return meta


@app.post("/api/events")
async def track_event(payload: Dict[str, Any], request: Request) -> dict:
    """Record a client-side behavioral event (page_view, pdf_export, ...).

    Fire-and-forget from the client's perspective; always returns ok. The
    event_type and an optional props object come from the body; identity/geo are
    enriched server-side from headers."""
    event_type = str(payload.get("event_type") or "").strip()[:64]
    if not event_type:
        raise HTTPException(status_code=400, detail="event_type is required")
    meta = _client_meta(request)
    meta["event_type"] = event_type
    props = payload.get("props")
    meta["props"] = props if isinstance(props, dict) else None
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, db.store_event, meta)
    return {"ok": True}


def _is_owner(request: Optional[Request], password: Optional[str]) -> bool:
    """Whether this request was made by the operator (admin session or header).

    Used to exempt the operator from the per-user run quota. Returns False
    (treat as anonymous user) when no admin password is configured at all, so
    a misconfiguration can't accidentally grant everyone unlimited runs.
    """
    return _admin_authed(request, password)


def _quota_state(client_meta: Dict[str, Any], owner: bool) -> Dict[str, Any]:
    """Compute the requester's current quota usage and remaining allowance."""
    if owner or RUN_LIMIT_PER_USER <= 0:
        return {
            "limit": RUN_LIMIT_PER_USER,
            "used": 0,
            "remaining": None,  # null = unlimited
            "window_hours": RUN_LIMIT_WINDOW_HOURS,
            "owner": owner,
            "allowed": True,
        }
    used = db.count_quota_consumed(
        client_meta.get("visitor_id"),
        client_meta.get("ip_address"),
        hours=RUN_LIMIT_WINDOW_HOURS,
    )
    return {
        "limit": RUN_LIMIT_PER_USER,
        "used": used,
        "remaining": max(RUN_LIMIT_PER_USER - used, 0),
        "window_hours": RUN_LIMIT_WINDOW_HOURS,
        "owner": False,
        "allowed": used < RUN_LIMIT_PER_USER,
    }


@app.get("/api/runs/quota")
async def get_quota(
    request: Request,
    x_admin_password: Optional[str] = Header(default=None),
) -> dict:
    """How many runs this user has left in the rolling window.

    Lets the UI show "1 free run today / used" *before* the user clicks, instead
    of only learning at submission time. Mirrors the gate in :func:`start_run`.
    """
    loop = asyncio.get_running_loop()
    meta = _client_meta(request)
    owner = _is_owner(request, x_admin_password)
    state = await loop.run_in_executor(None, _quota_state, meta, owner)
    return state


@app.post("/api/runs")
async def start_run(
    req: RunRequest,
    request: Request,
    x_admin_password: Optional[str] = Header(default=None),
) -> dict:
    if not req.ticker.strip():
        raise HTTPException(status_code=400, detail="ticker is required")
    if not req.analysts:
        raise HTTPException(status_code=400, detail="select at least one analyst")

    loop = asyncio.get_running_loop()

    # Log who requested this analysis (fail-open; never blocks the run). Done
    # before the cache check so cache hits are recorded too.
    client_meta = _client_meta(request)
    request_meta = {
        **client_meta,
        "ticker": req.ticker.strip().upper(),
        "trade_date": req.trade_date,
        "asset_type": req.asset_type,
        "analysts": req.analysts,
        "provider": req.provider,
    }
    await loop.run_in_executor(None, db.store_analysis_request, request_meta)
    # Funnel event: an analysis was requested (mirrors the request log but lives
    # in the unified event stream for retention/funnel queries).
    await loop.run_in_executor(
        None,
        db.store_event,
        {
            **client_meta,
            "event_type": "run_requested",
            "props": {
                "ticker": req.ticker.strip().upper(),
                "asset_type": req.asset_type,
                "analysts": req.analysts,
                "provider": req.provider,
                "forced": bool(req.force),
            },
        },
    )

    # 60-minute cache: if enabled and a recent run exists, serve it instantly.
    # Cache hits don't consume the per-user quota — they don't burn LLM tokens.
    if not req.force:
        enabled = await loop.run_in_executor(None, db.get_cache_enabled)
        if enabled:
            recent = await loop.run_in_executor(
                None, db.get_recent_run, req.ticker, req.trade_date, CACHE_WINDOW_MINUTES
            )
            if recent:
                return {"cached": True, "run": recent}

    # ── Per-user quota gate ───────────────────────────────────────────────
    # Owner (admin-password unlocked) is exempt. Everyone else is capped at
    # RUN_LIMIT_PER_USER in a rolling RUN_LIMIT_WINDOW_HOURS window. Failed
    # runs are refunded (db.count_quota_consumed subtracts run_failed).
    owner = _is_owner(request, x_admin_password)
    quota = await loop.run_in_executor(None, _quota_state, client_meta, owner)
    if not quota["allowed"]:
        # 429 with a structured detail the UI can render verbatim.
        raise HTTPException(
            status_code=429,
            detail={
                "code": "run_limit_reached",
                "message": (
                    f"You've used your {quota['limit']} analysis"
                    f"{'s' if quota['limit'] != 1 else ''} for the day. "
                    "Each full agent run costs roughly $3–$5 in LLM tokens "
                    "(more on Opus models), so to keep this app free we cap "
                    f"at {quota['limit']} per {quota['window_hours']}h per user."
                ),
                "limit": quota["limit"],
                "used": quota["used"],
                "window_hours": quota["window_hours"],
            },
        )

    nodes = _planned_nodes(req.analysts)
    run = manager.create(loop, nodes)
    run.analytics = client_meta  # carried into run_completed/run_failed events
    manager.emit(run, {"type": "nodes", "nodes": nodes})

    # Mark quota consumption. Counted against the user immediately so two
    # near-simultaneous submissions don't both slip through the gate. Refunded
    # automatically by run_failed if the run errors out (see _run_pipeline).
    await loop.run_in_executor(
        None,
        db.store_event,
        {
            **client_meta,
            "event_type": "run_started",
            "props": {
                "run_id": run.run_id,
                "ticker": req.ticker.strip().upper(),
                "asset_type": req.asset_type,
                "owner": owner,
            },
        },
    )

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


def _require_admin(request: Optional[Request], password: Optional[str]) -> None:
    if not ADMIN_PASSWORD:
        raise HTTPException(status_code=503, detail="admin features are not configured")
    # Accept a valid session cookie or the legacy header; both use constant-time
    # compares under the hood. Fail closed when the caller presented neither.
    if not _admin_authed(request, password):
        raise HTTPException(status_code=401, detail="invalid admin password")


def _admin_settings_payload() -> dict:
    return {
        "cache_enabled": db.get_cache_enabled(),
        "supabase_configured": db.is_configured(),
        "cache_window_minutes": CACHE_WINDOW_MINUTES,
    }


@app.post("/api/admin/login")
def admin_login(
    body: AdminLogin, request: Request, response: Response
) -> dict:
    """Exchange the admin password for a short-lived HttpOnly session cookie.

    The browser never stores the raw password again — the cookie is signed,
    JS-unreadable, and expires. Returns the settings payload so the client can
    populate the admin panel without a second round trip."""
    _require_admin(request, body.password)
    token = _mint_admin_session()
    if token:
        _set_admin_cookie(request, response, token)
    return {"ok": True, **_admin_settings_payload()}


@app.post("/api/admin/logout")
def admin_logout(response: Response) -> dict:
    """Drop the admin session cookie (best-effort; unauthenticated is fine)."""
    _clear_admin_cookie(response)
    return {"ok": True}


@app.get("/api/admin/settings")
def admin_settings(
    request: Request, x_admin_password: Optional[str] = Header(default=None)
) -> dict:
    _require_admin(request, x_admin_password)
    return _admin_settings_payload()


@app.post("/api/admin/cache")
def admin_set_cache(
    body: CacheToggle,
    request: Request,
    x_admin_password: Optional[str] = Header(default=None),
) -> dict:
    _require_admin(request, x_admin_password)
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


# ── Mobile (iOS) API — additive, default OFF (see web/mobile/) ────────────────
# Registers the versioned /api/v2/* router only when MOBILE_API_ENABLED is set.
# A no-op (and imports nothing further) otherwise, so the web app is unchanged.
# Guarded: the web/mobile package is optional/in-progress, so a missing module
# must degrade gracefully (skip the mobile API) rather than crash the server.
try:
    from web.mobile import include_mobile_api  # noqa: E402
    from web.mobile.settings import load_settings as _load_mobile_settings  # noqa: E402

    include_mobile_api(app)
    # Loud, fail-visible warning: dev auth mints tokens for ANY user_id from a
    # single shared secret, so it must never front a production mobile API.
    _ms = _load_mobile_settings()
    if _ms.enabled and _ms.auth_mode == "dev":
        logger.warning(
            "SECURITY: MOBILE_API_ENABLED with MOBILE_AUTH_MODE=dev — dev "
            "bearer tokens are forgeable by anyone holding the signing secret. "
            "Do NOT use in production; set MOBILE_AUTH_MODE=supabase."
        )
except ImportError as exc:  # noqa: BLE001 — mobile API is optional
    logger.warning("Mobile API unavailable, skipping (/api/v2 disabled): %s", exc)


# ── Voice-conversational API — additive, default OFF (see web/voice/) ─────────
# Mounts /api/voice/* only when TRADINGAGENTS_VOICE_ENABLED=true AND all three
# transport credentials (LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET)
# are set. With the feature off the web app is unchanged (no router, no
# imports beyond the gate). Missing optional voice deps (livekit-agents and
# plugins) are tolerated here because the FastAPI process only mints session
# tokens — the worker is a separate `python -m tradingagents.voice.agent_worker`
# process that owns the heavy dependencies.
try:
    from web.voice import include_voice_api  # noqa: E402

    include_voice_api(app)
except ImportError as exc:  # noqa: BLE001 — voice API is optional
    logger.warning("Voice API unavailable, skipping (/api/voice disabled): %s", exc)
