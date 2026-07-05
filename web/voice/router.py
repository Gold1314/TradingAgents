"""The additive ``/api/voice/*`` router for voice-conversational sessions.

Endpoints:

* ``GET  /api/voice/config`` — public capability probe (enabled?, providers).
* ``GET  /api/voice/personas`` — sanitised 12-agent persona list for the UI.
* ``POST /api/voice/sessions`` — start a voice call with one agent on a
  finished run. Body: ``{run_id, agent_id}``. Auth: dev/Supabase bearer when
  the mobile API is enabled, anonymous otherwise (web). Returns
  ``{session_id, url, token, persona, expires_at}``.
* ``GET  /api/voice/sessions/{id}`` — current status + persisted transcript.
* ``GET  /api/voice/runs/{run_id}/sessions`` — all voice sessions for a run
  (run-history UI).
* ``POST /api/voice/sessions/{id}/reconcile`` — Portfolio Manager only:
  re-invoke the PM with the user's objection as added context. Returns the
  updated rationale; no re-run of the full pipeline.
* ``GET  /api/voice/admin/cost-summary`` — admin-gated cost dashboard data.

All endpoints fail closed when voice is not fully configured (503 with a
human-readable reason) — see :meth:`VoiceSettings.fully_configured`.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from types import ModuleType
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from tradingagents.voice import personas as voice_personas
from tradingagents.voice.context_loader import (
    RunContext,
    load_run_context,
)
from tradingagents.voice.handoff import detect_reconcile_request
from tradingagents.voice.personas import VoicePersona, get_persona_for_user
from tradingagents.voice.reconcile import run_reconcile
from tradingagents.voice.settings import VoiceSettings, load_settings
from web.voice import db as voice_db
from web.voice import livekit_admin
from web.voice.jwt import mint_room_token

# The registered ``WorkerOptions.agent_name`` from the LiveKit Agents worker
# (``tradingagents/voice/agent_worker.py``). Must match — the LiveKit
# dispatcher uses this string to route a newly created room to the right
# worker pool when we pass ``RoomAgentDispatch(agent_name=...)``.
VOICE_AGENT_NAME = "tradingagents-voice"

logger = logging.getLogger("tradingagents.web.voice.router")


# Optional mobile-auth dependency. The mobile package is itself optional
# (it imports `web.mobile.settings.MOBILE_AUTH_*` env vars), so we shield
# the import behind try/except — documented exception to the
# `no-inline-imports` rule because the alternative is a hard dependency
# on the mobile router from the voice layer.
try:
    from web.mobile.auth import resolve_user as _mobile_resolve_user
    from web.mobile.settings import mobile_api_enabled as _mobile_api_enabled
    _MOBILE_AUTH_AVAILABLE = True
except ImportError:  # pragma: no cover - mobile package may be absent in slim builds
    _mobile_resolve_user = None  # type: ignore[assignment]
    _mobile_api_enabled = None  # type: ignore[assignment]
    _MOBILE_AUTH_AVAILABLE = False


def _try_mobile_auth():
    """Return the resolver if mobile auth is enabled; ``None`` otherwise."""
    if not _MOBILE_AUTH_AVAILABLE:
        return None
    if _mobile_api_enabled is None or not _mobile_api_enabled():
        return None
    return _mobile_resolve_user


class VoiceSessionRequest(BaseModel):
    run_id: str
    agent_id: str


class ReconcileRequest(BaseModel):
    objection: str


def _admin_password() -> Optional[str]:
    return os.environ.get("STOCKAGENTS_ADMIN_PASSWORD")


def _require_admin(password: Optional[str]) -> None:
    pw = _admin_password()
    if not pw:
        raise HTTPException(status_code=503, detail="admin features are not configured")
    # Constant-time compare to avoid leaking the password via timing. Fail
    # closed when the caller sent nothing.
    if not password or not hmac.compare_digest(password, pw):
        raise HTTPException(status_code=401, detail="invalid admin password")


def _resolve_user_id(request: Request, authorization: Optional[str]) -> Optional[str]:
    """Pull a stable user_id from the bearer when one is supplied, else fall
    back to the anonymous visitor id from the X-Visitor-Id header.

    The mobile resolver is only invoked when an ``Authorization`` header is
    actually present on the request. This preserves bearer enforcement for
    iOS clients (a present-but-invalid bearer still 401s) while letting the
    web client — which sends no Authorization header — fall through to the
    anonymous-visitor path documented at the top of this module.

    Returns ``None`` for fully anonymous web sessions without a visitor id —
    the caller decides whether to require auth.
    """
    resolver = _try_mobile_auth()
    if resolver is not None and authorization:
        try:
            user = resolver(authorization)
            return user.user_id if user is not None else None
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("voice user resolution failed: %s", exc)
            return None
    # Anonymous web fallback — track by the analytics visitor id so daily
    # caps still bind to one browser.
    return request.headers.get("x-visitor-id") or None


def _ensure_voice_ready(settings: VoiceSettings) -> None:
    ok, reason = settings.fully_configured
    if not ok:
        raise HTTPException(status_code=503, detail=f"voice unavailable: {reason}")


def _assert_run_owner(
    server: ModuleType, run_id: str, user_id: Optional[str]
) -> None:
    """Raise 403 if ``user_id`` is set and does not match the run's owner.

    Anonymous web mode (``user_id is None``) passes through — preserves the
    existing behavior when MOBILE_API_ENABLED is off. When mobile auth is
    on, the bearer must own the run before we mint a voice token, return
    transcripts, or trigger reconcile for it.

    Lookup order:
    1. In-memory ``RunManager`` (covers recently finished runs not yet flushed
       to Supabase, plus runs created in dev without a DB).
    2. Supabase ``runs`` table via :func:`voice_db.get_run_owner`.

    A run with a *recorded* owner is enforced (mismatch -> 403). A run that is
    genuinely ownerless (``owner is None`` — legacy web-flow runs) still passes
    through, since the web voice flow is meant to work for those. But if the
    ownership lookup itself *errors*, we fail CLOSED (deny) rather than treat
    the error as "ownerless" and grant access.
    """
    if user_id is None:
        return
    run = None
    get = getattr(server.manager, "get", None)
    if callable(get):
        try:
            run = get(run_id)
        except Exception as exc:  # noqa: BLE001 — best-effort
            logger.debug("run lookup failed for ownership check: %s", exc)
            run = None
    if run is not None:
        owner = ((getattr(run, "analytics", None) or {}) or {}).get("user_id")
        if owner is not None:
            if owner != user_id:
                raise HTTPException(
                    status_code=403, detail="run does not belong to caller"
                )
            return
    try:
        owner = voice_db.get_run_owner(run_id)
    except Exception as exc:  # noqa: BLE001 — fail CLOSED on lookup error
        logger.warning("run ownership lookup failed, denying: %s", exc)
        raise HTTPException(
            status_code=403, detail="run ownership could not be verified"
        ) from exc
    if owner is not None and owner != user_id:
        raise HTTPException(status_code=403, detail="run does not belong to caller")


def _assert_session_owner(
    session_id: str, user_id: Optional[str]
) -> dict:
    """Return the session row; 403 if ``user_id`` doesn't match its owner.

    Like :func:`_assert_run_owner`, anonymous callers pass through silently.
    Returns the loaded session dict so the caller can reuse it without a
    second fetch.
    """
    row = voice_db.get_session(session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="unknown voice session")
    if user_id is None:
        return row
    owner = row.get("user_id")
    if owner is not None and owner != user_id:
        raise HTTPException(
            status_code=403, detail="voice session does not belong to caller"
        )
    return row


def _persona_payload(p: VoicePersona) -> dict:
    return {
        "agent_id": p.agent_id,
        "display_name": p.display_name,
        "voice_name": p.voice_name,
        "short_intro": p.short_intro,
        "handoff_targets": list(p.handoff_targets),
        "tool_grants": sorted(p.tool_grants),
    }


def _run_completed(ctx: Optional[RunContext], agent_id: str) -> Optional[str]:
    """Return ``None`` if the run is ready for this agent, else an error string."""
    if ctx is None:
        return (
            "run not found, or the analysis for this ticker hasn't completed "
            "yet. Wait for the run to finish, then try again."
        )
    if ctx.agents.get(agent_id) is None:
        return (
            f"the {agent_id} hasn't finished this run yet. The agent's report "
            "must be on screen before you can talk to it."
        )
    return None


def build_voice_router(server: ModuleType) -> APIRouter:
    """Build the ``/api/voice`` router. ``server`` is the (fully-initialised)
    ``web.server`` module so we can reach ``server.manager`` for in-memory runs
    that haven't yet flushed to Supabase. Mirrors :mod:`web.mobile.router`."""

    router = APIRouter(prefix="/api/voice", tags=["voice"])

    @router.get("/config")
    def voice_config() -> dict:
        """Public capability probe — drives the "Talk" button visibility."""
        s = load_settings()
        ok, reason = s.fully_configured
        return {
            "enabled": s.enabled,
            "ready": ok,
            "reason": None if ok else reason,
            "tts_provider": s.tts_provider,
            "session_max_seconds": s.session_max_seconds,
            "daily_minutes_per_user": s.daily_minutes_per_user,
            "personas_count": len(voice_personas.PERSONAS),
        }

    @router.get("/personas")
    def list_personas() -> dict:
        """Sanitised 12-agent persona list (display names + short intros)."""
        return {"personas": voice_personas.list_for_client()}

    @router.post("/sessions")
    async def create_session(
        body: VoiceSessionRequest,
        request: Request,
        authorization: Optional[str] = Header(default=None),
    ) -> dict:
        settings = load_settings()
        _ensure_voice_ready(settings)

        user_id = _resolve_user_id(request, authorization)
        _assert_run_owner(server, body.run_id, user_id)
        # A/B routing: ``get_persona_for_user`` looks up
        # ``TRADINGAGENTS_VOICE_VARIANT_{A,B}_<AGENT>`` env to pick the
        # voice variant deterministically per user. Falls back to the base
        # persona when no experiment is configured.
        assignment = get_persona_for_user(body.agent_id, user_id)
        if assignment is None:
            raise HTTPException(
                status_code=400,
                detail=f"unknown agent_id: {body.agent_id}",
            )
        persona, variant = assignment
        # When mobile auth is enabled, the resolver raises 401 on missing
        # bearer. For anonymous web visitors we accept None — we still gate
        # minute caps by visitor_id when available.

        loop = asyncio.get_running_loop()

        # Daily minute cap (Phase 6) — best-effort, fail-open.
        if user_id and settings.daily_minutes_per_user > 0:
            used = await loop.run_in_executor(
                None, voice_db.user_voice_minutes_today, user_id
            )
            if used >= settings.daily_minutes_per_user:
                raise HTTPException(
                    status_code=429,
                    detail={
                        "code": "voice_minutes_exceeded",
                        "message": (
                            f"You've used your daily voice minutes "
                            f"({settings.daily_minutes_per_user} min / 24h)."
                        ),
                        "used_minutes": used,
                        "limit_minutes": settings.daily_minutes_per_user,
                    },
                )

        # Hourly session cap — defends against rapid-fire reconnects /
        # accidental loops blowing through the daily minutes via lots of
        # short calls. Bypass for anonymous web (no user_id to bind to).
        if settings.hourly_sessions_per_user > 0 and user_id is not None:
            since_ts = datetime.now(timezone.utc) - timedelta(hours=1)
            recent = await loop.run_in_executor(
                None, voice_db.count_user_sessions_since, user_id, since_ts
            )
            if recent >= settings.hourly_sessions_per_user:
                raise HTTPException(
                    status_code=429,
                    detail={
                        "code": "voice_sessions_exceeded",
                        "message": (
                            f"You've started too many voice calls this hour "
                            f"({settings.hourly_sessions_per_user}/hr). "
                            "Wait a bit and try again."
                        ),
                        "used_sessions": recent,
                        "limit_sessions": settings.hourly_sessions_per_user,
                    },
                )

        # Pull the run's context. Try Supabase, then the in-memory manager.
        ctx = await loop.run_in_executor(
            None, lambda: load_run_context(body.run_id, server.manager)
        )
        err = _run_completed(ctx, body.agent_id)
        if err is not None:
            raise HTTPException(status_code=409, detail=err)

        session_id = uuid.uuid4().hex
        # The room name encodes (run_id, agent_slug) so multiple users can
        # talk to different agents on the same run without colliding, while
        # one user re-joining the same agent ends up in the same room.
        agent_slug = body.agent_id.lower().replace(" ", "-")
        room = f"voice-{body.run_id}-{agent_slug}-{session_id[:8]}"

        # Room metadata — the worker reads this on JobContext.connect() to
        # pick the persona and load the run context. Keep it small (<8KB).
        metadata = {
            "session_id": session_id,
            "run_id": body.run_id,
            "agent_id": body.agent_id,
            "user_id": user_id,
            "ticker": ctx.ticker if ctx else None,
            "trade_date": ctx.trade_date if ctx else None,
            "asset_type": ctx.asset_type if ctx else None,
            "variant": variant,
        }

        # Pre-create the room so the LiveKit dispatcher binds our worker
        # (via RoomAgentDispatch) and the worker sees ``ctx.room.metadata``
        # set. Without this the user joins a silent, agent-less room.
        try:
            await livekit_admin.ensure_room(
                url=settings.livekit_url or "",
                api_key=settings.livekit_api_key or "",
                api_secret=settings.livekit_api_secret or "",
                room=room,
                metadata=metadata,
                agent_name=VOICE_AGENT_NAME,
            )
        except Exception as exc:  # noqa: BLE001 — surface as 503
            logger.error("voice ensure_room failed: %s", exc)
            raise HTTPException(
                status_code=503,
                detail="could not provision voice room",
            ) from exc

        try:
            token = mint_room_token(
                api_key=settings.livekit_api_key or "",
                api_secret=settings.livekit_api_secret or "",
                identity=f"user-{user_id or 'anon'}-{session_id[:6]}",
                room=room,
                name=user_id or "user",
                ttl_seconds=settings.token_ttl_seconds,
                metadata=metadata,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        # Persist the session row (fail-open — voice still works without it).
        await loop.run_in_executor(
            None,
            lambda: voice_db.create_session(
                session_id=session_id,
                run_id=body.run_id,
                agent_id=body.agent_id,
                user_id=user_id,
                livekit_room=room,
                persona_voice_id=persona.voice_id,
                persona_voice_name=persona.voice_name,
                tts_provider=settings.tts_provider,
                asr_provider="deepgram",
                llm_provider=settings.voice_llm_provider,
                llm_model=settings.voice_llm_model,
                variant=variant,
            ),
        )

        return {
            "session_id": session_id,
            "url": settings.livekit_url,
            "token": token,
            "room": room,
            "persona": _persona_payload(persona),
            "variant": variant,
            "expires_in": settings.token_ttl_seconds,
            "session_max_seconds": settings.session_max_seconds,
        }

    @router.get("/sessions/{session_id}")
    async def get_session(
        session_id: str,
        request: Request,
        authorization: Optional[str] = Header(default=None),
    ) -> dict:
        user_id = _resolve_user_id(request, authorization)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, _assert_session_owner, session_id, user_id
        )

    @router.get("/runs/{run_id}/sessions")
    async def list_run_sessions(
        run_id: str,
        request: Request,
        authorization: Optional[str] = Header(default=None),
    ) -> dict:
        user_id = _resolve_user_id(request, authorization)
        _assert_run_owner(server, run_id, user_id)
        loop = asyncio.get_running_loop()
        rows = await loop.run_in_executor(None, voice_db.list_sessions_for_run, run_id)
        return {"sessions": rows}

    @router.post("/sessions/{session_id}/reconcile")
    async def reconcile_pm(
        session_id: str,
        body: ReconcileRequest,
        request: Request,
        authorization: Optional[str] = Header(default=None),
    ) -> dict:
        """Re-invoke the Portfolio Manager with the user's objection.

        Voice-mode "ask the PM to reconcile". Only valid for a Portfolio
        Manager session; carries the user's last objection as added context
        and runs a one-shot reasoning call. Does NOT re-run the whole
        pipeline — that would burn ~$30 of LLM tokens for a single follow-up.

        Implementation: defers to :mod:`tradingagents.voice.reconcile` which
        composes a focused prompt from the run context + objection and calls
        the deep LLM through the existing factory. Stored as a ``voice_turn``
        row with role=``"reconcile"``.
        """
        objection = (body.objection or "").strip()
        if not objection:
            raise HTTPException(status_code=400, detail="objection text is required")
        if not detect_reconcile_request(objection) and len(objection) < 8:
            # Cheap sanity check: don't burn an LLM call on "k" / "ok".
            raise HTTPException(
                status_code=400,
                detail="objection must contain a substantive challenge (8+ chars)",
            )

        user_id = _resolve_user_id(request, authorization)
        loop = asyncio.get_running_loop()
        row = await loop.run_in_executor(
            None, _assert_session_owner, session_id, user_id
        )
        if row.get("agent_id") != "Portfolio Manager":
            raise HTTPException(
                status_code=409,
                detail="reconcile is only valid for a Portfolio Manager session",
            )

        result = await loop.run_in_executor(
            None,
            lambda: run_reconcile(
                run_id=row["run_id"],
                session_id=session_id,
                objection=objection,
                manager=server.manager,
            ),
        )
        if result is None:
            raise HTTPException(
                status_code=503,
                detail="could not reconcile — Portfolio Manager LLM unavailable",
            )
        return result

    @router.get("/admin/cost-summary")
    async def cost_summary(
        hours: int = 24,
        x_admin_password: Optional[str] = Header(default=None),
    ) -> dict:
        _require_admin(x_admin_password)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: voice_db.cost_summary(hours=hours))

    return router
