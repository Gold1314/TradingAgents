"""Turn a live account snapshot into context text for the agents.

When grounding is enabled, this string is appended to the per-run instrument
context so every agent (most importantly the Trader and Portfolio Manager)
reasons about the *actual* account — buying power and the existing position in
the ticker — instead of an imaginary blank slate.
"""

from __future__ import annotations

from typing import Optional

from tradingagents.brokers.intents import AccountSnapshot


def _fmt_money(value: Optional[float], currency: str = "USD") -> str:
    if value is None:
        return "unknown"
    symbol = "$" if currency == "USD" else ""
    return f"{symbol}{value:,.2f}"


def build_account_context(snapshot: AccountSnapshot, ticker: str) -> str:
    """Render a compact, agent-readable summary of the connected account.

    Returns an empty string when there's nothing meaningful to say, so callers
    can safely concatenate the result unconditionally.
    """
    if snapshot is None:
        return ""

    lines = ["", "--- Live Brokerage Account (Robinhood) ---"]
    have_any = False

    if snapshot.buying_power is not None:
        lines.append(
            f"Available buying power: {_fmt_money(snapshot.buying_power, snapshot.currency)}."
        )
        have_any = True
    if snapshot.portfolio_value is not None:
        lines.append(
            f"Total portfolio value: {_fmt_money(snapshot.portfolio_value, snapshot.currency)}."
        )
        have_any = True

    position = snapshot.position_for(ticker)
    if position:
        qty = (
            position.get("quantity")
            or position.get("qty")
            or position.get("shares")
        )
        avg = (
            position.get("average_buy_price")
            or position.get("average_price")
            or position.get("avg_price")
        )
        detail = f"Current position in {ticker.upper()}: {qty} shares"
        if avg:
            detail += f" at an average cost of {_fmt_money(_safe_float(avg))}"
        lines.append(detail + ".")
        have_any = True
    else:
        lines.append(f"No existing position in {ticker.upper()}.")
        have_any = True

    if not have_any:
        return ""

    lines.append(
        "Factor this real position and buying power into sizing and the final "
        "recommendation. Do not propose selling more than is currently held."
    )
    return "\n".join(lines)


def _safe_float(value) -> Optional[float]:
    try:
        return float(str(value).replace("$", "").replace(",", ""))
    except (ValueError, TypeError):
        return None
