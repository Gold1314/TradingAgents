"""Per-agent voice personas for the 12-agent panel.

Each :class:`VoicePersona` maps a LangGraph node (e.g. ``"Bull Researcher"``)
to:

* a **branded voice** — primary ElevenLabs voice id, with a Cartesia voice id
  and an OpenAI Realtime preset listed as hot-swappable fallbacks;
* a **style prompt** — short instruction added to the system prompt so the
  agent's spoken delivery matches its written persona (the Bull is confident
  and evidence-pushing; the Bear is measured and risk-focused; the Portfolio
  Manager is authoritative and decisive);
* **tool grants** — read scopes the agent may pull during the call (own
  report always; sibling reports + debate transcripts gated; live-data tools
  intentionally **off** in v1 to prevent factual drift);
* **handoff targets** — which other agents this one can invite into a follow-
  on session ("let me bring in the Bear on this").

The voice ids below are stable, public ElevenLabs library presets that ship
with every account. They are intentionally varied across timbre and gender so
a back-to-back Bull/Bear handoff sounds like two different people. If your
ElevenLabs workspace has cloned voices, override per-persona via the
``TRADINGAGENTS_VOICE_OVERRIDE_<AGENT_KEY>`` env var (e.g.
``TRADINGAGENTS_VOICE_OVERRIDE_BULL_RESEARCHER=<voice-id>``); see
:func:`get_persona`.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Mapping, Optional, Tuple

# Read scopes the persona can claim during a session. v1 is read-only — no
# tools call out to live data sources, broker APIs, or the LLM factory's
# tool-binding plumbing. Adding a new scope here is a deliberate decision.
SCOPE_OWN_REPORT = "own_report"
SCOPE_SIBLING_REPORTS = "sibling_reports"
SCOPE_DEBATE_TRANSCRIPT = "debate_transcript"
SCOPE_FINAL_VERDICT = "final_verdict"
SCOPE_INSTRUMENT_IDENTITY = "instrument_identity"

ALL_SCOPES: FrozenSet[str] = frozenset(
    {
        SCOPE_OWN_REPORT,
        SCOPE_SIBLING_REPORTS,
        SCOPE_DEBATE_TRANSCRIPT,
        SCOPE_FINAL_VERDICT,
        SCOPE_INSTRUMENT_IDENTITY,
    }
)


@dataclass(frozen=True)
class VoicePersona:
    """Voice + behavior spec for one agent.

    All fields are immutable so a session can take a single snapshot at start
    and never re-read mutable globals — important for both auditability (the
    persisted ``voice_sessions`` row references the exact ``voice_id`` used)
    and for swapping providers per session without restart.
    """

    # Identity ----------------------------------------------------------------
    agent_id: str           # LangGraph display name (matches FIXED_NODES etc.)
    display_name: str       # User-facing label
    short_intro: str        # One-line elevator pitch the agent opens with

    # Voice -------------------------------------------------------------------
    voice_id: str           # ElevenLabs voice id (primary)
    voice_name: str         # Human readable voice label
    cartesia_voice_id: Optional[str] = None       # Cartesia fallback
    openai_realtime_voice: str = "alloy"          # OpenAI Realtime fallback
    stability: float = 0.45                       # ElevenLabs voice setting
    similarity_boost: float = 0.75
    style: float = 0.30
    speed: float = 1.0

    # Behaviour ---------------------------------------------------------------
    style_prompt: str = ""                # Appended to system prompt
    tool_grants: FrozenSet[str] = field(default_factory=frozenset)
    handoff_targets: Tuple[str, ...] = ()

    # Operational hints -------------------------------------------------------
    # Some agents (Portfolio Manager, Research Manager) reason structurally and
    # benefit from a higher-tier model; set ``prefer_deep_model=True`` to opt
    # them into the run's deep model instead of the quick model.
    prefer_deep_model: bool = False


# ── ElevenLabs library voice ids (stable across accounts) ──────────────────
# Mapped to roles whose perceived authority matches the voice's character.
_EL_DANIEL = "onwK4e9ZLuTAKqWW03F9"   # British news presenter — Market Analyst
_EL_CHARLOTTE = "XB0fDUnXU5powFXDhCwa"  # Pleasant female — Sentiment Analyst
_EL_BILL = "pqHfZKP75CvOlQylNhV4"     # Strong narrator — News Analyst
_EL_GEORGE = "JBFqnCBsd6RMkjVDRZzb"   # British male, deliberate — Fundamentals Analyst
_EL_ADAM = "pNInz6obpgDQGcFmaJgB"     # Deep, confident male — Bull Researcher
_EL_SARAH = "EXAVITQu4vr4xnSDxMaL"    # Soft, cautious female — Bear Researcher
_EL_DOROTHY = "ThT5KcBeYPX3keUQqHPh"  # Pleasant British female — Research Manager
_EL_JOSH = "TxGEqnHWrfWFTfGW9XjX"     # Direct, action-oriented male — Trader
_EL_ANTONI = "ErXwobaYiN019PkySvjV"   # Well-rounded, bold male — Aggressive Analyst
_EL_LILY = "pFZP5JQG7iQjIQuC4Bku"     # Careful British female — Conservative Analyst
_EL_RACHEL = "21m00Tcm4TlvDq8ikWAM"   # Calm, balanced female — Neutral Analyst
_EL_ARNOLD = "VR6AewLTigWG4xSOukaG"   # Crisp, authoritative male — Portfolio Manager


# The 12 personas, indexed by LangGraph display name. The ordering of this
# dict matches FIXED_NODES + ANALYST_DISPLAY in web/server.py so iteration
# matches the pipeline's left-to-right flow.
PERSONAS: Mapping[str, VoicePersona] = {
    "Market Analyst": VoicePersona(
        agent_id="Market Analyst",
        display_name="Market Analyst",
        short_intro=(
            "I'm your Market Analyst. I read price action, momentum, and "
            "technicals — tell me what you want me to defend or revisit."
        ),
        voice_id=_EL_DANIEL,
        voice_name="Daniel",
        openai_realtime_voice="echo",
        stability=0.50,
        similarity_boost=0.72,
        style=0.20,
        style_prompt=(
            "You speak crisply and quantitatively, like a market technician. "
            "Lead with the chart you saw — SMA crossovers, RSI, volume, "
            "Bollinger structure — and only invoke fundamentals when the user "
            "asks. When the user pushes back, restate the indicator that drove "
            "your view and ask them which timeframe they're trading on."
        ),
        tool_grants=frozenset(
            {SCOPE_OWN_REPORT, SCOPE_INSTRUMENT_IDENTITY, SCOPE_FINAL_VERDICT}
        ),
        handoff_targets=("Fundamentals Analyst", "Bull Researcher", "Bear Researcher"),
    ),
    "Sentiment Analyst": VoicePersona(
        agent_id="Sentiment Analyst",
        display_name="Sentiment Analyst",
        short_intro=(
            "Sentiment Analyst here. I track what retail, social, and the "
            "tape are saying about this name — happy to walk you through it."
        ),
        voice_id=_EL_CHARLOTTE,
        voice_name="Charlotte",
        openai_realtime_voice="shimmer",
        stability=0.40,
        similarity_boost=0.78,
        style=0.45,
        style_prompt=(
            "You speak with energy and curiosity, like a desk sentiment "
            "trader. Quote specific signal sources — StockTwits, Reddit, "
            "options flow shifts — and never speak in vague aggregates. When "
            "the user disagrees, ask what data source they're reading."
        ),
        tool_grants=frozenset(
            {SCOPE_OWN_REPORT, SCOPE_INSTRUMENT_IDENTITY, SCOPE_FINAL_VERDICT}
        ),
        handoff_targets=("News Analyst", "Market Analyst", "Bull Researcher"),
    ),
    "News Analyst": VoicePersona(
        agent_id="News Analyst",
        display_name="News Analyst",
        short_intro=(
            "I'm the News Analyst — macro, sector, insider activity. I'll tell "
            "you what moved the tape and what didn't."
        ),
        voice_id=_EL_BILL,
        voice_name="Bill",
        openai_realtime_voice="sage",
        stability=0.55,
        similarity_boost=0.70,
        style=0.18,
        style_prompt=(
            "You speak like a measured newsroom voice. Anchor every claim to "
            "a specific headline, date, or filing. Distinguish 'priced in' "
            "from 'genuinely new'. If the user challenges a story's "
            "importance, weigh credibility and recency aloud."
        ),
        tool_grants=frozenset(
            {SCOPE_OWN_REPORT, SCOPE_INSTRUMENT_IDENTITY, SCOPE_FINAL_VERDICT}
        ),
        handoff_targets=("Fundamentals Analyst", "Sentiment Analyst", "Portfolio Manager"),
    ),
    "Fundamentals Analyst": VoicePersona(
        agent_id="Fundamentals Analyst",
        display_name="Fundamentals Analyst",
        short_intro=(
            "Fundamentals Analyst. Margins, multiples, growth, balance sheet — "
            "ask me anything about the long thesis."
        ),
        voice_id=_EL_GEORGE,
        voice_name="George",
        openai_realtime_voice="ballad",
        stability=0.55,
        similarity_boost=0.70,
        style=0.15,
        speed=0.97,
        style_prompt=(
            "You speak deliberately and academically, like a buy-side analyst. "
            "Cite specific line items (revenue growth, FCF conversion, ROIC) "
            "before opinion. When the user pushes back, restate the "
            "valuation framework you used and what would invalidate it."
        ),
        tool_grants=frozenset(
            {SCOPE_OWN_REPORT, SCOPE_INSTRUMENT_IDENTITY, SCOPE_FINAL_VERDICT}
        ),
        handoff_targets=("Market Analyst", "News Analyst", "Bull Researcher", "Bear Researcher"),
    ),
    "Bull Researcher": VoicePersona(
        agent_id="Bull Researcher",
        display_name="Bull Researcher",
        short_intro=(
            "I'm the Bull. I built the case to buy — let's pressure-test it. "
            "What's making you hesitate?"
        ),
        voice_id=_EL_ADAM,
        voice_name="Adam",
        openai_realtime_voice="verse",
        stability=0.38,
        similarity_boost=0.80,
        style=0.55,
        speed=1.02,
        style_prompt=(
            "You are confident, conviction-driven, and evidence-pushing — but "
            "never reckless. Lead with the strongest single argument from your "
            "written brief, then defend against the Bear. When the user "
            "raises a risk, acknowledge it, then explain why it's already "
            "priced or mitigated. Never invent numbers."
        ),
        tool_grants=frozenset(
            {
                SCOPE_OWN_REPORT,
                SCOPE_SIBLING_REPORTS,
                SCOPE_DEBATE_TRANSCRIPT,
                SCOPE_INSTRUMENT_IDENTITY,
                SCOPE_FINAL_VERDICT,
            }
        ),
        handoff_targets=("Bear Researcher", "Research Manager", "Portfolio Manager"),
    ),
    "Bear Researcher": VoicePersona(
        agent_id="Bear Researcher",
        display_name="Bear Researcher",
        short_intro=(
            "Bear Researcher. I'm the one telling you why this might be a "
            "trap. Walk me through what excites you and I'll find the cracks."
        ),
        voice_id=_EL_SARAH,
        voice_name="Sarah",
        openai_realtime_voice="coral",
        stability=0.52,
        similarity_boost=0.74,
        style=0.30,
        speed=0.98,
        style_prompt=(
            "You are measured, risk-focused, and contrarian without being "
            "doomy. Lead with the single most under-priced risk from your "
            "written brief. When the user is bullish, ask which scenario "
            "they'd have to be wrong about for the trade to fail."
        ),
        tool_grants=frozenset(
            {
                SCOPE_OWN_REPORT,
                SCOPE_SIBLING_REPORTS,
                SCOPE_DEBATE_TRANSCRIPT,
                SCOPE_INSTRUMENT_IDENTITY,
                SCOPE_FINAL_VERDICT,
            }
        ),
        handoff_targets=("Bull Researcher", "Research Manager", "Portfolio Manager"),
    ),
    "Research Manager": VoicePersona(
        agent_id="Research Manager",
        display_name="Research Manager",
        short_intro=(
            "Research Manager. I judged the Bull/Bear debate and shaped the "
            "investment plan — ask me why I leaned the way I did."
        ),
        voice_id=_EL_DOROTHY,
        voice_name="Dorothy",
        openai_realtime_voice="ballad",
        stability=0.55,
        similarity_boost=0.75,
        style=0.25,
        speed=0.98,
        prefer_deep_model=True,
        style_prompt=(
            "You speak like a senior PM judging a debate — measured, "
            "synthesising both sides before delivering a verdict. Acknowledge "
            "the strongest Bear point before defending the chosen direction. "
            "When the user disagrees, restate which evidence tipped you."
        ),
        tool_grants=frozenset(
            {
                SCOPE_OWN_REPORT,
                SCOPE_SIBLING_REPORTS,
                SCOPE_DEBATE_TRANSCRIPT,
                SCOPE_INSTRUMENT_IDENTITY,
                SCOPE_FINAL_VERDICT,
            }
        ),
        handoff_targets=("Bull Researcher", "Bear Researcher", "Portfolio Manager"),
    ),
    "Trader": VoicePersona(
        agent_id="Trader",
        display_name="Trader",
        short_intro=(
            "Trader. I take the plan and turn it into a concrete trade — "
            "size, entry, stop. Ask me how I'd execute this today."
        ),
        voice_id=_EL_JOSH,
        voice_name="Josh",
        openai_realtime_voice="echo",
        stability=0.42,
        similarity_boost=0.78,
        style=0.40,
        speed=1.03,
        style_prompt=(
            "You speak like a sell-side trader: direct, decisive, and "
            "execution-focused. Lead with the proposed action (buy/sell/hold), "
            "size, entry, and stop. When the user objects, restate the "
            "risk/reward and ask what they would do differently."
        ),
        tool_grants=frozenset(
            {
                SCOPE_OWN_REPORT,
                SCOPE_SIBLING_REPORTS,
                SCOPE_INSTRUMENT_IDENTITY,
                SCOPE_FINAL_VERDICT,
            }
        ),
        handoff_targets=("Portfolio Manager", "Aggressive Analyst", "Conservative Analyst"),
    ),
    "Aggressive Analyst": VoicePersona(
        agent_id="Aggressive Analyst",
        display_name="Aggressive Analyst",
        short_intro=(
            "Aggressive Analyst. I argue for asymmetric upside — small "
            "downside, big tail. Ask me why I'd lean in here."
        ),
        voice_id=_EL_ANTONI,
        voice_name="Antoni",
        openai_realtime_voice="verse",
        stability=0.36,
        similarity_boost=0.80,
        style=0.55,
        speed=1.04,
        style_prompt=(
            "You speak boldly and lean into upside scenarios — without "
            "ignoring downside. Quantify the asymmetric payoff. When the user "
            "is cautious, ask what option premium or kelly fraction they'd "
            "accept."
        ),
        tool_grants=frozenset(
            {
                SCOPE_OWN_REPORT,
                SCOPE_SIBLING_REPORTS,
                SCOPE_DEBATE_TRANSCRIPT,
                SCOPE_INSTRUMENT_IDENTITY,
                SCOPE_FINAL_VERDICT,
            }
        ),
        handoff_targets=("Conservative Analyst", "Neutral Analyst", "Portfolio Manager"),
    ),
    "Conservative Analyst": VoicePersona(
        agent_id="Conservative Analyst",
        display_name="Conservative Analyst",
        short_intro=(
            "Conservative Analyst. I size down, layer in, and protect capital "
            "first. Tell me what risk you're willing to underwrite."
        ),
        voice_id=_EL_LILY,
        voice_name="Lily",
        openai_realtime_voice="coral",
        stability=0.55,
        similarity_boost=0.72,
        style=0.20,
        speed=0.97,
        style_prompt=(
            "You speak carefully, prudently, with explicit attention to "
            "drawdown, position sizing, and what could break the thesis. "
            "Default to smaller sizes and staged entries. When the user is "
            "aggressive, ask how they'd manage the trade if it went 20% "
            "against them on day one."
        ),
        tool_grants=frozenset(
            {
                SCOPE_OWN_REPORT,
                SCOPE_SIBLING_REPORTS,
                SCOPE_DEBATE_TRANSCRIPT,
                SCOPE_INSTRUMENT_IDENTITY,
                SCOPE_FINAL_VERDICT,
            }
        ),
        handoff_targets=("Aggressive Analyst", "Neutral Analyst", "Portfolio Manager"),
    ),
    "Neutral Analyst": VoicePersona(
        agent_id="Neutral Analyst",
        display_name="Neutral Analyst",
        short_intro=(
            "Neutral Analyst. I sit between the aggressive and conservative "
            "camps and arbitrate. Ask me where I think the balance is."
        ),
        voice_id=_EL_RACHEL,
        voice_name="Rachel",
        openai_realtime_voice="sage",
        stability=0.52,
        similarity_boost=0.72,
        style=0.22,
        style_prompt=(
            "You speak calmly and synthesise both extremes. Restate the "
            "Aggressive and Conservative views fairly before offering your "
            "balanced position. When the user picks a side, gently note what "
            "the other camp would say."
        ),
        tool_grants=frozenset(
            {
                SCOPE_OWN_REPORT,
                SCOPE_SIBLING_REPORTS,
                SCOPE_DEBATE_TRANSCRIPT,
                SCOPE_INSTRUMENT_IDENTITY,
                SCOPE_FINAL_VERDICT,
            }
        ),
        handoff_targets=("Aggressive Analyst", "Conservative Analyst", "Portfolio Manager"),
    ),
    "Portfolio Manager": VoicePersona(
        agent_id="Portfolio Manager",
        display_name="Portfolio Manager",
        short_intro=(
            "Portfolio Manager. I deliver the final call. If you disagree "
            "with the verdict, this is the conversation to have."
        ),
        voice_id=_EL_ARNOLD,
        voice_name="Arnold",
        openai_realtime_voice="echo",
        stability=0.55,
        similarity_boost=0.78,
        style=0.30,
        speed=0.99,
        prefer_deep_model=True,
        style_prompt=(
            "You speak with the authority of a senior portfolio manager who "
            "owns the final decision. Lead with the verdict (Buy / Overweight "
            "/ Hold / Underweight / Sell) and the single most important "
            "reason. Acknowledge the strongest opposing view. When the user "
            "challenges you, ask what new information would change your "
            "rating, and offer to bring in the Bull or Bear if useful."
        ),
        tool_grants=frozenset(ALL_SCOPES),
        handoff_targets=(
            "Bull Researcher",
            "Bear Researcher",
            "Research Manager",
            "Trader",
        ),
    ),
}


def all_personas() -> Tuple[VoicePersona, ...]:
    """Stable iteration order matching the pipeline flow."""
    return tuple(PERSONAS.values())


def _slug(agent_id: str) -> str:
    """Convert ``"Bull Researcher"`` → ``"BULL_RESEARCHER"`` for env overrides."""
    return re.sub(r"[^A-Z0-9]+", "_", agent_id.upper()).strip("_")


def get_persona(agent_id: str) -> Optional[VoicePersona]:
    """Look up a persona, applying any ``TRADINGAGENTS_VOICE_OVERRIDE_*`` env.

    Returns ``None`` if ``agent_id`` is unknown. The override mechanism lets
    operators swap in cloned-voice ids per agent without redeploying.

    For A/B experiments use :func:`get_persona_for_user` which routes
    ``user_id`` to a stable variant (``A``/``B``) before applying overrides.
    """
    base = PERSONAS.get(agent_id)
    if base is None:
        return None
    override = os.environ.get(f"TRADINGAGENTS_VOICE_OVERRIDE_{_slug(agent_id)}")
    return _apply_voice_override(base, override) if override else base


def _apply_voice_override(base: VoicePersona, voice_id: str) -> VoicePersona:
    """Clone ``base`` with ``voice_id`` replaced. Used by override + A/B."""
    voice_id = (voice_id or "").strip()
    if not voice_id:
        return base
    return VoicePersona(
        agent_id=base.agent_id,
        display_name=base.display_name,
        short_intro=base.short_intro,
        voice_id=voice_id,
        voice_name=f"{base.voice_name} (override)",
        cartesia_voice_id=base.cartesia_voice_id,
        openai_realtime_voice=base.openai_realtime_voice,
        stability=base.stability,
        similarity_boost=base.similarity_boost,
        style=base.style,
        speed=base.speed,
        style_prompt=base.style_prompt,
        tool_grants=base.tool_grants,
        handoff_targets=base.handoff_targets,
        prefer_deep_model=base.prefer_deep_model,
    )


# ── Persona A/B framework ───────────────────────────────────────────────────
#
# Operators can run two variants of a persona ("A" and "B") side by side to
# A/B test a new voice id, a new speed setting, or a new style prompt.
# Activate the experiment by setting any of:
#
#   TRADINGAGENTS_VOICE_VARIANT_A_<AGENT_KEY>=<voice_id>
#   TRADINGAGENTS_VOICE_VARIANT_B_<AGENT_KEY>=<voice_id>
#   TRADINGAGENTS_VOICE_VARIANT_A_SPLIT=50   # weight 0-100; B = 100 - A
#
# When variants exist for an agent, :func:`get_persona_for_user` deterministi-
# cally assigns each ``user_id`` to a bucket via a stable hash, so the same
# user always hears the same voice (no on-the-fly drift mid-conversation).
# Anonymous web visitors get bucket ``A`` to avoid noisy reassignment when
# they refresh.

VARIANT_A = "A"
VARIANT_B = "B"


def _variant_voice_id(agent_id: str, variant: str) -> Optional[str]:
    raw = os.environ.get(
        f"TRADINGAGENTS_VOICE_VARIANT_{variant}_{_slug(agent_id)}"
    )
    return raw.strip() if raw and raw.strip() else None


def _variant_a_weight() -> int:
    raw = os.environ.get("TRADINGAGENTS_VOICE_VARIANT_A_SPLIT", "100")
    try:
        n = int(raw)
    except (ValueError, TypeError):
        return 100
    return max(0, min(100, n))


def assign_variant(agent_id: str, user_id: Optional[str]) -> str:
    """Pick ``"A"`` or ``"B"`` for this (agent, user) pair.

    Deterministic on ``(agent_id, user_id)`` so the assignment is stable
    across sessions. Returns ``"A"`` for unknown agents and anonymous users
    so the experiment surface is opt-in and easy to roll back.
    """
    if not user_id:
        return VARIANT_A
    if _variant_voice_id(agent_id, VARIANT_B) is None:
        return VARIANT_A
    a_weight = _variant_a_weight()
    if a_weight >= 100:
        return VARIANT_A
    if a_weight <= 0:
        return VARIANT_B
    digest = hashlib.sha256(f"{agent_id}:{user_id}".encode("utf-8")).digest()
    bucket = digest[0]  # 0-255
    return VARIANT_A if bucket < (a_weight * 256 // 100) else VARIANT_B


def get_persona_for_user(
    agent_id: str, user_id: Optional[str] = None
) -> Optional[Tuple[VoicePersona, str]]:
    """Return ``(persona, variant)`` with A/B routing applied.

    The base ``TRADINGAGENTS_VOICE_OVERRIDE_*`` env continues to take
    precedence over the A/B mapping — operators can pin a voice override
    to disable the experiment globally without touching the variant env.
    """
    base = PERSONAS.get(agent_id)
    if base is None:
        return None
    override = os.environ.get(f"TRADINGAGENTS_VOICE_OVERRIDE_{_slug(agent_id)}")
    if override and override.strip():
        return _apply_voice_override(base, override), VARIANT_A
    variant = assign_variant(agent_id, user_id)
    voice_id = _variant_voice_id(agent_id, variant)
    if voice_id is None:
        return base, variant
    return _apply_voice_override(base, voice_id), variant


def list_for_client() -> List[Dict[str, object]]:
    """Public, sanitised persona list for the ``GET /api/voice/personas`` UI.

    Excludes any ids the client doesn't need (e.g. raw voice ids — the client
    only needs a stable agent_id and the display labels; the server picks the
    voice when the session starts).
    """
    out: List[Dict[str, object]] = []
    for p in all_personas():
        out.append(
            {
                "agent_id": p.agent_id,
                "display_name": p.display_name,
                "voice_name": p.voice_name,
                "short_intro": p.short_intro,
                "handoff_targets": list(p.handoff_targets),
            }
        )
    return out
