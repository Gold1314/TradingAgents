"""Multi-agent *panel* call orchestration (moderated turn-taking).

A panel call puts several personas in **one** LiveKit room and lets the user
converse with all of them in a single continuous session — instead of the
solo model where each agent lives in its own room and switching means a
tear-down-and-redial (see :mod:`web.voice.router`).

Interaction model (chosen with the product owner):

* **Moderated turn-taking** — exactly one panelist speaks per user turn. A
  cheap, deterministic moderator (:func:`select_speaker`) routes each user
  question to the best-fit agent; there is no talking over each other.
* **Auto + user-directed** — the moderator auto-picks by default, but the user
  can address a specific panelist (tap a chip → the client publishes a
  ``panel.direct`` data message, or simply say "Bear, what do you think" and
  the same name-detector routes the turn).

Everything in this module is **pure and synchronous** so it can be unit-tested
without LiveKit, audio, or network. The worker (:mod:`tradingagents.voice.agent_worker`)
drives the LiveKit ``AgentSession`` around these decisions; the FastAPI router
(:mod:`web.voice.router`) validates panel rosters with :func:`normalize_roster`.
"""

from __future__ import annotations

import re
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from tradingagents.voice.personas import PERSONAS, VoicePersona

# ── Data-channel payload kinds (panel-specific) ────────────────────────────
# Kept here next to the panel logic but conceptually part of the shared
# data-channel contract in :mod:`tradingagents.voice.handoff`. All three
# clients (web/iOS/RN) consume these — keep the string values stable.
#
# Server → client:
KIND_PANEL_SPEAKER = "panel.speaker"       # who is about to speak this turn
KIND_PANEL_ROSTER = "panel.roster"         # the panelist list at call start
# Client → server:
KIND_PANEL_DIRECT = "panel.direct"         # user directs the next answer to an agent

# A panel must have at least two agents (otherwise it's a solo call) and is
# capped to keep latency and per-turn cost bounded. The upper bound is also
# enforced server-side from settings; this is the hard ceiling.
MIN_PANEL_AGENTS = 2
MAX_PANEL_AGENTS_HARD = 6


# Topic affinity keywords per agent. Deterministic, cheap, and testable — this
# is the moderator's routing table when the user hasn't addressed anyone by
# name. Intentionally kept in the panel layer (not on the frozen
# ``VoicePersona``) so tuning routing never risks the persisted persona spec.
# Match is word-boundary, case-insensitive; longer phrases score higher.
TOPIC_KEYWORDS: Mapping[str, Tuple[str, ...]] = {
    "Market Analyst": (
        "chart", "technical", "technicals", "price", "momentum", "trend",
        "support", "resistance", "rsi", "moving average", "sma", "volume",
        "breakout", "timeframe",
    ),
    "Sentiment Analyst": (
        "sentiment", "social", "reddit", "stocktwits", "twitter", "retail",
        "hype", "buzz", "options flow", "crowd", "mood",
    ),
    "News Analyst": (
        "news", "headline", "macro", "catalyst", "earnings date", "filing",
        "insider", "sector", "announcement", "priced in",
    ),
    "Fundamentals Analyst": (
        "fundamentals", "valuation", "multiple", "pe", "p/e", "margin",
        "margins", "revenue", "growth", "balance sheet", "cash flow", "fcf",
        "roic", "earnings", "moat",
    ),
    "Bull Researcher": (
        "bull", "bullish", "upside", "buy case", "why buy", "long thesis",
        "optimistic", "opportunity",
    ),
    "Bear Researcher": (
        "bear", "bearish", "downside", "risk", "risks", "short", "trap",
        "overvalued", "concern", "concerns", "what could go wrong",
    ),
    "Research Manager": (
        "debate", "verdict", "judge", "both sides", "consensus",
        "investment plan", "who won", "synthesis",
    ),
    "Trader": (
        "trade", "execute", "execution", "entry", "exit", "stop", "size",
        "sizing", "position", "how would you buy", "today",
    ),
    "Aggressive Analyst": (
        "aggressive", "asymmetric", "lean in", "tail", "kelly", "leverage",
        "high conviction",
    ),
    "Conservative Analyst": (
        "conservative", "protect", "capital", "drawdown", "hedge", "cautious",
        "position sizing", "staged", "safe",
    ),
    "Neutral Analyst": (
        "neutral", "balance", "balanced", "middle", "arbitrate", "fair",
        "both camps",
    ),
    "Portfolio Manager": (
        "final", "decision", "rating", "call", "overall", "verdict",
        "recommendation", "bottom line", "your call",
    ),
}


def normalize_roster(
    agent_ids: Sequence[str], *, max_agents: int
) -> Tuple[str, ...]:
    """Validate + de-duplicate a requested panel roster, preserving order.

    Raises :class:`ValueError` (the router maps this to a 400) when the roster
    is too small, too large, or names an unknown agent. De-duplicates while
    keeping first-seen order so ``["Bull","Bear","Bull"]`` → ``("Bull","Bear")``.

    ``max_agents`` is the operator-configured cap; it is clamped to
    :data:`MAX_PANEL_AGENTS_HARD` so a mis-set env can't blow up latency/cost.
    """
    ceiling = max(MIN_PANEL_AGENTS, min(max_agents, MAX_PANEL_AGENTS_HARD))
    seen: List[str] = []
    for aid in agent_ids:
        name = (aid or "").strip()
        if not name:
            continue
        if name not in PERSONAS:
            raise ValueError(f"unknown agent_id: {name}")
        if name not in seen:
            seen.append(name)
    if len(seen) < MIN_PANEL_AGENTS:
        raise ValueError(
            f"a panel needs at least {MIN_PANEL_AGENTS} distinct agents"
        )
    if len(seen) > ceiling:
        raise ValueError(f"a panel is capped at {ceiling} agents")
    return tuple(seen)


def lead_agent(roster: Sequence[str]) -> str:
    """The panel's default speaker when nothing else routes a turn.

    Prefers the Portfolio Manager (owns the final call, natural moderator);
    otherwise the first agent in the roster. Assumes a non-empty roster.
    """
    if "Portfolio Manager" in roster:
        return "Portfolio Manager"
    return roster[0]


def detect_addressed_agent(
    text: str, roster: Sequence[str]
) -> Optional[str]:
    """Return the roster agent the user addressed by name, if any.

    Matches a persona's display name OR its short role word (the first token,
    e.g. "Bull", "Bear", "Trader", "Fundamentals") on a word boundary. Longer
    names are matched first so "Bull Researcher" wins over a bare "Bull". Used
    both for the user's spoken turn and as a fallback for a typed direct.
    """
    if not text:
        return None
    lowered = text.lower()
    # Build (needle, agent_id) pairs, longest needle first for specificity.
    needles: List[Tuple[str, str]] = []
    for aid in roster:
        needles.append((aid.lower(), aid))
        first = aid.split()[0].lower()
        if first != aid.lower():
            needles.append((first, aid))
    for needle, aid in sorted(needles, key=lambda p: len(p[0]), reverse=True):
        if re.search(r"\b" + re.escape(needle) + r"\b", lowered):
            return aid
    return None


def _affinity_score(text_lower: str, agent_id: str) -> int:
    """Count topic-keyword hits for one agent in the (lowercased) user text."""
    score = 0
    for kw in TOPIC_KEYWORDS.get(agent_id, ()):  # noqa: SIM118 — Mapping.get
        if re.search(r"\b" + re.escape(kw) + r"\b", text_lower):
            # Weight multi-word phrases higher — they're more specific signals.
            score += 2 if " " in kw else 1
    return score


def select_speaker(
    user_text: str,
    roster: Sequence[str],
    *,
    directed_agent_id: Optional[str] = None,
    last_speaker: Optional[str] = None,
) -> str:
    """Pick which panelist answers this user turn (the moderator).

    Precedence:
      1. **Directed** — an explicit ``panel.direct`` from the client (user
         tapped a panelist). Honored only if the target is on the roster.
      2. **Addressed by name** — the user named a panelist in their utterance
         ("Bear, what's the risk?").
      3. **Topic affinity** — highest keyword-match score across the roster.
      4. **Lead** — the Portfolio Manager (or first agent) as the default.

    Tie-breaks and the fallback both avoid immediately repeating
    ``last_speaker`` when another qualified agent exists, so the panel doesn't
    get monopolized by one voice. Assumes a non-empty roster.
    """
    roster = list(roster)
    if directed_agent_id and directed_agent_id in roster:
        return directed_agent_id

    addressed = detect_addressed_agent(user_text, roster)
    if addressed is not None:
        return addressed

    text_lower = (user_text or "").lower()
    scored: List[Tuple[int, str]] = [
        (_affinity_score(text_lower, aid), aid) for aid in roster
    ]
    best = max(s for s, _ in scored)
    if best > 0:
        # Winners at the top score, in roster order; skip the last speaker if
        # a different top-scorer exists (keeps the conversation moving).
        winners = [aid for s, aid in scored if s == best]
        for aid in winners:
            if aid != last_speaker:
                return aid
        return winners[0]

    # No topical signal — fall back to the lead, avoiding an immediate repeat.
    lead = lead_agent(roster)
    if lead == last_speaker and len(roster) > 1:
        for aid in roster:
            if aid != last_speaker:
                return aid
    return lead


def panel_roster_payload(personas: Sequence[VoicePersona]) -> List[Dict[str, str]]:
    """The ``panel.roster`` data-channel payload — one entry per panelist.

    Clients render this as the in-call roster strip (avatar + name) and use
    ``agent_id`` both to attribute transcript turns and to direct questions.
    """
    return [
        {
            "agent_id": p.agent_id,
            "display_name": p.display_name,
            "voice_name": p.voice_name,
        }
        for p in personas
    ]


def build_panel_system_prompt(
    persona: VoicePersona,
    base_solo_prompt: str,
    roster_display: Sequence[str],
) -> str:
    """Wrap a persona's solo system prompt with panel-mode framing.

    ``base_solo_prompt`` is whatever :func:`context_loader.build_system_prompt`
    produced for this persona; we append panel-specific behavior so the agent
    knows it shares the room, must be brief, and should defer rather than
    speak for others. Keeping the solo prompt intact means panel and solo
    calls stay factually consistent.
    """
    others = [name for name in roster_display if name != persona.display_name]
    others_line = ", ".join(others) if others else "no one else"
    panel_note = "\n".join(
        [
            "PANEL MODE:",
            f"- You are on a live panel with: {others_line}. The user can hear "
            "all of you and is addressing the panel.",
            "- Only ONE panelist speaks per question. You were picked to answer "
            "THIS one, so answer it directly and concisely (1–3 sentences).",
            "- Stay strictly in your lane. If the question is really about "
            "another panelist's expertise, give a one-line take and explicitly "
            "suggest the user hear from them by name (e.g. 'the Bear should "
            "weigh in on that risk').",
            "- Do not speak for the other panelists or narrate what they would "
            "say. Do not greet or re-introduce yourself every turn.",
        ]
    )
    return base_solo_prompt + "\n\n" + panel_note
