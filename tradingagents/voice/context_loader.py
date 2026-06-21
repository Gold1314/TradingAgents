"""Load a completed run's structured context for a voice session.

The voice agent's "knowledge" during a call is intentionally bounded to a
snapshot of the run that already finished: the agent's own report (always),
its siblings' reports (if the persona's ``tool_grants`` allow), the debate
transcripts (Bull/Bear and Risk trio), the final verdict, and the resolved
instrument identity. We do not call any live-data tools during the call —
this keeps the agent factual and prevents hallucinated "current price" claims.

Persistence model: we read the same Supabase tables the web/mobile pipeline
writes to (``runs``, ``agent_outputs`` — see :mod:`web.db`). When Supabase is
unconfigured (the dev fallback), we accept an in-memory ``Run`` object from
the ``web.server.RunManager`` for the same data, so the worker boots in tests
without a DB. Both paths produce the same :class:`RunContext`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

from tradingagents.voice.personas import (
    SCOPE_DEBATE_TRANSCRIPT,
    SCOPE_FINAL_VERDICT,
    SCOPE_INSTRUMENT_IDENTITY,
    SCOPE_OWN_REPORT,
    SCOPE_SIBLING_REPORTS,
    VoicePersona,
)

logger = logging.getLogger("tradingagents.voice.context_loader")


@dataclass(frozen=True)
class AgentReport:
    """One agent's textual output from a finished run."""

    agent: str
    content: str
    seq: int = 0


@dataclass(frozen=True)
class RunContext:
    """Everything a voice session needs to reason about its run."""

    run_id: str
    ticker: str
    trade_date: str
    asset_type: str
    decision: Optional[str]          # 5-tier verdict ("Buy" / "Hold" / ...)
    final_rationale: Optional[str]   # Portfolio Manager's markdown rationale
    identity: Optional[str]          # Resolved instrument identity block
    provider: Optional[str]
    deep_model: Optional[str]
    quick_model: Optional[str]
    agents: Dict[str, AgentReport] = field(default_factory=dict)

    # Convenience accessors --------------------------------------------------
    def report_for(self, agent_id: str) -> Optional[str]:
        rec = self.agents.get(agent_id)
        return rec.content if rec else None

    def siblings_of(self, agent_id: str) -> List[AgentReport]:
        """All reports other than ``agent_id``'s own."""
        return [r for k, r in sorted(self.agents.items(), key=_seq_key) if k != agent_id]


def _seq_key(item: Any) -> Any:
    _name, rec = item
    return (rec.seq, rec.agent)


# ── In-process loader (read RunManager state) ───────────────────────────────


def load_from_run_manager(run_id: str, manager: Any) -> Optional[RunContext]:
    """Build a :class:`RunContext` from an in-memory ``Run`` (no Supabase).

    Used by tests, local dev, and the immediate post-run path where the
    Supabase row may not have been written yet (``store_run`` runs *after*
    the SSE ``final`` event). Walks the run's event log to recover the same
    fields we'd otherwise read from the DB.
    """
    run = manager.get(run_id) if manager is not None else None
    if run is None:
        return None

    ticker = ""
    trade_date = ""
    asset_type = "stock"
    decision: Optional[str] = None
    final_rationale: Optional[str] = None
    identity: Optional[str] = None
    agents: Dict[str, AgentReport] = {}

    # The run.analytics dict typically carries ticker/user_id when present
    # (set by web/mobile/router.py); fall back to scanning events otherwise.
    if isinstance(getattr(run, "analytics", None), dict):
        ticker = run.analytics.get("ticker") or ticker
        trade_date = run.analytics.get("trade_date") or trade_date
        asset_type = run.analytics.get("asset_type") or asset_type

    seq = 0
    for event in run.event_log:
        if not isinstance(event, dict):
            continue
        et = event.get("type")
        if et == "identity":
            content = event.get("content")
            if isinstance(content, str):
                identity = content
        elif et == "final":
            d = event.get("decision")
            c = event.get("content")
            if isinstance(d, str):
                decision = d
            if isinstance(c, str):
                final_rationale = c
        elif et == "agent" and event.get("status") == "done":
            name = event.get("node")
            content = event.get("content") or ""
            if isinstance(name, str) and isinstance(content, str) and content.strip():
                agents[name] = AgentReport(agent=name, content=content, seq=seq)
                seq += 1

    if not ticker:
        return None

    return RunContext(
        run_id=run_id,
        ticker=ticker,
        trade_date=trade_date,
        asset_type=asset_type,
        decision=decision,
        final_rationale=final_rationale,
        identity=identity,
        provider=None,
        deep_model=None,
        quick_model=None,
        agents=agents,
    )


# ── Supabase loader ─────────────────────────────────────────────────────────


def load_from_supabase(run_id: str) -> Optional[RunContext]:
    """Build a :class:`RunContext` from Supabase. Returns ``None`` on miss/error.

    Fail-open like :mod:`web.db`: any DB error degrades to "no context" rather
    than failing the voice session. Callers should fall back to
    :func:`load_from_run_manager` for very recent runs.
    """
    try:
        from web import db  # local import to keep this module importable in tests
    except ImportError:
        return None

    client = db._get_client() if hasattr(db, "_get_client") else None
    if client is None:
        return None
    try:
        res = (
            client.table("runs")
            .select("*")
            .eq("id", run_id)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        if not rows:
            return None
        row = rows[0]
        agents_res = (
            client.table("agent_outputs")
            .select("seq,agent,content")
            .eq("run_id", run_id)
            .order("seq")
            .execute()
        )
        agents: Dict[str, AgentReport] = {}
        for a in agents_res.data or []:
            name = a.get("agent")
            content = a.get("content") or ""
            if not isinstance(name, str) or not content.strip():
                continue
            agents[name] = AgentReport(
                agent=name, content=content, seq=int(a.get("seq", 0) or 0)
            )
        return RunContext(
            run_id=run_id,
            ticker=row.get("ticker") or "",
            trade_date=str(row.get("trade_date") or ""),
            asset_type=row.get("asset_type") or "stock",
            decision=row.get("decision"),
            final_rationale=row.get("final_content"),
            identity=row.get("identity"),
            provider=row.get("provider"),
            deep_model=row.get("deep_model"),
            quick_model=row.get("quick_model"),
            agents=agents,
        )
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning("voice context load (run_id=%s) failed: %s", run_id, exc)
        return None


def load_run_context(run_id: str, manager: Any = None) -> Optional[RunContext]:
    """Try Supabase first, then the in-memory run manager. Returns ``None`` if both miss."""
    ctx = load_from_supabase(run_id)
    if ctx is not None:
        return ctx
    return load_from_run_manager(run_id, manager)


# ── Persona-scoped prompt assembly ──────────────────────────────────────────


def _truncate(text: str, max_chars: int) -> str:
    text = text or ""
    if len(text) <= max_chars:
        return text
    head = text[: max_chars - 80].rstrip()
    return f"{head}\n\n[…truncated for the voice call. The user can scroll the full report on screen.]"


# Per-section character budgets. The voice agent's context window is tight
# both for latency and for keeping the model focused on a single agent's
# voice. We bias the budget toward the agent's own report and a digest of
# siblings rather than full sibling text.
OWN_REPORT_BUDGET = 8000
SIBLING_REPORT_BUDGET_EACH = 1500
DEBATE_BUDGET = 4000
FINAL_BUDGET = 4000


def build_system_prompt(
    persona: VoicePersona,
    ctx: RunContext,
    extra_user_facts: Optional[Mapping[str, str]] = None,
) -> str:
    """Compose the voice agent's system prompt for one call.

    The prompt has four parts:

    1. **Role & style** — who the agent is and how it speaks.
    2. **Scope rules** — what the agent can and cannot say.
    3. **Bounded context** — the run's data, gated by the persona's
       ``tool_grants``.
    4. **Conversation handling** — opener line, how to handle pushback,
       how to invite a handoff.

    Returns a single string the LiveKit Agents worker passes as the assistant's
    system message. Length-capped per section so a long debate transcript
    never blows out the LLM's context window.
    """
    grants = persona.tool_grants
    parts: List[str] = []

    parts.append(
        f"You are the **{persona.display_name}** on a real-time voice call with "
        f"the user about {ctx.ticker} (trade date {ctx.trade_date}, "
        f"asset type {ctx.asset_type}). Your role and written analysis just "
        "finished and is on screen. The user has now tapped your card to talk "
        "to *you specifically*."
    )

    if persona.style_prompt:
        parts.append("STYLE & VOICE:\n" + persona.style_prompt)

    parts.append(
        "\n".join(
            [
                "GROUND RULES:",
                "- Speak in short, conversational turns — not paragraphs.",
                "- Only assert facts that appear in your bounded context below. "
                "Do not invent numbers, dates, or price levels.",
                "- If you do not know something, say so and offer to bring in "
                "another agent who would (see HANDOFFS).",
                "- The user already has the chart and your full written report "
                "on screen. Avoid re-reading the report verbatim; reference "
                "specific points instead.",
                "- Never pretend to place an order or query a live broker. You "
                "are explaining your reasoning, not executing trades.",
            ]
        )
    )

    if SCOPE_INSTRUMENT_IDENTITY in grants and ctx.identity:
        parts.append("INSTRUMENT IDENTITY:\n" + _truncate(ctx.identity, 1500))

    if SCOPE_OWN_REPORT in grants:
        own = ctx.report_for(persona.agent_id)
        if own:
            parts.append(
                f"YOUR OWN WRITTEN REPORT (always cite this first):\n"
                + _truncate(own, OWN_REPORT_BUDGET)
            )
        else:
            parts.append(
                "YOUR OWN WRITTEN REPORT: (not available — you may explain "
                "your role conceptually, but flag that the run's stored "
                "report is missing.)"
            )

    if SCOPE_SIBLING_REPORTS in grants:
        siblings = ctx.siblings_of(persona.agent_id)
        if siblings:
            chunks = ["SIBLING AGENT REPORTS (read-only summaries):"]
            for r in siblings:
                chunks.append(
                    f"\n— {r.agent} —\n" + _truncate(r.content, SIBLING_REPORT_BUDGET_EACH)
                )
            parts.append("\n".join(chunks))

    if SCOPE_DEBATE_TRANSCRIPT in grants:
        # We reconstruct a "debate transcript" by concatenating Bull/Bear and
        # the Risk trio's reports in pipeline order — that mirrors the live
        # debate ordering since each entry is one full turn.
        debate_agents = [
            "Bull Researcher",
            "Bear Researcher",
            "Aggressive Analyst",
            "Conservative Analyst",
            "Neutral Analyst",
        ]
        chunks: List[str] = []
        for name in debate_agents:
            if name == persona.agent_id:
                continue
            rec = ctx.agents.get(name)
            if rec and rec.content.strip():
                chunks.append(f"--- {rec.agent} ---\n{rec.content}")
        if chunks:
            parts.append(
                "DEBATE TRANSCRIPT (each agent's full turn):\n"
                + _truncate("\n\n".join(chunks), DEBATE_BUDGET)
            )

    if SCOPE_FINAL_VERDICT in grants:
        if ctx.decision or ctx.final_rationale:
            parts.append(
                f"FINAL VERDICT (Portfolio Manager): {ctx.decision or 'unspecified'}\n\n"
                + _truncate(ctx.final_rationale or "", FINAL_BUDGET)
            )

    if persona.handoff_targets:
        parts.append(
            "HANDOFFS: when the user wants a different perspective, you may "
            "offer to bring in one of these agents by name: "
            + ", ".join(persona.handoff_targets)
            + ". When you do, end the turn with a clear phrase like 'I can "
            "bring in the <agent>' — the app will surface a handoff button."
        )

    if extra_user_facts:
        formatted = "\n".join(f"- {k}: {v}" for k, v in extra_user_facts.items() if v)
        if formatted:
            parts.append("USER CONTEXT:\n" + formatted)

    parts.append(
        f"OPENER: when the call starts, greet the user briefly and say one "
        f"sentence: \"{persona.short_intro}\""
    )

    return "\n\n".join(parts)
