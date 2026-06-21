"""Unit tests for cross-agent handoff + PM reconcile signal detection."""

from __future__ import annotations

from tradingagents.voice.handoff import (
    HandoffSuggested,
    coalesce_handoffs,
    detect_handoff,
    detect_reconcile_request,
)


def test_detect_handoff_picks_first_named_target() -> None:
    """The earliest intent + named target wins, even if a later one exists."""
    text = (
        "Let me bring in the Bear Researcher on the leverage risk, "
        "and we should also pull in the Bull Researcher to defend it."
    )
    result = detect_handoff(
        text,
        source_agent_id="Portfolio Manager",
        allowed_targets=["Bear Researcher", "Bull Researcher"],
    )
    assert result is not None
    assert result.target_agent_id == "Bear Researcher"
    assert "Bear Researcher" in result.quote


def test_detect_handoff_respects_allowed_targets() -> None:
    """A persona can't hand off to an agent not in its allow list."""
    text = "Let's bring in the Trader on this one."
    result = detect_handoff(
        text,
        source_agent_id="Bull Researcher",
        allowed_targets=["Bear Researcher", "Research Manager"],
    )
    assert result is None


def test_detect_handoff_returns_none_without_intent_phrase() -> None:
    """Saying an agent's name without an intent verb is not a handoff."""
    result = detect_handoff(
        "The Bear Researcher disagreed with my conclusion.",
        source_agent_id="Bull Researcher",
        allowed_targets=["Bear Researcher"],
    )
    assert result is None


def test_detect_handoff_prefers_long_match() -> None:
    """When two allowed targets share a prefix the longer one wins."""
    text = "Why don't we hand off to the Bear Researcher real quick."
    result = detect_handoff(
        text,
        source_agent_id="Bull Researcher",
        allowed_targets=["Bear", "Bear Researcher"],
    )
    assert result is not None
    assert result.target_agent_id == "Bear Researcher"


def test_coalesce_handoffs_drops_duplicates() -> None:
    items = [
        HandoffSuggested("Bull Researcher", "Bear Researcher", "q1"),
        HandoffSuggested("Bull Researcher", "Bear Researcher", "q2"),
        HandoffSuggested("Bull Researcher", "Research Manager", "q3"),
    ]
    coalesced = coalesce_handoffs(items)
    assert [h.target_agent_id for h in coalesced] == [
        "Bear Researcher",
        "Research Manager",
    ]
    # First occurrence wins, so we keep "q1" not "q2".
    assert coalesced[0].quote == "q1"


def test_detect_reconcile_request_positive() -> None:
    assert detect_reconcile_request("Can you reconsider the rating?") is True
    assert detect_reconcile_request("I think you should reconcile this.") is True
    assert (
        detect_reconcile_request("Please change your mind on the call.") is True
    )


def test_detect_reconcile_request_negative() -> None:
    assert detect_reconcile_request("") is False
    assert detect_reconcile_request("Tell me more about the bull case.") is False
    assert (
        detect_reconcile_request("I disagree with the rating but it's fine.")
        is False
    )
