"""Tests for the pure multi-agent panel orchestration (tradingagents/voice/panel.py).

These exercise the moderator's speaker-selection precedence, roster
validation, name addressing, and prompt framing — no LiveKit/audio/network.
"""

import pytest

from tradingagents.voice import panel


# ── normalize_roster ────────────────────────────────────────────────────────

def test_normalize_roster_dedups_preserving_order():
    out = panel.normalize_roster(
        ["Bull Researcher", "Bear Researcher", "Bull Researcher"], max_agents=4
    )
    assert out == ("Bull Researcher", "Bear Researcher")


def test_normalize_roster_rejects_unknown_agent():
    with pytest.raises(ValueError, match="unknown agent_id"):
        panel.normalize_roster(["Bull Researcher", "Nobody"], max_agents=4)


def test_normalize_roster_requires_min_two():
    with pytest.raises(ValueError, match="at least 2"):
        panel.normalize_roster(["Bull Researcher"], max_agents=4)


def test_normalize_roster_enforces_cap():
    six = [
        "Market Analyst", "Sentiment Analyst", "News Analyst",
        "Fundamentals Analyst", "Bull Researcher",
    ]
    with pytest.raises(ValueError, match="capped at 3"):
        panel.normalize_roster(six, max_agents=3)


def test_normalize_roster_clamps_cap_to_hard_ceiling():
    # A mis-set env asking for 100 is clamped to the hard ceiling (6), so a
    # 7-agent roster is still rejected.
    seven = [
        "Market Analyst", "Sentiment Analyst", "News Analyst",
        "Fundamentals Analyst", "Bull Researcher", "Bear Researcher",
        "Research Manager",
    ]
    with pytest.raises(ValueError, match="capped at 6"):
        panel.normalize_roster(seven, max_agents=100)


# ── lead_agent ──────────────────────────────────────────────────────────────

def test_lead_prefers_portfolio_manager():
    assert panel.lead_agent(["Bull Researcher", "Portfolio Manager"]) == "Portfolio Manager"


def test_lead_falls_back_to_first():
    assert panel.lead_agent(["Bull Researcher", "Bear Researcher"]) == "Bull Researcher"


# ── detect_addressed_agent ──────────────────────────────────────────────────

def test_detect_addressed_by_role_word():
    roster = ["Bull Researcher", "Bear Researcher"]
    assert panel.detect_addressed_agent("Bear, what's the risk here?", roster) == "Bear Researcher"


def test_detect_addressed_full_name_beats_partial():
    roster = ["Bull Researcher", "Bear Researcher", "Portfolio Manager"]
    # "Portfolio Manager" full name present.
    assert panel.detect_addressed_agent(
        "Portfolio Manager, give me the final call", roster
    ) == "Portfolio Manager"


def test_detect_addressed_none_when_absent():
    roster = ["Bull Researcher", "Bear Researcher"]
    assert panel.detect_addressed_agent("what do you all think?", roster) is None


def test_detect_addressed_ignores_offroster_name():
    roster = ["Bull Researcher", "Bear Researcher"]
    # Trader isn't on this panel, so naming it doesn't route.
    assert panel.detect_addressed_agent("Trader, size it up", roster) is None


# ── select_speaker precedence ───────────────────────────────────────────────

def test_select_directed_wins():
    roster = ["Bull Researcher", "Bear Researcher"]
    assert panel.select_speaker(
        "chart looks strong", roster, directed_agent_id="Bear Researcher"
    ) == "Bear Researcher"


def test_select_directed_ignored_when_offroster():
    roster = ["Bull Researcher", "Bear Researcher"]
    # Directed to someone not on the panel → fall through to topic affinity.
    picked = panel.select_speaker(
        "what's the bear risk?", roster, directed_agent_id="Trader"
    )
    assert picked == "Bear Researcher"


def test_select_addressed_beats_topic():
    roster = ["Bull Researcher", "Bear Researcher"]
    # Text is topically bullish ("upside") but addresses the Bear by name.
    assert panel.select_speaker("Bear, isn't the upside already priced?", roster) == "Bear Researcher"


def test_select_topic_affinity():
    roster = ["Market Analyst", "Fundamentals Analyst"]
    assert panel.select_speaker("what does the RSI and moving average say?", roster) == "Market Analyst"
    assert panel.select_speaker("walk me through the margins and valuation", roster) == "Fundamentals Analyst"


def test_select_falls_back_to_lead_when_no_signal():
    roster = ["Bull Researcher", "Portfolio Manager"]
    # No keyword/name signal → lead (PM).
    assert panel.select_speaker("hey there", roster) == "Portfolio Manager"


def test_select_avoids_repeating_last_speaker_on_fallback():
    roster = ["Bull Researcher", "Portfolio Manager"]
    # Lead is PM, but PM just spoke and there's no signal → pick the other.
    assert panel.select_speaker("hmm ok", roster, last_speaker="Portfolio Manager") == "Bull Researcher"


def test_select_avoids_repeat_on_topic_tie():
    roster = ["Bull Researcher", "Bear Researcher"]
    # Neutral text → both score 0 → lead is Bull (first); if Bull just spoke,
    # move to Bear.
    assert panel.select_speaker("go on", roster, last_speaker="Bull Researcher") == "Bear Researcher"


# ── prompt + roster payloads ────────────────────────────────────────────────

def test_panel_prompt_appends_panel_note_and_lists_others():
    from tradingagents.voice.personas import get_persona

    bull = get_persona("Bull Researcher")
    prompt = panel.build_panel_system_prompt(
        bull, "BASE PROMPT BODY", ["Bull Researcher", "Bear Researcher"]
    )
    assert "BASE PROMPT BODY" in prompt
    assert "PANEL MODE:" in prompt
    assert "Bear Researcher" in prompt          # the other panelist listed
    assert "ONE panelist speaks per question" in prompt


def test_roster_payload_shape():
    from tradingagents.voice.personas import get_persona

    personas = [get_persona("Bull Researcher"), get_persona("Bear Researcher")]
    payload = panel.panel_roster_payload(personas)
    assert [p["agent_id"] for p in payload] == ["Bull Researcher", "Bear Researcher"]
    assert all({"agent_id", "display_name", "voice_name"} <= set(p) for p in payload)
