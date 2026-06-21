"""Portfolio Manager voice "reconcile" — one-shot rationale update.

When the user objects to the verdict during a Portfolio Manager voice call
(detected by :func:`tradingagents.voice.handoff.detect_reconcile_request`),
the client posts to ``POST /api/voice/sessions/{id}/reconcile`` with the
objection. We don't re-run the full LangGraph pipeline — that would burn
~$30 of tokens per challenge. Instead we make a *focused* call to the
project's deep LLM (via :mod:`tradingagents.llm_clients.factory`) that:

* takes the run's final rationale + decision + the user's objection,
* either reaffirms the rating with an explicit answer to the objection, OR
* flips the rating with a clear "what changed" statement,

and returns the updated markdown + the (possibly new) decision.

The result is persisted as a ``voice_turn`` with ``role="reconcile"`` so the
UI can replay the exchange like any other turn, and it's small enough to
inline in the response body so the client renders the update immediately.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from tradingagents.agents.utils.rating import parse_rating
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.llm_clients.factory import create_llm_client
from tradingagents.voice.context_loader import (
    RunContext,
    load_run_context,
)
from web.voice import db as voice_db

logger = logging.getLogger("tradingagents.voice.reconcile")


_RECONCILE_TEMPLATE = """You are the Portfolio Manager who just delivered a written verdict
on the analysis of {ticker} (trade date {trade_date}). The user is now
challenging your rating in a real-time voice call. Your job is to *reconcile*:
either reaffirm the rating with a sharp answer to the objection, or flip it
with a clear explanation of what changed.

Constraints:
- Do NOT invent new data. Use only the rationale you already wrote plus
  the user's stated objection.
- The output is a short markdown response (200-450 words) — this is a
  follow-up, not a fresh analysis.
- End with one of: "Updated rating: Buy", "Updated rating: Overweight",
  "Updated rating: Hold", "Updated rating: Underweight",
  "Updated rating: Sell" — pick the same rating you had before if you are
  not flipping, otherwise pick the new one.

Your original verdict: **{decision}**

Your original written rationale (the same one on screen now):
---
{rationale}
---

The user's objection (just spoken on the call):
"{objection}"

Reconcile the objection in 200-450 words of markdown, then end with the
"Updated rating:" line as instructed.
"""


def _build_llm(persona_provider: Optional[str], persona_model: Optional[str]) -> Any:
    """Pick the LLM client. Defaults to the project's configured deep model.

    The voice reconcile path benefits from a *reasoning* model, not the
    voice-LLM low-latency chat model — the user is willing to wait 3-5s
    here for a substantive update.
    """
    provider = persona_provider or DEFAULT_CONFIG.get("llm_provider") or "openai"
    model = persona_model or DEFAULT_CONFIG.get("deep_think_llm") or "gpt-5.5"
    return create_llm_client(provider=provider, model=model)


def run_reconcile(
    *,
    run_id: str,
    session_id: str,
    objection: str,
    manager: Any,
) -> Optional[Dict[str, Any]]:
    """Execute one reconcile pass. Returns the response payload or ``None``.

    ``None`` indicates a non-recoverable error (run context missing, LLM
    unavailable) — the caller surfaces this as a 503 / 404 as appropriate.
    """
    ctx: Optional[RunContext] = load_run_context(run_id, manager=manager)
    if ctx is None or not ctx.final_rationale:
        logger.warning(
            "reconcile %s: missing run context or rationale for run %s",
            session_id,
            run_id,
        )
        return None

    prompt = _RECONCILE_TEMPLATE.format(
        ticker=ctx.ticker,
        trade_date=ctx.trade_date,
        decision=ctx.decision or "unspecified",
        rationale=ctx.final_rationale.strip(),
        objection=objection.strip(),
    )

    try:
        llm = _build_llm(ctx.provider, ctx.deep_model)
        response = llm.invoke(prompt)
        text = _extract_text(response)
    except Exception as exc:  # noqa: BLE001 — caller maps to 503
        logger.exception("reconcile llm call failed: %s", exc)
        return None

    if not text or not text.strip():
        return None

    # Pass the original decision as the default so a model that omits the
    # required "Updated rating:" line is treated as "no change" rather than
    # silently downgraded to "Hold" (the parser's hard-coded default).
    new_rating = parse_rating(text, default=ctx.decision or "Hold")

    # Persist as a transcript turn for replay. Fail-open so a Supabase
    # outage never blocks the in-call rationale update. The next idx comes
    # from a query (not a hardcoded 10_000) so a second reconcile in the
    # same session doesn't collide on the (session_id, idx) unique index.
    try:
        idx = voice_db.next_turn_idx(session_id, default_start=10_000)
        voice_db.append_turn(
            session_id=session_id,
            idx=idx,
            role="reconcile",
            text=text,
            started_at=datetime.now(timezone.utc),
            ended_at=datetime.now(timezone.utc),
            provider=DEFAULT_CONFIG.get("llm_provider"),
            model=DEFAULT_CONFIG.get("deep_think_llm"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("reconcile persist failed: %s", exc)

    return {
        "session_id": session_id,
        "run_id": run_id,
        "original_decision": ctx.decision,
        "updated_decision": new_rating,
        "rationale_markdown": text,
        "flipped": (new_rating != ctx.decision) if (new_rating and ctx.decision) else False,
    }


def _extract_text(response: Any) -> str:
    """Best-effort extraction of the LLM's text reply.

    Supports both LangChain ``AIMessage``-style objects (``.content``) and
    plain-string responses returned by some provider clients.
    """
    if isinstance(response, str):
        return response
    content = getattr(response, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # Anthropic-style multi-part content.
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(parts)
    return str(response)
