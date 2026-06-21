"""Unit tests for the voice persona A/B framework.

Locks in the contract that ``get_persona_for_user`` returns the variant we
expect for the four interesting cases (no experiment, experiment with full A,
experiment with full B, and a 50/50 split). The 50/50 case is the only
non-trivial path — it relies on a stable SHA-256 hash over
``(agent_id, user_id)`` so a given user always stays in the same bucket.
"""

from __future__ import annotations

import os

import pytest

from tradingagents.voice import personas as voice_personas


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip any leaked VOICE_OVERRIDE / VARIANT env between tests."""
    for key in list(os.environ):
        if key.startswith("TRADINGAGENTS_VOICE_OVERRIDE_") or key.startswith(
            "TRADINGAGENTS_VOICE_VARIANT_"
        ):
            monkeypatch.delenv(key, raising=False)


def test_anonymous_user_gets_variant_a() -> None:
    """No user id → never randomise — always A so the experiment is opt-in."""
    result = voice_personas.get_persona_for_user("Bull Researcher", user_id=None)
    assert result is not None
    _persona, variant = result
    assert variant == "A"


def test_unknown_agent_returns_none() -> None:
    assert voice_personas.get_persona_for_user("Nonexistent Agent", "u1") is None


def test_override_wins_over_variant(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pinned ``OVERRIDE`` env disables the A/B experiment entirely."""
    monkeypatch.setenv(
        "TRADINGAGENTS_VOICE_OVERRIDE_BULL_RESEARCHER", "pinned-voice"
    )
    monkeypatch.setenv(
        "TRADINGAGENTS_VOICE_VARIANT_B_BULL_RESEARCHER", "experiment-voice"
    )
    monkeypatch.setenv("TRADINGAGENTS_VOICE_VARIANT_A_SPLIT", "0")
    result = voice_personas.get_persona_for_user("Bull Researcher", "u1")
    assert result is not None
    persona, variant = result
    # Pinned override always overrides; variant is normalised to A.
    assert persona.voice_id == "pinned-voice"
    assert variant == "A"


def test_full_b_assignment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "TRADINGAGENTS_VOICE_VARIANT_B_BULL_RESEARCHER", "alt-voice"
    )
    monkeypatch.setenv("TRADINGAGENTS_VOICE_VARIANT_A_SPLIT", "0")
    result = voice_personas.get_persona_for_user("Bull Researcher", "user-x")
    assert result is not None
    persona, variant = result
    assert variant == "B"
    assert persona.voice_id == "alt-voice"


def test_split_assignment_is_stable(monkeypatch: pytest.MonkeyPatch) -> None:
    """A given (agent, user) tuple always hashes to the same bucket."""
    monkeypatch.setenv(
        "TRADINGAGENTS_VOICE_VARIANT_B_BULL_RESEARCHER", "alt-voice"
    )
    monkeypatch.setenv("TRADINGAGENTS_VOICE_VARIANT_A_SPLIT", "50")
    seen = {
        voice_personas.assign_variant("Bull Researcher", f"u{i}")
        for _ in range(5)
        for i in range(20)
    }
    # Reproducibility: 5 passes × 20 users => 100 calls — still only 2 outcomes.
    assert seen <= {"A", "B"}
    a = voice_personas.assign_variant("Bull Researcher", "user-fixed")
    for _ in range(20):
        assert voice_personas.assign_variant("Bull Researcher", "user-fixed") == a


def test_split_distribution_is_roughly_balanced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "TRADINGAGENTS_VOICE_VARIANT_B_BULL_RESEARCHER", "alt-voice"
    )
    monkeypatch.setenv("TRADINGAGENTS_VOICE_VARIANT_A_SPLIT", "50")
    a, b = 0, 0
    for i in range(1000):
        variant = voice_personas.assign_variant("Bull Researcher", f"u{i}")
        if variant == "A":
            a += 1
        else:
            b += 1
    # Allow ±10% drift around the 50/50 target; the hash distribution is
    # uniform enough that this should hold every run.
    assert abs(a - b) < 200, f"unbalanced split: A={a} B={b}"
