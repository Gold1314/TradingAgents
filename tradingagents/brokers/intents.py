"""Data models shared across the brokerage layer.

These are deliberately plain dataclasses (no MCP/network imports) so the
rating→intent mapping and the web/API serialisation can be unit-tested and
reasoned about without a live broker.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# The five Portfolio Manager ratings, ordered bearish → bullish.
RATING_ORDER = ["Sell", "Underweight", "Hold", "Overweight", "Buy"]


def _opt_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass
class OrderIntent:
    """A broker-agnostic description of the order implied by a decision.

    ``notional`` (USD) and ``quantity`` (shares) are mutually exclusive ways to
    size the order; exactly one is normally set. ``action`` is one of
    ``"buy"``, ``"sell"`` or ``"none"`` (the latter for Hold / no-op).
    """

    ticker: str
    action: str  # "buy" | "sell" | "none"
    rating: str
    notional: Optional[float] = None
    quantity: Optional[float] = None
    order_type: str = "market"
    reason: str = ""

    @property
    def is_actionable(self) -> bool:
        return self.action in ("buy", "sell")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OrderIntent":
        """Build a validated intent from an (untrusted) client payload.

        Coerces types and normalises ``action``; unknown actions collapse to
        ``"none"``. Sizing is left as-is here — the executor re-clamps it to the
        account's limits before anything is placed.
        """
        action = str(data.get("action", "none")).strip().lower()
        if action not in ("buy", "sell", "none"):
            action = "none"
        return cls(
            ticker=str(data.get("ticker", "")).strip().upper(),
            action=action,
            rating=str(data.get("rating", "")),
            notional=_opt_float(data.get("notional")),
            quantity=_opt_float(data.get("quantity")),
            order_type=str(data.get("order_type", "market")).strip().lower() or "market",
            reason=str(data.get("reason", "")),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ticker": self.ticker,
            "action": self.action,
            "rating": self.rating,
            "notional": self.notional,
            "quantity": self.quantity,
            "order_type": self.order_type,
            "reason": self.reason,
        }


@dataclass
class ExecutionResult:
    """Outcome of attempting to act on an :class:`OrderIntent`.

    ``status`` values:
      * ``disabled``  — the integration or auto-trade is off.
      * ``skipped``   — a Hold / non-actionable rating, nothing to do.
      * ``dry_run``   — order was simulated, not sent (dry_run=True).
      * ``placed``    — order was submitted to the broker.
      * ``error``     — something failed (see ``message``).
    """

    status: str
    intent: Optional[OrderIntent] = None
    order_id: Optional[str] = None
    message: str = ""
    raw: Optional[Any] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "intent": self.intent.to_dict() if self.intent else None,
            "order_id": self.order_id,
            "message": self.message,
        }


@dataclass
class AccountSnapshot:
    """A read-only view of the connected brokerage account."""

    buying_power: Optional[float] = None
    portfolio_value: Optional[float] = None
    currency: str = "USD"
    account_number: Optional[str] = None
    positions: List[Dict[str, Any]] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)

    def position_for(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Return the held position matching ``ticker`` (case-insensitive)."""
        want = (ticker or "").strip().upper()
        for pos in self.positions:
            sym = str(
                pos.get("symbol")
                or pos.get("ticker")
                or pos.get("instrument")
                or ""
            ).upper()
            if sym == want:
                return pos
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "buying_power": self.buying_power,
            "portfolio_value": self.portfolio_value,
            "currency": self.currency,
            "account_number": self.account_number,
            "positions": self.positions,
        }
