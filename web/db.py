"""Supabase persistence + 60-minute cache for StockAgents.

Every function here is **fail-open**: if Supabase is not configured (no
``SUPABASE_URL`` / ``SUPABASE_KEY``) or a call errors, the helpers return
``None`` / ``False`` and the web app continues to work without persistence.

Configuration (in ``.env``):
    SUPABASE_URL=https://<project-ref>.supabase.co
    SUPABASE_KEY=<service_role key>   # server-side only; never exposed to the browser

The synchronous Supabase client is fine here because callers invoke these from
a thread pool / worker thread, not the asyncio event loop.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("tradingagents.web.db")

CACHE_KEY = "cache_enabled"

_client = None
_client_init = False


def is_configured() -> bool:
    return bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_KEY"))


def _get_client():
    """Lazily create (and memoize) the Supabase client, or None if unavailable."""
    global _client, _client_init
    if _client_init:
        return _client
    _client_init = True
    if not is_configured():
        logger.info("Supabase not configured; persistence/cache disabled.")
        _client = None
        return None
    try:
        from supabase import create_client

        _client = create_client(
            os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"]
        )
    except Exception as exc:  # noqa: BLE001 — never hard-fail on DB init
        logger.warning("Could not initialize Supabase client: %s", exc)
        _client = None
    return _client


# ── settings ────────────────────────────────────────────────────────────────
def get_cache_enabled() -> bool:
    """Whether the 60-minute cache is enabled. Defaults to True when a Supabase
    project is configured but the setting row is missing; False when there is no
    Supabase at all (nothing to cache from)."""
    client = _get_client()
    if client is None:
        return False
    try:
        res = client.table("app_settings").select("value").eq("key", CACHE_KEY).limit(1).execute()
        rows = res.data or []
        if not rows:
            return True
        val = rows[0]["value"]
        return bool(val) if not isinstance(val, str) else val.lower() == "true"
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_cache_enabled failed: %s", exc)
        return False


def set_cache_enabled(enabled: bool) -> bool:
    client = _get_client()
    if client is None:
        return False
    try:
        client.table("app_settings").upsert(
            {"key": CACHE_KEY, "value": enabled, "updated_at": _now_iso()}
        ).execute()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("set_cache_enabled failed: %s", exc)
        return False


# ── persistence ───────────────────────────────────────────────────────────────
def store_run(meta: Dict[str, Any], agents: List[Dict[str, Any]]) -> Optional[str]:
    """Persist a completed run + its per-agent outputs. Returns the run id."""
    client = _get_client()
    if client is None:
        return None
    try:
        run_row = {
            "ticker": meta.get("ticker"),
            "trade_date": str(meta.get("trade_date")),
            "asset_type": meta.get("asset_type"),
            "provider": meta.get("provider"),
            "deep_model": meta.get("deep_model"),
            "quick_model": meta.get("quick_model"),
            "decision": meta.get("decision"),
            "final_content": meta.get("final_content"),
            "identity": meta.get("identity"),
        }
        res = client.table("runs").insert(run_row).execute()
        run_id = (res.data or [{}])[0].get("id")
        if run_id and agents:
            rows = [
                {
                    "run_id": run_id,
                    "seq": a.get("seq", i),
                    "agent": a.get("agent"),
                    "content": a.get("content") or "",
                }
                for i, a in enumerate(agents)
            ]
            client.table("agent_outputs").insert(rows).execute()
        return run_id
    except Exception as exc:  # noqa: BLE001
        logger.warning("store_run failed: %s", exc)
        return None


def store_analysis_request(meta: Dict[str, Any]) -> bool:
    """Log a single analysis *request* (who asked for what) to analysis_requests.

    Captures the requester IP / user agent / device / geo for every ``/api/runs``
    call — even cache hits — so attempts are recorded regardless of run
    completion. Fail-open like the rest of this module: a missing table or
    unconfigured Supabase simply returns False and never blocks the analysis.
    """
    client = _get_client()
    if client is None:
        return False
    try:
        row = {
            "ip_address": meta.get("ip_address"),
            "forwarded_for": meta.get("forwarded_for"),
            "user_agent": meta.get("user_agent"),
            "ticker": meta.get("ticker"),
            "trade_date": str(meta.get("trade_date")) if meta.get("trade_date") else None,
            "asset_type": meta.get("asset_type"),
            "analysts": meta.get("analysts"),
            "provider": meta.get("provider"),
            "visitor_id": meta.get("visitor_id"),
            "session_id": meta.get("session_id"),
            "country": meta.get("country"),
            "region": meta.get("region"),
            "city": meta.get("city"),
            "device_type": meta.get("device_type"),
            "os": meta.get("os"),
            "browser": meta.get("browser"),
            "referrer": meta.get("referrer"),
            "language": meta.get("language"),
        }
        client.table("analysis_requests").insert(row).execute()
        return True
    except Exception as exc:  # noqa: BLE001 — logging must never block a run
        logger.warning("store_analysis_request failed: %s", exc)
        return False


def store_event(meta: Dict[str, Any]) -> bool:
    """Append one behavioral event (page_view, run_*, pdf_export, ...) to events.

    The event stream powers acquisition / engagement / retention analytics. The
    ``event_type`` is required; everything else is optional context (``props`` is
    a free-form JSON blob). Fail-open: never blocks a request.
    """
    client = _get_client()
    if client is None:
        return False
    event_type = meta.get("event_type")
    if not event_type:
        return False
    try:
        row = {
            "event_type": event_type,
            "visitor_id": meta.get("visitor_id"),
            "session_id": meta.get("session_id"),
            "props": meta.get("props"),
            "ip_address": meta.get("ip_address"),
            "country": meta.get("country"),
            "region": meta.get("region"),
            "city": meta.get("city"),
            "device_type": meta.get("device_type"),
            "os": meta.get("os"),
            "browser": meta.get("browser"),
            "referrer": meta.get("referrer"),
            "language": meta.get("language"),
            "user_agent": meta.get("user_agent"),
        }
        client.table("events").insert(row).execute()
        return True
    except Exception as exc:  # noqa: BLE001 — analytics must never block a request
        logger.warning("store_event failed: %s", exc)
        return False


def get_recent_run(
    ticker: str, trade_date: str, within_minutes: int = 60
) -> Optional[Dict[str, Any]]:
    """Return the most recent run for (ticker, trade_date) within the window,
    including its agent outputs, or None."""
    client = _get_client()
    if client is None:
        return None
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=within_minutes)).isoformat()
        res = (
            client.table("runs")
            .select("*")
            .eq("ticker", ticker)
            .eq("trade_date", str(trade_date))
            .gte("created_at", cutoff)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        if not rows:
            return None
        run = rows[0]
        agents_res = (
            client.table("agent_outputs")
            .select("seq,agent,content")
            .eq("run_id", run["id"])
            .order("seq")
            .execute()
        )
        run["agents"] = agents_res.data or []
        return run
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_recent_run failed: %s", exc)
        return None


# ── blueprint access allowlist ─────────────────────────────────────────────────
def is_blueprint_email_allowed(email: str) -> Optional[bool]:
    """Whether an email is on the blueprint allowlist (blueprint_leads).

    Returns True/False when Supabase answers, or **None** when access cannot be
    verified (Supabase not configured or a query error). Callers must treat
    None as deny (fail-closed) — this gate is an access restriction, not a
    best-effort capture."""
    client = _get_client()
    if client is None:
        return None
    try:
        res = (
            client.table("blueprint_leads")
            .select("email")
            .eq("email", (email or "").strip().lower())
            .limit(1)
            .execute()
        )
        return bool(res.data)
    except Exception as exc:  # noqa: BLE001
        logger.warning("is_blueprint_email_allowed failed: %s", exc)
        return None


# ── blueprint email capture ───────────────────────────────────────────────────
def store_blueprint_email(email: str, meta: Optional[Dict[str, Any]] = None) -> bool:
    """Record an email that unlocked the blueprint. Fail-open like the rest of db.

    Returns True only when the row was actually written to Supabase; callers
    should treat False as "not persisted" (e.g. Supabase not configured), not as
    a hard error — access is still granted regardless.
    """
    client = _get_client()
    if client is None:
        return False
    try:
        row = {
            "email": email,
            "analysts": (meta or {}).get("analysts"),
            "user_agent": (meta or {}).get("user_agent"),
        }
        client.table("blueprint_leads").insert(row).execute()
        return True
    except Exception as exc:  # noqa: BLE001 — capture must never block access
        logger.warning("store_blueprint_email failed: %s", exc)
        return False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
