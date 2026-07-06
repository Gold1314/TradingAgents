"""Supabase persistence for voice sessions and transcript turns.

Fail-open like :mod:`web.db`: if Supabase is not configured (no
``SUPABASE_URL`` / ``SUPABASE_KEY``) or a call errors, these helpers return
``None`` / ``False`` and voice sessions still run — they just don't persist a
transcript or usage row. Voice cost guardrails (daily minute caps) degrade to
"unenforced" rather than blocking the call.

Schema (see ``supabase/migrations/*_voice_sessions.sql``):

* ``voice_sessions`` — one row per call.
* ``voice_turns`` — one row per finalized ASR/TTS turn.

Both are read by ``GET /api/voice/sessions/{id}`` for replay UI and by the
cost dashboard (Phase 6).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

# ``web.db`` owns the memoized Supabase client singleton. We share it here
# so all voice persistence runs against the same connection pool that the
# run-history endpoints already use. The import is guarded with a
# try/except because slim deployments (e.g. CI) may not install supabase
# at all — that path is exercised by the unit tests.
try:
    from web import db as _db_module
    _DB_AVAILABLE = True
except ImportError:  # pragma: no cover - supabase optional
    _db_module = None  # type: ignore[assignment]
    _DB_AVAILABLE = False

logger = logging.getLogger("tradingagents.web.voice.db")


def _client():
    """Return the shared Supabase client, or ``None`` when unavailable."""
    if not _DB_AVAILABLE or _db_module is None:
        return None
    getter = getattr(_db_module, "_get_client", None)
    if getter is None:
        return None
    return getter()


def create_session(
    *,
    session_id: str,
    run_id: str,
    agent_id: str,
    user_id: Optional[str],
    livekit_room: str,
    persona_voice_id: str,
    persona_voice_name: str,
    tts_provider: str,
    asr_provider: str,
    llm_provider: Optional[str],
    llm_model: Optional[str],
    variant: Optional[str] = None,
) -> bool:
    """Insert a ``voice_sessions`` row at session start. Idempotent on session_id.

    ``variant`` records the persona A/B bucket so cost/quality analyses can be
    sliced by variant. The DB column is optional — older deployments without
    the column simply ignore the field (Supabase upsert ignores unknown keys
    when its row-validation is permissive; if your schema is strict, run the
    migration in ``supabase/migrations/*_voice_variant.sql``).
    """
    client = _client()
    if client is None:
        return False
    row = {
        "id": session_id,
        "run_id": run_id,
        "agent_id": agent_id,
        "user_id": user_id,
        "livekit_room": livekit_room,
        "status": "started",
        "persona_voice_id": persona_voice_id,
        "persona_voice_name": persona_voice_name,
        "tts_provider": tts_provider,
        "asr_provider": asr_provider,
        "llm_provider": llm_provider,
        "llm_model": llm_model,
        "started_at": _now_iso(),
    }
    if variant:
        row["variant"] = variant
    try:
        client.table("voice_sessions").upsert(row, on_conflict="id").execute()
        return True
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning("voice create_session failed: %s", exc)
        return False


def create_panel_session(
    *,
    session_id: str,
    run_id: str,
    agent_ids: List[str],
    lead_agent_id: str,
    user_id: Optional[str],
    livekit_room: str,
    tts_provider: str,
    asr_provider: str,
    llm_provider: Optional[str],
    llm_model: Optional[str],
) -> bool:
    """Insert a panel ``voice_sessions`` row (``mode='panel'``).

    The scalar ``agent_id`` column stores the lead (moderator) so the cost
    dashboard and per-agent queries keep working unchanged; ``agent_ids`` holds
    the full roster. Fail-open like :func:`create_session`: a missing ``mode`` /
    ``agent_ids`` column (un-migrated schema) is tolerated because we drop those
    keys and retry once as a plain solo-shaped row.
    """
    client = _client()
    if client is None:
        return False
    base = {
        "id": session_id,
        "run_id": run_id,
        "agent_id": lead_agent_id,
        "user_id": user_id,
        "livekit_room": livekit_room,
        "status": "started",
        "tts_provider": tts_provider,
        "asr_provider": asr_provider,
        "llm_provider": llm_provider,
        "llm_model": llm_model,
        "started_at": _now_iso(),
    }
    row = {**base, "mode": "panel", "agent_ids": list(agent_ids)}
    try:
        client.table("voice_sessions").upsert(row, on_conflict="id").execute()
        return True
    except Exception as exc:  # noqa: BLE001 — retry without panel columns
        logger.warning("voice create_panel_session (panel cols) failed: %s", exc)
        try:
            client.table("voice_sessions").upsert(base, on_conflict="id").execute()
            return True
        except Exception as exc2:  # noqa: BLE001 — fail-open
            logger.warning("voice create_panel_session fallback failed: %s", exc2)
            return False


def finish_session(
    session_id: str,
    *,
    status: str,
    input_audio_seconds: float = 0.0,
    output_audio_seconds: float = 0.0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_usd: float = 0.0,
    error: Optional[str] = None,
) -> bool:
    """Update a ``voice_sessions`` row with final timing/cost."""
    client = _client()
    if client is None:
        return False
    row: Dict[str, Any] = {
        "status": status,
        "input_audio_seconds": input_audio_seconds,
        "output_audio_seconds": output_audio_seconds,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": cost_usd,
        "ended_at": _now_iso(),
    }
    if error is not None:
        row["error"] = error[:500]
    try:
        client.table("voice_sessions").update(row).eq("id", session_id).execute()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("voice finish_session failed: %s", exc)
        return False


def append_turn(
    *,
    session_id: str,
    idx: int,
    role: str,
    text: str,
    started_at: Optional[datetime] = None,
    ended_at: Optional[datetime] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    speaker_agent_id: Optional[str] = None,
) -> bool:
    """Insert one finalized ASR or TTS turn into ``voice_turns``.

    ``speaker_agent_id`` attributes an assistant turn to the persona that spoke
    it (panel mode). It is only sent when provided *and* only re-tried without
    the column on failure, so an un-migrated schema still records the turn.
    """
    client = _client()
    if client is None:
        return False
    row: Dict[str, Any] = {
        "session_id": session_id,
        "idx": idx,
        "role": role,
        "text": text[:8000],
        "provider": provider,
        "model": model,
    }
    if started_at:
        row["started_at"] = started_at.isoformat()
    if ended_at:
        row["ended_at"] = ended_at.isoformat()
    if speaker_agent_id:
        row["speaker_agent_id"] = speaker_agent_id
    try:
        client.table("voice_turns").insert(row).execute()
        return True
    except Exception as exc:  # noqa: BLE001 — retry without the panel column
        if speaker_agent_id:
            row.pop("speaker_agent_id", None)
            try:
                client.table("voice_turns").insert(row).execute()
                return True
            except Exception as exc2:  # noqa: BLE001
                logger.warning("voice append_turn fallback failed: %s", exc2)
                return False
        logger.warning("voice append_turn failed: %s", exc)
        return False


def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    """Return the session row + its ordered turns (for status + replay UI)."""
    client = _client()
    if client is None:
        return None
    try:
        sres = (
            client.table("voice_sessions")
            .select("*")
            .eq("id", session_id)
            .limit(1)
            .execute()
        )
        rows = sres.data or []
        if not rows:
            return None
        session = rows[0]
        # Prefer the panel-attributed columns; fall back to the base set on an
        # un-migrated schema so the session still loads (fail-open).
        try:
            tres = (
                client.table("voice_turns")
                .select("idx,role,text,started_at,ended_at,speaker_agent_id")
                .eq("session_id", session_id)
                .order("idx")
                .execute()
            )
        except Exception:  # noqa: BLE001 — speaker_agent_id column may be absent
            tres = (
                client.table("voice_turns")
                .select("idx,role,text,started_at,ended_at")
                .eq("session_id", session_id)
                .order("idx")
                .execute()
            )
        session["turns"] = tres.data or []
        return session
    except Exception as exc:  # noqa: BLE001
        logger.warning("voice get_session failed: %s", exc)
        return None


def list_sessions_for_run(run_id: str) -> List[Dict[str, Any]]:
    """All voice sessions for a finished run (used by run-history UI)."""
    client = _client()
    if client is None:
        return []
    try:
        res = (
            client.table("voice_sessions")
            .select("id,agent_id,status,started_at,ended_at,input_audio_seconds,output_audio_seconds,cost_usd")
            .eq("run_id", run_id)
            .order("started_at")
            .execute()
        )
        return res.data or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("voice list_sessions_for_run failed: %s", exc)
        return []


def user_voice_minutes_today(user_id: str) -> float:
    """Total spoken minutes (input + output audio) consumed by ``user_id``
    in the rolling 24h window. Used to enforce the daily voice minute cap.

    Returns 0.0 on Supabase miss/error — we never lock a user out due to a
    DB blip (fail-open consistent with the rest of the module).
    """
    client = _client()
    if client is None or not user_id:
        return 0.0
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    try:
        res = (
            client.table("voice_sessions")
            .select("input_audio_seconds,output_audio_seconds")
            .eq("user_id", user_id)
            .gte("started_at", cutoff)
            .execute()
        )
        total_seconds = 0.0
        for row in res.data or []:
            total_seconds += float(row.get("input_audio_seconds") or 0.0)
            total_seconds += float(row.get("output_audio_seconds") or 0.0)
        return total_seconds / 60.0
    except Exception as exc:  # noqa: BLE001
        logger.warning("voice user_voice_minutes_today failed: %s", exc)
        return 0.0


def count_user_sessions_since(user_id: str, since_ts: datetime) -> int:
    """Count voice sessions for ``user_id`` started at or after ``since_ts``.

    Powers the hourly per-user session cap. Fail-open: returns 0 on any
    Supabase error so a DB blip never locks the user out.
    """
    client = _client()
    if client is None or not user_id:
        return 0
    try:
        res = (
            client.table("voice_sessions")
            .select("id", count="exact")
            .eq("user_id", user_id)
            .gte("started_at", since_ts.isoformat())
            .execute()
        )
        count = getattr(res, "count", None)
        if count is not None:
            return int(count)
        return len(res.data or [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("voice count_user_sessions_since failed: %s", exc)
        return 0


def get_run_owner(run_id: str) -> Optional[str]:
    """Return the owning user_id for a run, or ``None`` if unknown.

    Only the dedicated ``runs.user_id`` column is treated as an authoritative
    owner. The legacy ``runs.identity`` column is *not* a user identifier —
    per ``supabase_schema.sql`` it stores the "resolved instrument context
    banner" (e.g. ``"RKLB stock"``), a human-readable label that gets carried
    into the run UI. Earlier versions of this function fell back to that
    column and returned the banner string as the "owner", which then failed
    every Supabase-UUID comparison and produced spurious 403s on cached
    voice sessions.

    Returns ``None`` (treated by the caller as "genuinely ownerless, allow")
    when:
      * Supabase isn't configured,
      * the run row exists but has no ``user_id`` (legacy web-flow runs that
        were created before the column was wired in), or
      * the run row doesn't exist.

    A genuine *query error* (including the ``user_id`` column missing on a
    pre-migration schema) is **not** swallowed here — it is re-raised so the
    caller can fail CLOSED rather than mistaking an error for "ownerless" and
    granting access. Deployments running the voice ownership check must have
    the ``runs.user_id`` column migrated in.
    """
    client = _client()
    if client is None or not run_id:
        return None
    try:
        res = (
            client.table("runs")
            .select("user_id")
            .eq("id", run_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 — fail CLOSED: let the caller deny
        logger.debug("voice get_run_owner query failed: %s", exc)
        raise
    rows = res.data or []
    if not rows:
        return None
    value = rows[0].get("user_id")
    return str(value) if value else None


def next_turn_idx(session_id: str, default_start: int = 10_000) -> int:
    """Return the next available ``idx`` for a ``voice_turns`` insert.

    Used by the reconcile path so a second reconcile in the same session
    doesn't collide on the ``unique (session_id, idx)`` constraint. The
    default base of ``10_000`` keeps reconcile turns sorted *after* ASR/TTS
    turns (which use natural counters starting at 0) for replay ordering.
    """
    client = _client()
    if client is None or not session_id:
        return default_start
    try:
        res = (
            client.table("voice_turns")
            .select("idx")
            .eq("session_id", session_id)
            .order("idx", desc=True)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        if not rows:
            return default_start
        current_max = int(rows[0].get("idx") or 0)
        return max(current_max + 1, default_start)
    except Exception as exc:  # noqa: BLE001
        logger.warning("voice next_turn_idx failed: %s", exc)
        return default_start


def cost_summary(*, hours: int = 24) -> Dict[str, Any]:
    """Aggregate cost / usage stats across all users in the window.

    Powers the ``/api/voice/cost-summary`` admin endpoint (Phase 6 cost
    dashboard). Returns zeros when Supabase is unavailable.
    """
    client = _client()
    if client is None:
        return {"sessions": 0, "minutes": 0.0, "cost_usd": 0.0, "by_agent": []}
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    try:
        res = (
            client.table("voice_sessions")
            .select("agent_id,input_audio_seconds,output_audio_seconds,cost_usd")
            .gte("started_at", cutoff)
            .execute()
        )
        rows = res.data or []
        by_agent: Dict[str, Dict[str, float]] = {}
        total_minutes = 0.0
        total_cost = 0.0
        for r in rows:
            audio = float(r.get("input_audio_seconds") or 0.0) + float(
                r.get("output_audio_seconds") or 0.0
            )
            cost = float(r.get("cost_usd") or 0.0)
            total_minutes += audio / 60.0
            total_cost += cost
            agent = r.get("agent_id") or "unknown"
            bucket = by_agent.setdefault(
                agent, {"sessions": 0, "minutes": 0.0, "cost_usd": 0.0}
            )
            bucket["sessions"] += 1
            bucket["minutes"] += audio / 60.0
            bucket["cost_usd"] += cost
        return {
            "sessions": len(rows),
            "minutes": total_minutes,
            "cost_usd": total_cost,
            "by_agent": [
                {"agent_id": k, **v}
                for k, v in sorted(by_agent.items(), key=lambda kv: -kv[1]["cost_usd"])
            ],
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("voice cost_summary failed: %s", exc)
        return {"sessions": 0, "minutes": 0.0, "cost_usd": 0.0, "by_agent": []}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
