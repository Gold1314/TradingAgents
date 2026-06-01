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
import json
import logging
import os
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.checkpointer import clear_checkpoint, thread_id
from tradingagents.graph.trading_graph import TradingAgentsGraph
from web import db
from web.charts import build_chart_payload

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


@dataclass
class Run:
    run_id: str
    queue: "asyncio.Queue[dict]"
    loop: asyncio.AbstractEventLoop
    nodes: List[str]
    done: threading.Event = field(default_factory=threading.Event)


class RunManager:
    """Holds active runs and bridges worker threads to SSE consumers."""

    def __init__(self) -> None:
        self._runs: Dict[str, Run] = {}

    def create(self, loop: asyncio.AbstractEventLoop, nodes: List[str]) -> Run:
        run = Run(run_id=uuid.uuid4().hex, queue=asyncio.Queue(), loop=loop, nodes=nodes)
        self._runs[run.run_id] = run
        return run

    def get(self, run_id: str) -> Optional[Run]:
        return self._runs.get(run_id)

    def emit(self, run: Run, event: dict) -> None:
        """Thread-safe push of an event onto the run's asyncio queue."""
        run.loop.call_soon_threadsafe(run.queue.put_nowait, event)

    def finish(self, run_id: str) -> None:
        run = self._runs.pop(run_id, None)
        if run:
            run.done.set()


manager = RunManager()


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


def _extract_event(node: str, delta: Any) -> Optional[dict]:
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
        try:
            ta._resolve_pending_entries(req.ticker)
        except Exception as exc:  # noqa: BLE001 — never block a run on memory I/O
            logger.warning("resolve_pending_entries failed: %s", exc)

        past_context = ta.memory_log.get_past_context(req.ticker)
        instrument_context = ta.resolve_instrument_context(req.ticker, req.asset_type)
        manager.emit(run, {"type": "identity", "content": instrument_context})

        init_state = ta.propagator.create_initial_state(
            req.ticker,
            req.trade_date,
            asset_type=req.asset_type,
            past_context=past_context,
            instrument_context=instrument_context,
        )
        # get_graph_args() bakes in stream_mode="values"; we want per-node
        # deltas ("updates") so we can attribute output to each agent.
        args = ta.propagator.get_graph_args()
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
                    continue
                event = _extract_event(node, delta)
                if event is not None:
                    if event["type"] == "agent" and event.get("status") == "done":
                        agent_outputs.append(
                            {"seq": seq, "agent": event["node"], "content": event.get("content") or ""}
                        )
                        seq += 1
                    manager.emit(run, event)

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
    except Exception as exc:  # noqa: BLE001 — surface any failure to the UI
        logger.exception("run failed")
        manager.emit(
            run,
            {"type": "error", "message": str(exc), "trace": traceback.format_exc()},
        )
    finally:
        manager.emit(run, {"type": "done"})


app = FastAPI(title="StockAgents")


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
    }


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


@app.get("/api/runs/{run_id}/events")
async def run_events(run_id: str) -> StreamingResponse:
    run = manager.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="unknown or completed run")

    async def event_stream():
        # Tell the client which nodes to render in the pipeline graph.
        yield f"data: {json.dumps({'type': 'nodes', 'nodes': run.nodes})}\n\n"
        while True:
            # A single agent (deep model) can run for minutes between events.
            # Emit an SSE comment heartbeat on idle so proxies (Railway) don't
            # drop the connection. EventSource ignores comment lines.
            try:
                event = await asyncio.wait_for(run.queue.get(), timeout=15)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue
            yield f"data: {json.dumps(event)}\n\n"
            if event.get("type") == "done":
                break
        manager.finish(run_id)

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
