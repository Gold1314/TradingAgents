"""Panel-of-agents orchestrator: prompt assembly + tolerant LLM output parser.

This is the brain of the "Voice Group Call" feature (Approach B in the design
plan). One LiveKit worker, one Deepgram STT, one Claude LLM hosts a panel of
2-3 personas; the TTS voice rotates per utterance so each persona speaks in
their own ElevenLabs voice. Free-debate UX — Claude decides which persona(s)
respond each turn, biased toward 1-2 voices per turn so we never stack three
sequential TTS calls on every reply.

This module is intentionally pure (no LiveKit, no async, no LangChain). Two
public functions:

* :func:`build_panel_system_prompt` — composes the Claude orchestrator prompt
  by merging each persona's ``style_prompt`` into a single "you are running a
  panel of N agents" preamble, then enumerating identity + voice for each, and
  explaining the structured JSON output contract.

* :func:`parse_orchestrator_output` — tolerant parser. Tries strict JSON first
  (the contract), falls back to a ``[Persona] ... [Persona] ...`` bracket-prose
  form (the real-world LLM-drift case), and finally returns ``[]`` for garbage
  the worker can downgrade to "no utterance this turn". Never raises — voice
  loops must not crash on a one-off model glitch.

The output is a flat list of :class:`PanelUtterance`, in spoken order. The
worker iterates and TTS-renders each utterance with the persona's voice. We
deliberately do NOT shuffle the order client-side; the orchestrator's emit
order IS the spoken order so the model can stage a "Bull → Bear" exchange
predictably.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Iterable, List, Mapping, Optional, Sequence

from tradingagents.voice.context_loader import (
    DEBATE_BUDGET,
    FINAL_BUDGET,
    OWN_REPORT_BUDGET,
    RunContext,
    SIBLING_REPORT_BUDGET_EACH,
)
from tradingagents.voice.personas import (
    SCOPE_DEBATE_TRANSCRIPT,
    SCOPE_FINAL_VERDICT,
    SCOPE_INSTRUMENT_IDENTITY,
    SCOPE_OWN_REPORT,
    SCOPE_SIBLING_REPORTS,
    VoicePersona,
)


def _truncate(text: str, max_chars: int) -> str:
    """Hard-cap a string at ``max_chars`` with a trailing "(truncated)" note.

    Duplicated rather than imported from :mod:`context_loader` because that
    module's version is a private helper; we keep our own copy so the
    contract stays under our control.
    """
    text = text or ""
    if len(text) <= max_chars:
        return text
    head = text[: max_chars - 80].rstrip()
    return f"{head}\n\n[…truncated for the voice call. The user can scroll the full report on screen.]"


@dataclass(frozen=True)
class PanelUtterance:
    """One scripted utterance: which persona speaks, and what they say.

    Frozen so a downstream TTS dispatcher can pass it around without worrying
    about mutation. ``persona_agent_id`` matches the LangGraph display name
    (e.g. ``"Bull Researcher"``) and is also the lookup key into the panel's
    persona map; the worker uses it to pick the correct ``voice_id``.
    """

    persona_agent_id: str
    text: str


# The orchestrator is asked to emit a JSON array. We document the EXACT shape
# here so the prompt assembly and the parser stay in lockstep — any change to
# this contract must update both ends in the same commit.
_OUTPUT_CONTRACT = (
    "Reply with a JSON array of objects, each with two keys:\n"
    '  - "persona": one of the EXACT agent_id strings listed below.\n'
    '  - "text": the spoken line for that persona, in plain prose (no markdown,'
    " no stage directions, no asterisks).\n"
    "Example: [\n"
    '  {"persona": "Bull Researcher", "text": "I still like this here — margins'
    ' expanded for the third straight quarter."},\n'
    '  {"persona": "Bear Researcher", "text": "Sure, but the multiple already'
    ' bakes that in."}\n'
    "]\n"
    "Output ONLY the JSON array. No prose around it. No code fences."
)


def build_panel_system_prompt(
    personas: Sequence[VoicePersona],
    ctx: RunContext,
    extra_user_facts: Optional[Mapping[str, str]] = None,
) -> str:
    """Compose the panel orchestrator system prompt.

    The structure mirrors :func:`context_loader.build_system_prompt` (so a
    single agent's prompt and a panel's prompt feel like siblings, not
    forks), with the multi-persona twist front-and-center:

    1. **Panel preamble** — who is on the call, the panel-size hard cap, and
       the orchestrator's job (decide who speaks, in what order).
    2. **Per-persona dossiers** — each persona's identity, voice name (so the
       LLM can mention them naturally), style prompt, and handoff targets.
    3. **Turn-taking rules** — bias toward 1-2 personas per turn; the third
       only chimes in when their viewpoint is materially different. Avoids
       stacking three sequential TTS calls per user utterance on most turns.
    4. **Bounded context** — same content the single-agent prompt has (the
       run's identity banner, the verdict, the debate transcripts), gated by
       the UNION of all selected personas' ``tool_grants`` so the panel
       never accidentally drops scope a member would have had solo.
    5. **Output contract** — the strict JSON array the parser expects.

    The union-of-grants choice matters: if a Bull (no DEBATE_TRANSCRIPT
    grant) shares the panel with the Research Manager (who has it), the
    panel transcript section is still included — the Manager will reference
    it and the Bull may want to push back on it.
    """
    if not personas:
        raise ValueError("build_panel_system_prompt requires at least one persona")

    parts: List[str] = []

    names = ", ".join(p.display_name for p in personas)
    parts.append(
        "You are the **orchestrator** of a real-time voice panel of "
        f"{len(personas)} agents: {names}. You're talking with the user about "
        f"{ctx.ticker} (trade date {ctx.trade_date}, asset type {ctx.asset_type}). "
        "Each agent's full written analysis is already on screen — the user "
        "tapped 'Group call' to hear you debate it live."
    )

    parts.append(
        "\n".join(
            [
                "YOUR JOB:",
                "- For every user turn, decide which persona(s) should speak and",
                "  emit their spoken lines in a STRICT JSON array (see OUTPUT",
                "  FORMAT below).",
                "- Stay in character for each persona — adopt their style prompt",
                "  faithfully. Do NOT narrate (\"Bull says...\"); just write the",
                "  spoken line in the \"text\" field.",
                "- Prefer 1-2 personas per turn. Only bring in a third when their",
                "  viewpoint is materially different (e.g. the Risk Manager when",
                "  Bull/Bear debate sizing). Avoid stacking three sequential",
                "  voices on every reply — it makes the call feel theatrical.",
                "- Keep each utterance short and conversational (one to three",
                "  sentences) — this is a voice call, not an essay.",
                "- Never invent numbers. Cite from the bounded context below.",
                "- Never speak for a persona who is not on this panel.",
            ]
        )
    )

    parts.append("PANEL ROSTER:")
    for idx, p in enumerate(personas, start=1):
        targets = (
            ", ".join(p.handoff_targets) if p.handoff_targets else "(none)"
        )
        parts.append(
            "\n".join(
                [
                    f"  [{idx}] {p.display_name}  (agent_id: \"{p.agent_id}\","
                    f" voice: {p.voice_name})",
                    f"      Style: {p.style_prompt.strip() or '(no style notes)'}",
                    f"      Allowed handoff targets: {targets}",
                ]
            )
        )

    union_grants = set()
    for p in personas:
        union_grants.update(p.tool_grants)

    if SCOPE_INSTRUMENT_IDENTITY in union_grants and ctx.identity:
        parts.append("INSTRUMENT IDENTITY:\n" + _truncate(ctx.identity, 1500))

    if SCOPE_OWN_REPORT in union_grants:
        own_chunks: List[str] = []
        for p in personas:
            report = ctx.report_for(p.agent_id)
            if report:
                own_chunks.append(
                    f"— {p.display_name} (written brief) —\n"
                    + _truncate(report, OWN_REPORT_BUDGET // max(1, len(personas)))
                )
        if own_chunks:
            parts.append(
                "PANELIST WRITTEN BRIEFS (the report each panelist filed before"
                " the call):\n" + "\n\n".join(own_chunks)
            )

    if SCOPE_SIBLING_REPORTS in union_grants:
        panel_ids = {p.agent_id for p in personas}
        siblings = [
            r
            for k, r in sorted(ctx.agents.items(), key=lambda kv: (kv[1].seq, kv[0]))
            if k not in panel_ids
        ]
        if siblings:
            sib_chunks = ["OTHER AGENT REPORTS (panelists may reference, but not impersonate):"]
            for r in siblings:
                sib_chunks.append(
                    f"\n— {r.agent} —\n"
                    + _truncate(r.content, SIBLING_REPORT_BUDGET_EACH)
                )
            parts.append("\n".join(sib_chunks))

    if SCOPE_DEBATE_TRANSCRIPT in union_grants:
        debate_agents = [
            "Bull Researcher",
            "Bear Researcher",
            "Aggressive Analyst",
            "Conservative Analyst",
            "Neutral Analyst",
        ]
        chunks: List[str] = []
        for name in debate_agents:
            rec = ctx.agents.get(name)
            if rec and rec.content.strip():
                chunks.append(f"--- {rec.agent} ---\n{rec.content}")
        if chunks:
            parts.append(
                "DEBATE TRANSCRIPT (each agent's full turn):\n"
                + _truncate("\n\n".join(chunks), DEBATE_BUDGET)
            )

    if SCOPE_FINAL_VERDICT in union_grants:
        if ctx.decision or ctx.final_rationale:
            parts.append(
                f"FINAL VERDICT (Portfolio Manager): {ctx.decision or 'unspecified'}\n\n"
                + _truncate(ctx.final_rationale or "", FINAL_BUDGET)
            )

    if extra_user_facts:
        formatted = "\n".join(
            f"- {k}: {v}" for k, v in extra_user_facts.items() if v
        )
        if formatted:
            parts.append("USER CONTEXT:\n" + formatted)

    parts.append("OUTPUT FORMAT:\n" + _OUTPUT_CONTRACT)

    opener_lines = [
        f"  - {p.display_name}: \"{p.short_intro}\""
        for p in personas
    ]
    parts.append(
        "OPENER: on the very first turn, have EACH panelist say their one-line"
        " intro in roster order. Use these exact lines:\n"
        + "\n".join(opener_lines)
    )

    return "\n\n".join(parts)


# ── Parser ──────────────────────────────────────────────────────────────────

# Strip ``” ``` ``json blocks the model sometimes wraps the output in even
# when we told it not to. We do this before the JSON parse because
# json.loads cannot handle ``` fences.
_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", flags=re.DOTALL | re.IGNORECASE)


def parse_orchestrator_output(
    raw: str,
    *,
    allowed_persona_ids: Optional[Iterable[str]] = None,
) -> List[PanelUtterance]:
    """Tolerant parser for the orchestrator's panel reply.

    Strategy:
    1. Strip Markdown code fences if present.
    2. Try strict JSON parse against the documented contract.
    3. Fall back to ``[Persona] ... [Persona] ...`` bracket-prose form. This
       happens often enough in practice (Claude sometimes thinks it's being
       "helpful" by switching to a more human-readable shape) that we treat
       it as a first-class supported form rather than an error.
    4. If neither works, return an empty list. The caller is expected to
       degrade gracefully — most worker code will treat an empty parse as
       "no panel utterances this turn" and skip TTS dispatch rather than
       crashing the call.

    ``allowed_persona_ids`` is optional; when supplied, any utterance whose
    persona is not in the set is dropped. This defends against the model
    fabricating a persona name (e.g. a hallucinated "Risk Officer" that isn't
    on the panel). When ``None``, all named personas are kept.

    Never raises. The contract is "return what you can parse, drop the rest."
    """
    if not raw:
        return []
    text = raw.strip()
    if not text:
        return []

    fence_match = _CODE_FENCE_RE.search(text)
    if fence_match:
        text = fence_match.group(1).strip()

    allowed = set(allowed_persona_ids) if allowed_persona_ids is not None else None

    utterances = _try_strict_json(text, allowed)
    if utterances is not None:
        return utterances

    utterances = _try_bracketed_prose(text, allowed)
    if utterances:
        return utterances

    return []


def _try_strict_json(
    text: str, allowed: Optional[set]
) -> Optional[List[PanelUtterance]]:
    """Parse strict JSON. Returns ``None`` if it isn't JSON at all (signalling
    the caller to try the bracket-prose fallback). Returns ``[]`` if it IS
    JSON but no utterances survive validation (the strict path was the right
    one — don't fall back through garbage).
    """
    candidate = text
    if not (candidate.startswith("[") or candidate.startswith("{")):
        first_bracket = candidate.find("[")
        if first_bracket < 0:
            return None
        candidate = candidate[first_bracket:]
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict):
        parsed = [parsed]
    if not isinstance(parsed, list):
        return None
    out: List[PanelUtterance] = []
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        persona = entry.get("persona") or entry.get("persona_agent_id")
        message = entry.get("text") or entry.get("message")
        if not isinstance(persona, str) or not isinstance(message, str):
            continue
        persona = persona.strip()
        message = message.strip()
        if not persona or not message:
            continue
        if allowed is not None and persona not in allowed:
            continue
        out.append(PanelUtterance(persona_agent_id=persona, text=message))
    return out


# Match ``[Persona Name] body text`` blocks. The body extends to the next
# ``[Persona ...]`` marker or the end of the string. Allows hyphens, ampersands,
# and parentheses in persona names — the LLM occasionally writes "[Bull (long)]"
# or "[Research Manager]" and we want both to parse cleanly.
_BRACKET_RE = re.compile(
    r"\[\s*([A-Za-z][A-Za-z0-9 &/\-()'.]{0,60})\s*\]\s*",
    flags=re.MULTILINE,
)


def _try_bracketed_prose(
    text: str, allowed: Optional[set]
) -> List[PanelUtterance]:
    """Parse ``[Bull] ... [Bear] ...`` style output.

    When ``allowed`` is supplied we resolve each bracket label to the best
    matching persona id (case-insensitive prefix or substring) so the LLM can
    write either ``[Bull]`` or ``[Bull Researcher]`` and both route correctly.
    """
    matches = list(_BRACKET_RE.finditer(text))
    if not matches:
        return []
    out: List[PanelUtterance] = []
    for i, m in enumerate(matches):
        label = m.group(1).strip()
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end].strip()
        if not body:
            continue
        resolved = _resolve_label(label, allowed)
        if resolved is None:
            continue
        out.append(PanelUtterance(persona_agent_id=resolved, text=body))
    return out


def _resolve_label(label: str, allowed: Optional[set]) -> Optional[str]:
    """Map a free-form bracket label to one of ``allowed`` persona ids.

    Rules (in order):
    1. Exact match (case-insensitive) → use it.
    2. The label is a substring of exactly one allowed id → use that one.
    3. An allowed id is a substring of the label → use the longest match.

    If ``allowed`` is ``None`` we trust the label verbatim (callers without a
    persona constraint accept whatever the model wrote).
    """
    if allowed is None:
        return label
    label_norm = label.strip().lower()
    if not label_norm:
        return None
    for cand in allowed:
        if cand.lower() == label_norm:
            return cand
    substring_hits = [c for c in allowed if label_norm in c.lower()]
    if len(substring_hits) == 1:
        return substring_hits[0]
    contains_hits = sorted(
        (c for c in allowed if c.lower() in label_norm),
        key=len,
        reverse=True,
    )
    if contains_hits:
        return contains_hits[0]
    return None
