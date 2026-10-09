"""Shadow-mode Jev assessment of the Portfolio Manager's decision.

Jev (TypeSafe AI) is a decision model: it takes a ``state`` plus typed
questions and returns calibrated probabilities instead of text. Here it reads
the run's structured artifacts and records (a) a probability per 5-tier rating
and (b) the probability that the evidence is sufficient to act. The result is
stored alongside the run for later calibration against realised alpha; it
never changes the rating or any order.

Enabled only when ``JEV_API_KEY`` is set. Any failure returns ``None``.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Mapping, Optional

import requests

from tradingagents.agents.utils.rating import RATINGS_5_TIER

logger = logging.getLogger(__name__)

_DEFAULT_URL = "https://api.typesafe.ai/v1/systemone"
_DEFAULT_MODEL = "typesafe/jev-1.13"
# shortcut: char cap approximates Jev's ~32k-token state budget; switch to a tokenizer if requests get rejected.
_MAX_STATE_CHARS = 60_000

QUESTIONS = {
    "rating": {
        "type": "choice",
        "instructions": (
            "Given the research plan, trader proposal and portfolio manager decision, "
            "which rating does the evidence best support for this instrument?"
        ),
        "options": list(RATINGS_5_TIER),
    },
    "sufficient_evidence": {
        "type": "noul",
        "instructions": (
            "Is the cited evidence specific and consistent enough to justify "
            "taking or changing a position today?"
        ),
    },
}


def build_state(final_state: Mapping[str, Any]) -> str:
    """The structured artifacts only; raw debate histories would blow the budget."""
    parts = [
        ("Instrument", final_state.get("instrument_context") or final_state.get("company_of_interest", "")),
        ("Trade date", final_state.get("trade_date", "")),
        ("Research plan", final_state.get("investment_plan", "")),
        ("Trader proposal", final_state.get("trader_investment_plan", "")),
        ("Portfolio manager decision", final_state.get("final_trade_decision", "")),
    ]
    return "\n\n".join(f"## {k}\n{v}" for k, v in parts if v)[:_MAX_STATE_CHARS]


def assess(final_state: Mapping[str, Any], timeout: float = 10.0) -> Optional[dict]:
    """Return Jev's raw answers for the run, or ``None`` when disabled or on any error."""
    api_key = os.environ.get("JEV_API_KEY")
    if not api_key:
        return None
    try:
        resp = requests.post(
            os.environ.get("JEV_API_URL", _DEFAULT_URL),
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": os.environ.get("JEV_MODEL", _DEFAULT_MODEL),
                "state": build_state(final_state),
                "questions": QUESTIONS,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        body = resp.json()
        # Response wrappers differ across gateways; keep the answers verbatim.
        return body.get("answers", body)
    except Exception as exc:  # noqa: BLE001 — shadow mode must never fail a run
        logger.warning("Jev assessment failed: %s", exc)
        return None
