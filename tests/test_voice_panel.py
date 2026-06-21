"""Unit tests for the voice panel (group-call) layer.

Covers:

* :func:`tradingagents.voice.panel.build_panel_system_prompt` — every selected
  persona's identity + style + voice name appears in the rendered prompt, the
  output-contract block is present, and the OPENER lists each panelist in
  roster order.
* :func:`tradingagents.voice.panel.parse_orchestrator_output` — strict JSON
  happy path, the ``[Persona] ... [Persona] ...`` bracket-prose fallback,
  and graceful ``[]`` on garbage. Importantly, the parser must NEVER raise —
  voice loops crash hard on uncaught exceptions.
* :func:`web.voice.router._resolve_agent_ids` and ``_resolve_personas`` —
  the panel-mode validation surface: unknown ids → 400, too many → 400,
  duplicates → 400, single-agent backward-compat is unchanged.
* :func:`tradingagents.voice.handoff.detect_handoff` — when the target is
  already on the panel the suggestion is suppressed (no-op chip).
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from tradingagents.voice.context_loader import AgentReport, RunContext
from tradingagents.voice.handoff import detect_handoff
from tradingagents.voice.panel import (
    PanelUtterance,
    build_panel_system_prompt,
    parse_orchestrator_output,
)
from tradingagents.voice.personas import get_persona
from tradingagents.voice.settings import VoiceSettings
from web.voice.router import (
    VoiceSessionRequest,
    _build_room_name,
    _resolve_agent_ids,
    _resolve_personas,
)


def _ctx() -> RunContext:
    return RunContext(
        run_id="run-1",
        ticker="RKLB",
        trade_date="2026-06-21",
        asset_type="stock",
        decision="Buy",
        final_rationale="Margins expanded; growth visibility improved.",
        identity="RKLB — Rocket Lab USA, Inc.",
        provider="anthropic",
        deep_model="claude-opus-4",
        quick_model="claude-haiku-4-5",
        agents={
            "Bull Researcher": AgentReport(
                agent="Bull Researcher",
                content="Bull case: launches are accelerating.",
                seq=0,
            ),
            "Bear Researcher": AgentReport(
                agent="Bear Researcher",
                content="Bear case: cash burn risk.",
                seq=1,
            ),
            "Portfolio Manager": AgentReport(
                agent="Portfolio Manager",
                content="Verdict: tilt overweight.",
                seq=2,
            ),
        },
    )


# ── Prompt assembly ────────────────────────────────────────────────────────


def test_build_panel_system_prompt_includes_all_personas() -> None:
    """Every selected persona's display name, voice name, and style prompt
    must appear in the rendered orchestrator prompt — that's how Claude
    knows the roster and how to stay in character per panelist."""
    personas = [
        get_persona("Bull Researcher"),
        get_persona("Bear Researcher"),
        get_persona("Portfolio Manager"),
    ]
    assert all(p is not None for p in personas)
    prompt = build_panel_system_prompt(personas, _ctx())  # type: ignore[arg-type]

    for p in personas:
        assert p.display_name in prompt
        assert p.voice_name in prompt
        # Style prompts can be long — match a recognisable phrase from each
        # to confirm the actual style text is included, not just the name.
        assert p.style_prompt.split(".")[0][:30] in prompt


def test_build_panel_system_prompt_lists_opener_in_roster_order() -> None:
    personas = [get_persona("Bull Researcher"), get_persona("Bear Researcher")]
    prompt = build_panel_system_prompt(personas, _ctx())  # type: ignore[arg-type]
    bull_idx = prompt.index("Bull Researcher:")
    bear_idx = prompt.index("Bear Researcher:")
    assert bull_idx < bear_idx


def test_build_panel_system_prompt_documents_json_contract() -> None:
    """The prompt must spell out the JSON array contract so the LLM emits
    parseable output (the parser is tolerant but we still want the strict
    path to hit on >99% of turns)."""
    personas = [get_persona("Bull Researcher"), get_persona("Bear Researcher")]
    prompt = build_panel_system_prompt(personas, _ctx())  # type: ignore[arg-type]
    assert '"persona"' in prompt
    assert '"text"' in prompt
    assert "JSON array" in prompt


def test_build_panel_system_prompt_rejects_empty_panel() -> None:
    with pytest.raises(ValueError):
        build_panel_system_prompt([], _ctx())


# ── Parser ─────────────────────────────────────────────────────────────────


def test_parse_orchestrator_output_strict_json_happy_path() -> None:
    raw = (
        '[{"persona": "Bull Researcher", "text": "I still like this here."},'
        ' {"persona": "Bear Researcher", "text": "The multiple already bakes that in."}]'
    )
    out = parse_orchestrator_output(
        raw, allowed_persona_ids={"Bull Researcher", "Bear Researcher"}
    )
    assert out == [
        PanelUtterance(persona_agent_id="Bull Researcher", text="I still like this here."),
        PanelUtterance(
            persona_agent_id="Bear Researcher",
            text="The multiple already bakes that in.",
        ),
    ]


def test_parse_orchestrator_output_strips_code_fences() -> None:
    raw = (
        "```json\n"
        '[{"persona": "Bull Researcher", "text": "fenced"}]\n'
        "```"
    )
    out = parse_orchestrator_output(raw, allowed_persona_ids={"Bull Researcher"})
    assert out == [PanelUtterance("Bull Researcher", "fenced")]


def test_parse_orchestrator_output_bracketed_fallback() -> None:
    """``[Bull] ... [Bear] ...`` shape is the most common LLM-drift case;
    it must parse cleanly even when the model drops the JSON contract."""
    raw = (
        "[Bull Researcher] I see margin expansion holding. "
        "[Bear Researcher] You're underweighting the dilution risk."
    )
    out = parse_orchestrator_output(
        raw, allowed_persona_ids={"Bull Researcher", "Bear Researcher"}
    )
    assert len(out) == 2
    assert out[0].persona_agent_id == "Bull Researcher"
    assert "margin expansion" in out[0].text
    assert out[1].persona_agent_id == "Bear Researcher"
    assert "dilution" in out[1].text


def test_parse_orchestrator_output_bracketed_shortname_routes() -> None:
    """``[Bull]`` resolves to the full ``Bull Researcher`` id when that's the
    only allowed persona whose name contains the label."""
    raw = "[Bull] Optimistic. [Bear] Cautious."
    out = parse_orchestrator_output(
        raw, allowed_persona_ids={"Bull Researcher", "Bear Researcher"}
    )
    assert [u.persona_agent_id for u in out] == [
        "Bull Researcher",
        "Bear Researcher",
    ]


def test_parse_orchestrator_output_returns_empty_on_garbage() -> None:
    """Unstructured prose with no JSON and no bracket markers parses to ``[]``."""
    assert parse_orchestrator_output("Hi there, how are you?") == []
    assert parse_orchestrator_output("") == []
    assert parse_orchestrator_output("   ") == []


def test_parse_orchestrator_output_drops_unknown_personas() -> None:
    """If the LLM hallucinates a persona that isn't on the panel we drop the
    utterance rather than route it to the primary."""
    raw = (
        '[{"persona": "Bull Researcher", "text": "real"},'
        ' {"persona": "Phantom Analyst", "text": "fake"}]'
    )
    out = parse_orchestrator_output(
        raw, allowed_persona_ids={"Bull Researcher", "Bear Researcher"}
    )
    assert out == [PanelUtterance("Bull Researcher", "real")]


def test_parse_orchestrator_output_never_raises_on_malformed_json() -> None:
    """Voice loops crash hard on uncaught exceptions — parser must swallow
    every malformed input shape and degrade to ``[]`` instead."""
    # Mismatched braces
    assert parse_orchestrator_output("[{persona: bull, text: hi}") == []
    # Mid-string truncation
    assert parse_orchestrator_output('[{"persona": "Bull"') == []
    # Wrong-type values
    assert (
        parse_orchestrator_output('[{"persona": 1, "text": 2}]', allowed_persona_ids={"Bull Researcher"})
        == []
    )


# ── Router validation ─────────────────────────────────────────────────────


def _make_settings(panel_enabled: bool = True) -> VoiceSettings:
    return VoiceSettings(
        enabled=True,
        panel_enabled=panel_enabled,
        livekit_url="wss://x",
        livekit_api_key="k",
        livekit_api_secret="s",
        deepgram_api_key="d",
        deepgram_model="nova-3",
        tts_provider="elevenlabs",
        fallback_provider=None,
        elevenlabs_api_key="e",
        elevenlabs_model="eleven_flash_v2_5",
        cartesia_api_key=None,
        voice_llm_provider="anthropic",
        voice_llm_model="claude-haiku-4-5",
        anthropic_api_key="a",
        session_max_seconds=600,
        daily_minutes_per_user=30,
        hourly_sessions_per_user=12,
        token_ttl_seconds=1800,
    )


def test_router_resolve_agent_ids_single_backward_compat() -> None:
    """``agent_id`` alone keeps working — the v1 contract is untouched."""
    body = VoiceSessionRequest(run_id="r1", agent_id="Bull Researcher")
    ids = _resolve_agent_ids(body, _make_settings())
    assert ids == ["Bull Researcher"]


def test_router_resolve_agent_ids_panel_happy_path() -> None:
    body = VoiceSessionRequest(
        run_id="r1",
        agent_ids=["Bull Researcher", "Bear Researcher", "Portfolio Manager"],
    )
    assert _resolve_agent_ids(body, _make_settings()) == [
        "Bull Researcher",
        "Bear Researcher",
        "Portfolio Manager",
    ]


def test_router_resolve_agent_ids_rejects_over_three() -> None:
    body = VoiceSessionRequest(
        run_id="r1",
        agent_ids=["A", "B", "C", "D"],
    )
    with pytest.raises(HTTPException) as exc:
        _resolve_agent_ids(body, _make_settings())
    assert exc.value.status_code == 400


def test_router_resolve_agent_ids_rejects_duplicates() -> None:
    body = VoiceSessionRequest(
        run_id="r1",
        agent_ids=["Bull Researcher", "Bull Researcher"],
    )
    with pytest.raises(HTTPException) as exc:
        _resolve_agent_ids(body, _make_settings())
    assert exc.value.status_code == 400


def test_router_resolve_agent_ids_requires_field() -> None:
    """Missing both ``agent_id`` and ``agent_ids`` → 400."""
    body = VoiceSessionRequest(run_id="r1")
    with pytest.raises(HTTPException) as exc:
        _resolve_agent_ids(body, _make_settings())
    assert exc.value.status_code == 400


def test_router_resolve_agent_ids_blocks_panel_when_flag_off() -> None:
    body = VoiceSessionRequest(
        run_id="r1",
        agent_ids=["Bull Researcher", "Bear Researcher"],
    )
    with pytest.raises(HTTPException) as exc:
        _resolve_agent_ids(body, _make_settings(panel_enabled=False))
    # 403 — feature is disabled, not a malformed request.
    assert exc.value.status_code == 403


def test_router_resolve_agent_ids_single_via_list_allowed_with_flag_off() -> None:
    """A one-element ``agent_ids`` payload is identical to single-agent and
    must work even when the panel flag is off — collapsing the panel down
    to one persona is not really "panel mode"."""
    body = VoiceSessionRequest(run_id="r1", agent_ids=["Bull Researcher"])
    ids = _resolve_agent_ids(body, _make_settings(panel_enabled=False))
    assert ids == ["Bull Researcher"]


def test_router_resolve_personas_rejects_unknown_id() -> None:
    with pytest.raises(HTTPException) as exc:
        _resolve_personas(["Bull Researcher", "Phantom Analyst"], user_id=None)
    assert exc.value.status_code == 400
    assert "Phantom Analyst" in str(exc.value.detail)


def test_router_build_room_name_panel_is_stable_on_reorder() -> None:
    """Same set of agents (any order) ⇒ same hashed segment in the room name,
    so re-joining the panel resolves to the same room."""
    name_a = _build_room_name(
        "run-1",
        ["Bull Researcher", "Bear Researcher"],
        session_id="abcdef1234567890",
        is_panel=True,
    )
    name_b = _build_room_name(
        "run-1",
        ["Bear Researcher", "Bull Researcher"],
        session_id="abcdef1234567890",
        is_panel=True,
    )
    # Hash segment matches; session suffix is the same too.
    assert "panel" in name_a
    assert name_a == name_b


def test_router_build_room_name_single_matches_legacy_shape() -> None:
    """The single-agent room name must keep the original
    ``voice-{run}-{slug}-{sid8}`` shape so dashboards keep parsing it."""
    name = _build_room_name(
        "run-1",
        ["Bull Researcher"],
        session_id="abcdef1234567890",
        is_panel=False,
    )
    assert name == "voice-run-1-bull-researcher-abcdef12"


# ── Handoff suppression on panel ──────────────────────────────────────────


def test_persona_handoff_suppressed_when_target_in_panel() -> None:
    """A panel that already has the Bear shouldn't surface a "bring in the
    Bear" chip — the user is already talking to them."""
    suggestion = detect_handoff(
        "Let me bring in the Bear Researcher on the leverage risk.",
        source_agent_id="Bull Researcher",
        allowed_targets=("Bear Researcher", "Research Manager"),
        already_in_session=("Bull Researcher", "Bear Researcher"),
    )
    assert suggestion is None


def test_persona_handoff_still_fires_when_target_not_in_panel() -> None:
    suggestion = detect_handoff(
        "Let me bring in the Research Manager.",
        source_agent_id="Bull Researcher",
        allowed_targets=("Research Manager", "Bear Researcher"),
        already_in_session=("Bull Researcher", "Bear Researcher"),
    )
    assert suggestion is not None
    assert suggestion.target_agent_id == "Research Manager"
