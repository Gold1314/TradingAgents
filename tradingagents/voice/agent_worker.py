"""LiveKit Agents worker entry point for the voice-conversational layer.

This module is the long-running Python process the operator runs alongside
``web/server.py`` (e.g. ``python -m tradingagents.voice.agent_worker``). It
registers with LiveKit Cloud as an Agents worker; the LiveKit dispatcher
assigns it to any room whose name starts with ``voice-`` (created by
:mod:`web.voice.router`). On dispatch the worker:

1. Parses the room metadata (set when the room JWT was minted) to pick the
   persona, the run id, and the user.
2. Loads the run's structured context via :mod:`context_loader` and composes
   the persona's system prompt.
3. Wires Deepgram (ASR), the configured voice LLM, ElevenLabs (TTS), and
   Silero (VAD) into a single ``AgentSession`` with built-in turn-taking
   and barge-in.
4. Subscribes to assistant + user transcript events; persists each finalized
   turn to ``voice_turns`` and scans assistant utterances for handoff
   intents (data channel notification → UI handoff chip).
5. Hangs up automatically at ``session_max_seconds`` (defends against
   forgotten calls).

Optional dependencies: ``livekit-agents``, the matching LLM/STT/TTS plugins,
and provider SDKs. Imports are deferred so this module remains importable
without them (e.g. in unit tests that only exercise the prompt assembly).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from tradingagents.voice.context_loader import (
    RunContext,
    build_system_prompt,
    load_run_context,
)
from tradingagents.voice.handoff import (
    KIND_HANDOFF_SUGGESTED,
    KIND_RECONCILE_REQUESTED,
    KIND_TRANSCRIPT_FINAL,
    KIND_TRANSCRIPT_PARTIAL,
    KIND_USAGE,
    coalesce_handoffs,
    detect_handoff,
    detect_reconcile_request,
)
from tradingagents.voice import panel
from tradingagents.voice.personas import VoicePersona, get_persona
from tradingagents.voice.settings import VoiceSettings, load_settings
from web.voice import db as voice_db

logger = logging.getLogger("tradingagents.voice.agent_worker")


# Estimated unit pricing (USD per second of audio for TTS, per second for ASR,
# per 1k tokens for LLM). These are conservative defaults; override via env
# (``TRADINGAGENTS_VOICE_PRICE_*``) to track your actual contract rates. See
# ``docs/voice-cost-model.md``.
@dataclass(frozen=True)
class _Pricing:
    asr_per_second: float = 0.0043 / 60.0          # Deepgram Nova-3 streaming
    tts_per_second: float = 0.18 / 60.0            # ElevenLabs Flash v2.5
    llm_per_1k_input_tokens: float = 0.15 / 1000.0  # GPT-4o-mini est.
    llm_per_1k_output_tokens: float = 0.60 / 1000.0


def _load_pricing() -> _Pricing:
    """Reload pricing from env each call (cheap; called once per session)."""
    def fenv(name: str, default: float) -> float:
        raw = os.environ.get(name)
        if not raw:
            return default
        try:
            return float(raw)
        except ValueError:
            return default
    return _Pricing(
        asr_per_second=fenv("TRADINGAGENTS_VOICE_PRICE_ASR_PER_SECOND", 0.0043 / 60.0),
        tts_per_second=fenv("TRADINGAGENTS_VOICE_PRICE_TTS_PER_SECOND", 0.18 / 60.0),
        llm_per_1k_input_tokens=fenv(
            "TRADINGAGENTS_VOICE_PRICE_LLM_INPUT_PER_1K", 0.15 / 1000.0
        ) * 1000.0,
        llm_per_1k_output_tokens=fenv(
            "TRADINGAGENTS_VOICE_PRICE_LLM_OUTPUT_PER_1K", 0.60 / 1000.0
        ) * 1000.0,
    )


# Session-scoped accumulators populated as the call runs.
@dataclass
class _SessionAccumulators:
    persona: VoicePersona
    run_context: RunContext
    session_id: str
    user_id: Optional[str]
    started_at: float = field(default_factory=time.time)
    turn_index: int = 0
    input_audio_seconds: float = 0.0
    output_audio_seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    handoffs: List[Any] = field(default_factory=list)


# Sentinel: imports that may fail without the optional install. Resolved
# inside :func:`entrypoint` so module import stays cheap.
_LK_AVAILABLE = None


def _lazy_import_livekit():
    """Import the LiveKit Agents stack lazily; raise a clear error if missing."""
    global _LK_AVAILABLE
    if _LK_AVAILABLE is False:
        raise RuntimeError(
            "livekit-agents and its plugins are not installed. "
            "Install them with: pip install 'livekit-agents[deepgram,elevenlabs,silero]' "
            "livekit-plugins-anthropic"
        )
    if _LK_AVAILABLE is True:
        return
    try:
        # The "AgentSession" surface lives in livekit.agents.voice in 1.0+.
        import livekit.agents  # noqa: F401
        from livekit import rtc  # noqa: F401
        _LK_AVAILABLE = True
    except ImportError as exc:
        _LK_AVAILABLE = False
        raise RuntimeError(
            f"livekit-agents import failed: {exc}. "
            "Install with: pip install 'livekit-agents[deepgram,elevenlabs,silero]' "
            "livekit-plugins-anthropic"
        ) from exc


def _build_llm(settings: VoiceSettings, persona: VoicePersona) -> Any:
    """Construct the LiveKit Agents ``llm`` instance for this persona.

    Voice LLM is Anthropic Claude — favor low-latency Haiku for voice
    turn-around. The model is taken from ``TRADINGAGENTS_VOICE_LLM_MODEL``
    (plumbed via ``settings.voice_llm_model``) and falls back to Claude
    Haiku 4.5 (``claude-haiku-4-5-20251001``), the current voice-tier
    model. Anthropic has retired Haiku 3.5 from new accounts; if a future
    Haiku ships, point this at it via the env var override.
    We deliberately do NOT reuse the run's
    deep/quick LLM names — those are reasoning models tuned for the
    LangGraph pipeline. Voice favors low-latency chat models; see the cost
    model doc for the trade-off.

    ``api_key`` is pulled from the settings snapshot rather than
    ``os.environ`` because livekit-agents forks a sanitized subprocess
    per job (and we discovered the child can lose env that was implicit
    on the parent). Reading via settings means the snapshot is taken on
    the parent and passed in by value.
    """
    from livekit.plugins import anthropic as lk_anthropic  # noqa: PLC0415
    model = settings.voice_llm_model or "claude-haiku-4-5-20251001"
    api_key = settings.anthropic_api_key
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is missing from .env / environment — "
            "voice LLM cannot start."
        )
    return lk_anthropic.LLM(model=model, api_key=api_key)


def _build_tts(settings: VoiceSettings, persona: VoicePersona) -> Any:
    """Construct the TTS plugin instance, voiced for this persona."""
    if settings.tts_provider == "elevenlabs":
        from livekit.plugins import elevenlabs as lk_el  # noqa: PLC0415
        return lk_el.TTS(
            voice_id=persona.voice_id,
            model=settings.elevenlabs_model,
            api_key=settings.elevenlabs_api_key,
            # Voice settings reflect the persona (stability/style); these are
            # passed through to the ElevenLabs request body.
            voice_settings=lk_el.VoiceSettings(
                stability=persona.stability,
                similarity_boost=persona.similarity_boost,
                style=persona.style,
                use_speaker_boost=True,
                speed=persona.speed,
            ),
        )
    if settings.tts_provider == "cartesia":
        from livekit.plugins import cartesia as lk_ca  # noqa: PLC0415
        return lk_ca.TTS(
            voice=persona.cartesia_voice_id or persona.voice_id,
            api_key=settings.cartesia_api_key,
        )
    raise ValueError(
        f"unsupported TTS provider: {settings.tts_provider}. "
        "Set TRADINGAGENTS_VOICE_TTS_PROVIDER=elevenlabs|cartesia."
    )


def _build_stt(settings: VoiceSettings) -> Any:
    """Construct the ASR plugin instance."""
    from livekit.plugins import deepgram as lk_dg  # noqa: PLC0415
    return lk_dg.STT(
        model=settings.deepgram_model,
        api_key=settings.deepgram_api_key,
        # Streaming + numerals: finance jargon includes lots of numbers.
        smart_format=True,
        numerals=True,
        # Punctuation helps the LLM's turn-taking quality.
        punctuate=True,
    )


def _build_vad() -> Any:
    """Silero VAD (small ONNX model). Loaded once per worker."""
    from livekit.plugins import silero  # noqa: PLC0415
    return silero.VAD.load()


async def _publish_data_event(room: Any, payload: dict) -> None:
    """Publish a small JSON payload on the LiveKit data channel.

    All three clients (web/iOS/RN) subscribe to data events and dispatch
    by ``payload.kind`` (see :mod:`tradingagents.voice.handoff`). Failures are
    logged, not raised — a missed handoff signal must never crash the call.
    """
    try:
        from livekit import rtc  # noqa: PLC0415
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        await room.local_participant.publish_data(data, reliable=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("voice data publish failed: %s", exc)


async def _run_panel_session(
    ctx: Any, settings: VoiceSettings, pricing: _Pricing, metadata: dict
) -> None:
    """Run a multi-agent *panel* call (moderated turn-taking) in one room.

    The room already contains several personas' worth of context. We build one
    LiveKit ``Agent`` per panelist — each carrying its own persona instructions
    and its own TTS voice — and one shared ``AgentSession`` (STT/LLM/VAD). On
    every finalized user turn the pure moderator (:func:`panel.select_speaker`)
    picks who answers; we ``update_agent`` to that persona (which also switches
    the spoken voice) before the reply is generated, so the right agent answers
    in the right voice. The user can also direct a turn: the client publishes a
    ``panel.direct`` data message which we honor on the next turn.

    INTEGRATION NOTE: swapping the active agent inside the ``user_input_transcribed``
    handler is the documented LiveKit multi-agent hand-off mechanism, but the
    exact ordering vs. the session's automatic reply generation depends on the
    installed ``livekit-agents`` version and MUST be validated against live
    LiveKit — it cannot be exercised in unit tests (no audio/RTC here). The
    *routing decision* is fully covered by ``tests/test_voice_panel.py``; this
    function is the thin, best-effort glue around it.
    """
    from livekit.agents.voice import Agent, AgentSession  # noqa: PLC0415

    run_id = metadata.get("run_id")
    session_id = metadata.get("session_id")
    user_id = metadata.get("user_id")
    agent_ids = metadata.get("agent_ids") or []
    if not (run_id and session_id and len(agent_ids) >= 2):
        logger.error("voice panel room missing required metadata: %s", metadata)
        return

    personas = {}
    for aid in agent_ids:
        p = get_persona(aid)
        if p is not None:
            personas[aid] = p
    if len(personas) < 2:
        logger.error("voice panel has fewer than 2 known personas: %s", agent_ids)
        return
    roster = [aid for aid in agent_ids if aid in personas]

    run_context = load_run_context(run_id, manager=None)
    if run_context is None:
        await _publish_data_event(
            ctx.room,
            {
                "kind": "voice_error",
                "code": "no_run_context",
                "message": (
                    "I couldn't load the analysis for this run. It may still "
                    "be finishing — wait a moment and try again."
                ),
            },
        )
        await ctx.room.disconnect()
        return

    roster_display = [personas[aid].display_name for aid in roster]

    # One Agent per persona: its own panel-framed instructions and its own
    # TTS voice, so update_agent() swaps both the reasoning and the voice.
    agents: Dict[str, Any] = {}
    for aid in roster:
        persona = personas[aid]
        solo_prompt = build_system_prompt(persona, run_context)
        panel_prompt = panel.build_panel_system_prompt(
            persona, solo_prompt, roster_display
        )
        agents[aid] = Agent(instructions=panel_prompt, tts=_build_tts(settings, persona))

    lead = panel.lead_agent(roster)

    # Shared session — STT/LLM/VAD are room-wide; TTS is per-Agent (above).
    session = AgentSession(
        stt=_build_stt(settings),
        llm=_build_llm(settings, personas[lead]),
        vad=_build_vad(),
    )

    accum = _SessionAccumulators(
        persona=personas[lead],
        run_context=run_context,
        session_id=session_id,
        user_id=user_id,
    )

    # Turn state. ``current_speaker`` tracks the active Agent; ``last_speaker``
    # feeds the moderator's anti-repeat rule; ``directed_next`` holds a pending
    # user-directed target from a ``panel.direct`` data message.
    state: Dict[str, Optional[str]] = {
        "current_speaker": lead,
        "last_speaker": None,
        "directed_next": None,
    }
    last_user_final_at: Dict[str, float] = {"t": 0.0}

    async def _switch_to(agent_id: str) -> None:
        if agent_id == state["current_speaker"]:
            return
        try:
            await session.update_agent(agents[agent_id])
            state["current_speaker"] = agent_id
        except Exception as exc:  # noqa: BLE001 — never crash the call on a swap
            logger.warning("panel update_agent to %s failed: %s", agent_id, exc)

    async def _on_user_final(text: str) -> None:
        accum.turn_index += 1
        last_user_final_at["t"] = time.time()
        await asyncio.to_thread(
            _persist_turn,
            session_id=session_id,
            idx=accum.turn_index,
            role="user",
            text=text,
            provider="deepgram",
            model=settings.deepgram_model,
        )
        # Route this turn: directed (from panel.direct) → addressed-by-name →
        # topic affinity → lead. Then swap the active persona/voice.
        directed = state["directed_next"]
        state["directed_next"] = None
        speaker = panel.select_speaker(
            text,
            roster,
            directed_agent_id=directed,
            last_speaker=state["last_speaker"],
        )
        await _switch_to(speaker)
        state["last_speaker"] = speaker
        await _publish_data_event(
            ctx.room,
            {"kind": panel.KIND_PANEL_SPEAKER, "agent_id": speaker, "session_id": session_id},
        )
        # PM reconcile detection still applies when the PM is on the panel.
        if speaker == "Portfolio Manager" and detect_reconcile_request(text):
            await _publish_data_event(
                ctx.room,
                {
                    "kind": KIND_RECONCILE_REQUESTED,
                    "session_id": session_id,
                    "run_id": run_id,
                    "objection": text,
                },
            )
        await _publish_data_event(
            ctx.room,
            {"kind": KIND_TRANSCRIPT_FINAL, "role": "user", "text": text, "idx": accum.turn_index},
        )

    async def _on_assistant_final(text: str) -> None:
        accum.turn_index += 1
        speaker = state["current_speaker"]
        if last_user_final_at["t"]:
            rtt_ms = int((time.time() - last_user_final_at["t"]) * 1000.0)
            await _publish_data_event(
                ctx.room,
                {"kind": KIND_USAGE, "session_id": session_id, "rtt_ms": rtt_ms, "turn": accum.turn_index},
            )
            last_user_final_at["t"] = 0.0
        await asyncio.to_thread(
            _persist_turn,
            session_id=session_id,
            idx=accum.turn_index,
            role="assistant",
            text=text,
            provider=settings.tts_provider,
            model=settings.elevenlabs_model
            if settings.tts_provider == "elevenlabs"
            else "cartesia-sonic",
            speaker_agent_id=speaker,
        )
        # Attribute the assistant transcript to the persona that spoke it.
        await _publish_data_event(
            ctx.room,
            {
                "kind": KIND_TRANSCRIPT_FINAL,
                "role": "assistant",
                "text": text,
                "idx": accum.turn_index,
                "agent_id": speaker,
            },
        )

    @session.on("user_input_transcribed")
    def _wrap_user(ev: Any) -> None:  # noqa: ANN401 — vendor type
        text = getattr(ev, "transcript", None) or getattr(ev, "text", "")
        if not getattr(ev, "is_final", True):
            asyncio.create_task(
                _publish_data_event(
                    ctx.room,
                    {"kind": KIND_TRANSCRIPT_PARTIAL, "role": "user", "text": text},
                )
            )
            return
        asyncio.create_task(_on_user_final(text))

    @session.on("conversation_item_added")
    def _wrap_assistant(ev: Any) -> None:  # noqa: ANN401
        item = getattr(ev, "item", None)
        if item is None or getattr(item, "role", None) != "assistant":
            return
        content = getattr(item, "text_content", None) or getattr(item, "content", "")
        if not content:
            return
        asyncio.create_task(_on_assistant_final(content))

    # Inbound user-directed routing: the client publishes {kind:"panel.direct",
    # agent_id:...} when the user taps a panelist. We honor it on the next turn.
    @ctx.room.on("data_received")
    def _on_data(packet: Any) -> None:  # noqa: ANN401 — vendor type
        try:
            raw = getattr(packet, "data", None)
            if raw is None:
                return
            payload = json.loads(bytes(raw).decode("utf-8"))
        except Exception:  # noqa: BLE001 — ignore malformed inbound data
            return
        if payload.get("kind") == panel.KIND_PANEL_DIRECT:
            target = payload.get("agent_id")
            if target in roster:
                state["directed_next"] = target

    async def _terminate_after_max() -> None:
        await asyncio.sleep(max(60, settings.session_max_seconds))
        logger.info("voice panel %s hit max duration; disconnecting", session_id)
        try:
            await session.aclose()
        finally:
            await ctx.shutdown(reason="max-duration-reached")

    asyncio.create_task(_terminate_after_max())

    logger.info(
        "voice panel start: session=%s roster=%s lead=%s run=%s",
        session_id, roster, lead, run_id,
    )
    # Start on the lead agent so the moderator greets first.
    await session.start(agent=agents[lead], room=ctx.room)
    try:
        await session.generate_reply(
            instructions=(
                f"You are {personas[lead].display_name}, opening a panel with "
                f"{', '.join(roster_display)}. In one sentence, welcome the user "
                "and invite their first question to the panel."
            )
        )
    except Exception as exc:  # noqa: BLE001 — opener failure is non-fatal
        logger.warning("voice panel opener failed: %s", exc)

    try:
        await session.aclose_event.wait()
    except AttributeError:
        await ctx.room.wait_for_disconnect() if hasattr(ctx.room, "wait_for_disconnect") else None

    elapsed = max(0.0, time.time() - accum.started_at)
    accum.input_audio_seconds = elapsed * 0.4
    accum.output_audio_seconds = elapsed * 0.6
    cost_usd = _estimate_cost(accum, pricing)
    await asyncio.to_thread(
        _finalize_session,
        session_id=session_id,
        status="completed",
        input_audio_seconds=accum.input_audio_seconds,
        output_audio_seconds=accum.output_audio_seconds,
        input_tokens=accum.input_tokens,
        output_tokens=accum.output_tokens,
        cost_usd=cost_usd,
    )
    logger.info("voice panel end: session=%s elapsed=%.1fs cost=$%.4f", session_id, elapsed, cost_usd)


async def entrypoint(ctx: Any) -> None:
    """LiveKit Agents JobContext entrypoint.

    Argument type is ``livekit.agents.JobContext`` but we annotate ``Any`` so
    this module imports cleanly without livekit-agents installed.
    """
    _lazy_import_livekit()

    from livekit.agents import AutoSubscribe  # noqa: PLC0415
    from livekit.agents.voice import Agent, AgentSession  # noqa: PLC0415

    settings = load_settings()
    pricing = _load_pricing()

    # LiveKit gates ``ctx.room.metadata`` and ``ctx.room.local_participant``
    # on the room being connected — accessing them before this raises
    # "cannot access local participant before connecting" and the job
    # crashes. Connect first, then read metadata.
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    raw_metadata = ctx.room.metadata or ""
    if not raw_metadata:
        # Forward-compat fallback: if the room was created without metadata
        # (e.g. by a future code path that only sets it on the participant
        # JWT), read the local participant's ``metadata`` claim instead.
        participant = getattr(ctx.room, "local_participant", None)
        raw_metadata = (getattr(participant, "metadata", None) or "") if participant else ""
    if not raw_metadata:
        raw_metadata = "{}"
    try:
        metadata = json.loads(raw_metadata)
    except json.JSONDecodeError:
        logger.error("voice room has invalid metadata: %r", raw_metadata)
        return

    # Panel calls (mode="panel") host several personas in one room and run the
    # moderated multi-agent loop instead of the single-persona path below.
    if metadata.get("mode") == "panel":
        await _run_panel_session(ctx, settings, pricing, metadata)
        return

    agent_id = metadata.get("agent_id")
    run_id = metadata.get("run_id")
    session_id = metadata.get("session_id")
    user_id = metadata.get("user_id")
    if not (agent_id and run_id and session_id):
        logger.error("voice room missing required metadata: %s", metadata)
        return

    persona = get_persona(agent_id)
    if persona is None:
        logger.error("unknown agent_id from room metadata: %s", agent_id)
        return

    run_context = load_run_context(run_id, manager=None)
    if run_context is None:
        # The worker process can't see FastAPI's in-memory RunManager, so we
        # depend on the Supabase ``runs`` row being present by the time we
        # dispatch. If it isn't (race with store_run, or the user tapped Talk
        # mid-pipeline before the row was written), bail out cleanly. Speaking
        # an apology via raw TTS is brittle — the ElevenLabs plugin's
        # ``synthesize`` returns a ChunkedStream, not a coroutine, so the
        # original ``await tts.synthesize(...)`` crashed the worker.
        logger.error(
            "voice: no run context found for run_id=%s agent=%s — disconnecting",
            run_id,
            agent_id,
        )
        await _publish_data_event(
            ctx.room,
            {
                "kind": "voice_error",
                "code": "no_run_context",
                "message": (
                    "I couldn't load the analysis for this run. It may still "
                    "be finishing — wait a moment and try Talk again."
                ),
            },
        )
        await ctx.room.disconnect()
        return

    system_prompt = build_system_prompt(persona, run_context)
    logger.info(
        "voice session start: session=%s agent=%s run=%s user=%s",
        session_id,
        agent_id,
        run_id,
        user_id,
    )

    session = AgentSession(
        stt=_build_stt(settings),
        llm=_build_llm(settings, persona),
        tts=_build_tts(settings, persona),
        vad=_build_vad(),
    )

    accum = _SessionAccumulators(
        persona=persona,
        run_context=run_context,
        session_id=session_id,
        user_id=user_id,
    )

    # ── Event wiring ────────────────────────────────────────────────────
    # The 1.0 AgentSession exposes ``user_input_transcribed`` (final ASR)
    # and ``conversation_item_added`` (final assistant turn). Older builds
    # used ``transcription``-only events; we register against both names so
    # the worker is resilient to small plugin version skew.

    # ── Latency tracing ────────────────────────────────────────────────
    # We measure end-to-end roundtrip from the moment Deepgram emits an
    # ``is_final`` ASR result to the moment the first assistant turn lands.
    # Both wall-clock timestamps live here so the worker can publish a
    # ``usage`` event the clients log for telemetry. Anything more granular
    # would require pulling from the LiveKit plugin's own metrics surface.
    last_user_final_at: Dict[str, float] = {"t": 0.0}

    async def _on_user_final(text: str) -> None:
        accum.turn_index += 1
        last_user_final_at["t"] = time.time()
        # Persist (fail-open via voice_db).
        await asyncio.to_thread(
            _persist_turn,
            session_id=session_id,
            idx=accum.turn_index,
            role="user",
            text=text,
            provider="deepgram",
            model=settings.deepgram_model,
        )
        # Reconcile detection — only meaningful when talking to the PM.
        if persona.agent_id == "Portfolio Manager" and detect_reconcile_request(text):
            await _publish_data_event(
                ctx.room,
                {
                    "kind": KIND_RECONCILE_REQUESTED,
                    "session_id": session_id,
                    "run_id": run_id,
                    "objection": text,
                },
            )
        await _publish_data_event(
            ctx.room,
            {"kind": KIND_TRANSCRIPT_FINAL, "role": "user", "text": text, "idx": accum.turn_index},
        )

    async def _on_assistant_final(text: str) -> None:
        accum.turn_index += 1
        # Latency: ms from last user ASR-final to assistant turn final.
        if last_user_final_at["t"]:
            rtt_ms = int((time.time() - last_user_final_at["t"]) * 1000.0)
            await _publish_data_event(
                ctx.room,
                {
                    "kind": KIND_USAGE,
                    "session_id": session_id,
                    "rtt_ms": rtt_ms,
                    "turn": accum.turn_index,
                },
            )
            last_user_final_at["t"] = 0.0
        await asyncio.to_thread(
            _persist_turn,
            session_id=session_id,
            idx=accum.turn_index,
            role="assistant",
            text=text,
            provider=settings.tts_provider,
            model=settings.elevenlabs_model
            if settings.tts_provider == "elevenlabs"
            else "cartesia-sonic",
        )
        # Handoff intent detection — scan the assistant's words for "bring in
        # the Bear", etc. Emit at most one signal per session per target.
        suggestion = detect_handoff(
            text,
            source_agent_id=persona.agent_id,
            allowed_targets=persona.handoff_targets,
        )
        if suggestion is not None:
            existing = {h.target_agent_id for h in accum.handoffs}
            if suggestion.target_agent_id not in existing:
                accum.handoffs.append(suggestion)
                await _publish_data_event(ctx.room, suggestion.to_dict())
        await _publish_data_event(
            ctx.room,
            {"kind": KIND_TRANSCRIPT_FINAL, "role": "assistant", "text": text, "idx": accum.turn_index},
        )

    # The 1.0 session emits ``user_input_transcribed`` and
    # ``conversation_item_added`` (assistant turns). We treat both as the
    # canonical "finalized turn" signal.
    @session.on("user_input_transcribed")
    def _wrap_user(ev: Any) -> None:  # noqa: ANN401 — vendor type
        text = getattr(ev, "transcript", None) or getattr(ev, "text", "")
        if not getattr(ev, "is_final", True):
            asyncio.create_task(
                _publish_data_event(
                    ctx.room,
                    {"kind": KIND_TRANSCRIPT_PARTIAL, "role": "user", "text": text},
                )
            )
            return
        asyncio.create_task(_on_user_final(text))

    @session.on("conversation_item_added")
    def _wrap_assistant(ev: Any) -> None:  # noqa: ANN401
        item = getattr(ev, "item", None)
        if item is None or getattr(item, "role", None) != "assistant":
            return
        content = getattr(item, "text_content", None) or getattr(item, "content", "")
        if not content:
            return
        asyncio.create_task(_on_assistant_final(content))

    # ── Hard session cap ───────────────────────────────────────────────
    async def _terminate_after_max() -> None:
        await asyncio.sleep(max(60, settings.session_max_seconds))
        logger.info(
            "voice session %s hit max duration; disconnecting", session_id
        )
        try:
            await session.aclose()
        finally:
            await ctx.shutdown(reason="max-duration-reached")

    asyncio.create_task(_terminate_after_max())

    # ── Start the session and let the agent greet ──────────────────────
    agent = Agent(instructions=system_prompt)
    await session.start(agent=agent, room=ctx.room)

    # The persona's short_intro is delivered as the agent's first utterance.
    # We don't pass it as user input — calling generate_reply with the intro
    # as the assistant's seed makes the model adopt it cleanly.
    try:
        await session.generate_reply(instructions=f"Open with this exact line: {persona.short_intro}")
    except Exception as exc:  # noqa: BLE001 — opener failure is non-fatal
        logger.warning("voice opener failed: %s", exc)

    # ── Block until the session ends, then persist final usage row ─────
    try:
        await session.aclose_event.wait()
    except AttributeError:
        # Older session API: wait on the room disconnect instead.
        await ctx.room.wait_for_disconnect() if hasattr(ctx.room, "wait_for_disconnect") else None

    elapsed = max(0.0, time.time() - accum.started_at)
    # We don't have exact ASR/TTS audio second counts from the plugins in
    # every release, so we estimate them from the wall-clock split: roughly
    # 40% input (user speaking) / 60% output (agent speaking) for a typical
    # explainer session. Operators with hard SLAs should plumb the plugin's
    # ``input_audio_seconds`` / ``output_audio_seconds`` here when available.
    accum.input_audio_seconds = elapsed * 0.4
    accum.output_audio_seconds = elapsed * 0.6

    cost_usd = _estimate_cost(accum, pricing)
    await asyncio.to_thread(
        _finalize_session,
        session_id=session_id,
        status="completed",
        input_audio_seconds=accum.input_audio_seconds,
        output_audio_seconds=accum.output_audio_seconds,
        input_tokens=accum.input_tokens,
        output_tokens=accum.output_tokens,
        cost_usd=cost_usd,
    )
    logger.info(
        "voice session end: session=%s elapsed=%.1fs cost=$%.4f",
        session_id,
        elapsed,
        cost_usd,
    )


def _persist_turn(**kwargs: Any) -> None:
    """Best-effort transcript turn persistence. Runs in a thread."""
    try:
        voice_db.append_turn(**kwargs)
    except Exception as exc:  # noqa: BLE001
        logger.warning("persist turn failed: %s", exc)


def _finalize_session(**kwargs: Any) -> None:
    """Best-effort session-end row update. Runs in a thread."""
    try:
        voice_db.finish_session(**kwargs)
    except Exception as exc:  # noqa: BLE001
        logger.warning("finalize session failed: %s", exc)


def _estimate_cost(accum: _SessionAccumulators, pricing: _Pricing) -> float:
    """Sum ASR + TTS + LLM cost estimates."""
    cost = 0.0
    cost += accum.input_audio_seconds * pricing.asr_per_second
    cost += accum.output_audio_seconds * pricing.tts_per_second
    cost += (accum.input_tokens / 1000.0) * pricing.llm_per_1k_input_tokens
    cost += (accum.output_tokens / 1000.0) * pricing.llm_per_1k_output_tokens
    return round(cost, 5)


def main() -> None:
    """Run the worker. Invoked by ``python -m tradingagents.voice.agent_worker``.

    Reads LiveKit credentials from ``LIVEKIT_URL`` / ``LIVEKIT_API_KEY`` /
    ``LIVEKIT_API_SECRET`` (same env vars the FastAPI router uses to mint
    tokens). The worker registers with the LiveKit server and waits for
    dispatch — typically there are 1-2 worker processes per region.
    """
    _lazy_import_livekit()
    from livekit.agents import WorkerOptions, cli  # noqa: PLC0415

    settings = load_settings()
    ok, reason = settings.fully_configured
    if not ok:
        raise SystemExit(f"voice worker cannot start: {reason}")

    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            # The dispatcher will assign us any room whose name starts with
            # "voice-" (created exclusively by web/voice/router.py).
            agent_name="tradingagents-voice",
        )
    )


if __name__ == "__main__":
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    main()
