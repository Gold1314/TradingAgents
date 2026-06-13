"""Translate a Portfolio Manager rating into a broker order and (optionally)
place it.

The mapping is intentionally simple and transparent so its behaviour is
auditable:

* **Buy**        → buy ``default_order_notional`` USD.
* **Overweight** → buy half of ``default_order_notional`` USD.
* **Hold**       → no action.
* **Underweight**→ sell half of the currently held shares (if any).
* **Sell**       → sell the entire currently held position (if any).

Sizing is always clamped to ``max_order_notional`` and, for buys, to the
available buying power when known. Sells require a known position — the executor
never shorts or sells more than is held.
"""

from __future__ import annotations

import logging
import math
from typing import Optional

from tradingagents.brokers.config import RobinhoodConfig
from tradingagents.brokers.intents import (
    AccountSnapshot,
    ExecutionResult,
    OrderIntent,
)
from tradingagents.brokers.robinhood_mcp import RobinhoodBroker

logger = logging.getLogger(__name__)

# Fraction of default_order_notional to buy per bullish rating.
_BUY_FRACTION = {"Buy": 1.0, "Overweight": 0.5}
# Fraction of the held position to sell per bearish rating.
_SELL_FRACTION = {"Sell": 1.0, "Underweight": 0.5}


def _position_quantity(position: Optional[dict]) -> Optional[float]:
    if not position:
        return None
    raw = (
        position.get("quantity")
        or position.get("qty")
        or position.get("shares")
        or position.get("units")
    )
    try:
        return float(str(raw).replace(",", ""))
    except (ValueError, TypeError):
        return None


def _summarize_review(review: Optional[object]) -> str:
    """Pull a short human string out of a review_equity_order payload.

    Surfaces the live quote price and any pre-trade alerts/checks Robinhood
    returns (``order_checks`` is empty when the order passes cleanly).
    """
    if not isinstance(review, dict):
        return ""
    parts = []
    quote = review.get("quote_data")
    if isinstance(quote, dict):
        price = quote.get("last_trade_price") or quote.get("ask_price")
        try:
            if price is not None:
                parts.append(f"last {float(price):.2f}")
        except (TypeError, ValueError):
            pass

    checks = (
        review.get("order_checks")
        or review.get("alerts")
        or review.get("pre_trade_alerts")
    )
    alerts = checks.values() if isinstance(checks, dict) else checks
    if isinstance(alerts, (list, tuple)):
        msgs = []
        for a in alerts:
            if isinstance(a, dict):
                msgs.append(str(a.get("message") or a.get("type") or a))
            elif a:
                msgs.append(str(a))
        if any(msgs):
            parts.append("; ".join(m for m in msgs if m))

    return " | ".join(parts)[:300]


def rating_to_intent(
    rating: str,
    ticker: str,
    snapshot: Optional[AccountSnapshot],
    cfg: RobinhoodConfig,
) -> OrderIntent:
    """Pure mapping from a 5-tier rating to an :class:`OrderIntent`.

    No network calls — given a rating, ticker, account snapshot and config it
    deterministically returns the intent, which makes it unit-testable.
    """
    ticker = (ticker or "").strip().upper()
    rating = (rating or "").strip()
    snapshot = snapshot or AccountSnapshot()

    if rating in _BUY_FRACTION:
        notional = cfg.default_order_notional * _BUY_FRACTION[rating]
        notional = min(notional, cfg.max_order_notional)
        if snapshot.buying_power is not None:
            notional = min(notional, snapshot.buying_power)
        notional = round(max(notional, 0.0), 2)
        if notional <= 0:
            return OrderIntent(
                ticker=ticker,
                action="none",
                rating=rating,
                reason="Insufficient buying power for a buy order.",
            )
        return OrderIntent(
            ticker=ticker,
            action="buy",
            rating=rating,
            notional=notional,
            order_type=cfg.order_type,
            reason=f"{rating}: allocate {notional:.2f} USD.",
        )

    if rating in _SELL_FRACTION:
        held = _position_quantity(snapshot.position_for(ticker))
        if not held or held <= 0:
            return OrderIntent(
                ticker=ticker,
                action="none",
                rating=rating,
                reason=f"{rating} but no position held in {ticker}; nothing to sell.",
            )
        qty = held * _SELL_FRACTION[rating]
        # Round down to 6dp to avoid selling fractionally more than held.
        qty = math.floor(qty * 1e6) / 1e6
        if qty <= 0:
            return OrderIntent(
                ticker=ticker,
                action="none",
                rating=rating,
                reason=f"{rating}: computed sell quantity rounds to zero.",
            )
        return OrderIntent(
            ticker=ticker,
            action="sell",
            rating=rating,
            quantity=qty,
            order_type=cfg.order_type,
            reason=f"{rating}: sell {qty} of {held} held shares.",
        )

    # Hold or any unrecognised rating → no action.
    return OrderIntent(
        ticker=ticker,
        action="none",
        rating=rating or "Hold",
        reason="Hold: no order.",
    )


def clamp_intent(
    intent: OrderIntent,
    snapshot: Optional[AccountSnapshot],
    cfg: RobinhoodConfig,
) -> OrderIntent:
    """Re-apply hard limits to an intent regardless of where it came from.

    This is the authoritative guard for the manual path: an edited order ticket
    arrives from the client, but the *server* always re-clamps buy notionals to
    ``max_order_notional`` and available buying power, and sell quantities to the
    shares actually held — so a tampered or stale ticket can't exceed limits.
    """
    snapshot = snapshot or AccountSnapshot()
    if intent.action == "buy" and intent.notional is not None:
        notional = min(intent.notional, cfg.max_order_notional)
        if snapshot.buying_power is not None:
            notional = min(notional, snapshot.buying_power)
        intent.notional = round(max(notional, 0.0), 2)
    if intent.action == "sell" and intent.quantity is not None:
        held = _position_quantity(snapshot.position_for(intent.ticker))
        if held is not None:
            intent.quantity = min(intent.quantity, held)
    return intent


class AutoTradeExecutor:
    """Drives rating → intent → (proposed | simulated | real) order placement.

    In ``manual`` mode :meth:`execute` returns a ``proposed`` result (the intent
    is computed but not placed); the UI then calls :meth:`execute_intent` when
    the user clicks "Place order". In ``auto`` mode :meth:`execute` places (or
    simulates, under ``dry_run``) immediately.
    """

    def __init__(self, broker: RobinhoodBroker, cfg: RobinhoodConfig):
        self.broker = broker
        self.cfg = cfg

    def execute(
        self,
        rating: str,
        ticker: str,
        snapshot: Optional[AccountSnapshot] = None,
    ) -> ExecutionResult:
        """Compute the order for ``rating`` and act per the configured mode."""
        if not self.cfg.executes_orders:
            return ExecutionResult(
                status="disabled",
                message="Robinhood execution is off (grounding only).",
            )

        if snapshot is None:
            snapshot = self.broker.get_account_snapshot()

        intent = rating_to_intent(rating, ticker, snapshot, self.cfg)

        if not intent.is_actionable:
            return ExecutionResult(
                status="skipped", intent=intent, message=intent.reason
            )

        # Manual mode: stop here. A human places it via execute_intent().
        if self.cfg.is_manual:
            return ExecutionResult(
                status="proposed",
                intent=intent,
                message=f"Proposed: {intent.action} {ticker}. {intent.reason}",
            )

        # Auto mode: place (or simulate) right now.
        return self.execute_intent(intent, snapshot=snapshot)

    def execute_intent(
        self,
        intent: OrderIntent,
        snapshot: Optional[AccountSnapshot] = None,
    ) -> ExecutionResult:
        """Place a concrete (possibly user-edited) intent, with re-clamping.

        Used by both the auto path and the manual "Place order" button. Honors
        ``dry_run`` so the click flow can be rehearsed without sending an order.
        """
        if not self.cfg.executes_orders:
            return ExecutionResult(
                status="disabled", message="Robinhood execution is off."
            )
        if not intent.is_actionable:
            return ExecutionResult(
                status="skipped", intent=intent, message=intent.reason or "No action."
            )

        if snapshot is None:
            snapshot = self.broker.get_account_snapshot()
        intent = clamp_intent(intent, snapshot, self.cfg)

        if (intent.notional is not None and intent.notional <= 0) or (
            intent.quantity is not None and intent.quantity <= 0
        ):
            return ExecutionResult(
                status="skipped",
                intent=intent,
                message="Order size clamped to zero (insufficient funds or holdings).",
            )

        if self.cfg.dry_run:
            # Best-effort: ask Robinhood to *simulate* the order for real
            # pre-trade checks (buying power, halts, PDT) without placing it.
            review = None
            review_fn = getattr(self.broker, "review_order", None)
            if callable(review_fn):
                review = review_fn(intent)
            message = (
                f"DRY RUN — would {intent.action} {intent.ticker}. {intent.reason}"
            )
            alerts = _summarize_review(review)
            if alerts:
                message += f" | Robinhood checks: {alerts}"
            return ExecutionResult(
                status="dry_run",
                intent=intent,
                message=message,
                raw=review,
            )

        try:
            order_id, raw = self.broker.place_order(intent)
            return ExecutionResult(
                status="placed",
                intent=intent,
                order_id=order_id,
                message=f"Order submitted ({intent.action} {intent.ticker}).",
                raw=raw,
            )
        except Exception as exc:  # noqa: BLE001 — report, don't crash the run
            logger.exception("order placement failed")
            return ExecutionResult(
                status="error",
                intent=intent,
                message=f"Order placement failed: {exc}",
            )
