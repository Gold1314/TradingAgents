"""Typed, environment-overridable settings for the Robinhood MCP integration.

The canonical defaults live in ``DEFAULT_CONFIG["robinhood"]`` (see
:mod:`tradingagents.default_config`). This module turns that nested dict into a
typed :class:`RobinhoodConfig`, layering ``TRADINGAGENTS_ROBINHOOD_*`` env vars
on top so operators can flip the feature on/off and tune limits without editing
code. No secrets are read or stored here — OAuth tokens are handled separately
by :mod:`tradingagents.brokers.oauth`.

Trade modes
-----------
``trade_mode`` selects how the rating turns into an order:

* ``"off"``    — grounding only; never compute or place an order.
* ``"manual"`` — compute a *proposed* order after the run; a human places it via
  the UI "Place order" button (the click is the confirmation). **Default.**
* ``"auto"``   — place the order automatically right after the verdict.

Safety defaults are deliberately conservative:

* ``enabled`` defaults to ``False`` — the whole layer is inert unless asked for.
* ``trade_mode`` defaults to ``"manual"`` — execution always waits for a human
  click unless an operator explicitly opts into ``"auto"``.
* ``dry_run`` defaults to ``True`` — orders are *simulated* until an operator
  consciously sets ``dry_run=False``, in both manual and auto modes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional

DEFAULT_MCP_URL = "https://agent.robinhood.com/mcp/trading"

TRADE_MODES = ("off", "manual", "auto")


def _default_token_path() -> str:
    return os.path.join(
        os.path.expanduser("~"), ".tradingagents", "robinhood_token.json"
    )


# Canonical defaults merged into DEFAULT_CONFIG. Kept here (not just inlined in
# default_config) so tests and the broker can reason about the shape directly.
DEFAULT_ROBINHOOD_CONFIG: Dict[str, Any] = {
    "enabled": False,
    "mcp_url": DEFAULT_MCP_URL,
    # Inject the user's live positions / buying power into every agent's context.
    "grounding_enabled": True,
    # How the rating becomes an order: "off" | "manual" | "auto" (see module doc).
    "trade_mode": "manual",
    # Simulate orders instead of sending them. MUST be set False for a real order
    # to leave the building, in either manual or auto mode.
    "dry_run": True,
    # Per-order USD ceiling — a hard cap applied after sizing, regardless of
    # what the agents recommend. Orders above this are clamped down.
    "max_order_notional": 1000.0,
    # Default USD notional used when the agents don't imply a size of their own.
    "default_order_notional": 100.0,
    # "market" or "limit". Limit handling is left to the MCP server's schema.
    "order_type": "market",
    # OAuth token cache (created on first successful auth; never commit it).
    "token_storage_path": _default_token_path(),
    # OAuth callback loopback port for the local redirect handler.
    "oauth_callback_port": 8765,
    # Pin exact MCP tool names once you've seen what the server exposes. When a
    # value is None the broker discovers the tool by keyword heuristics.
    # Pinned to Robinhood's real tool names (verified live). Adaptive
    # name/arg resolution still runs as a fallback if a name is missing.
    "tool_overrides": {
        "accounts": "get_accounts",
        "portfolio": "get_portfolio",
        "positions": "get_equity_positions",
        "place_order": "place_equity_order",
        "review_order": "review_equity_order",
    },
    # Seconds to wait for a single MCP tool call before giving up.
    "request_timeout": 60.0,
}


def _coerce_bool(raw: str) -> bool:
    return raw.strip().lower() in ("true", "1", "yes", "on")


def _coerce_mode(raw: str) -> str:
    mode = raw.strip().lower()
    return mode if mode in TRADE_MODES else "manual"


@dataclass
class RobinhoodConfig:
    """Resolved Robinhood settings for one run."""

    enabled: bool = False
    mcp_url: str = DEFAULT_MCP_URL
    grounding_enabled: bool = True
    trade_mode: str = "manual"
    dry_run: bool = True
    max_order_notional: float = 1000.0
    default_order_notional: float = 100.0
    order_type: str = "market"
    token_storage_path: str = field(default_factory=_default_token_path)
    oauth_callback_port: int = 8765
    tool_overrides: Dict[str, Optional[str]] = field(default_factory=dict)
    request_timeout: float = 60.0

    # ── derived gates ────────────────────────────────────────────────────
    @property
    def is_auto(self) -> bool:
        return self.enabled and self.trade_mode == "auto"

    @property
    def is_manual(self) -> bool:
        return self.enabled and self.trade_mode == "manual"

    @property
    def executes_orders(self) -> bool:
        """True when this mode produces an order (proposed or placed)."""
        return self.enabled and self.trade_mode in ("manual", "auto")

    @property
    def can_place_real_orders(self) -> bool:
        """True when a click (manual) or the verdict (auto) WOULD spend money."""
        return bool(self.executes_orders and not self.dry_run)

    @property
    def auto_places_real_orders(self) -> bool:
        """True only when real orders fire automatically, with no human click."""
        return bool(self.is_auto and not self.dry_run)

    def public_status(self) -> Dict[str, Any]:
        """Non-secret snapshot safe to expose to a UI/API."""
        return {
            "enabled": self.enabled,
            "mcp_url": self.mcp_url,
            "grounding_enabled": self.grounding_enabled,
            "trade_mode": self.trade_mode,
            "dry_run": self.dry_run,
            "can_place_real_orders": self.can_place_real_orders,
            "auto_places_real_orders": self.auto_places_real_orders,
            "max_order_notional": self.max_order_notional,
            "default_order_notional": self.default_order_notional,
            "order_type": self.order_type,
        }


# env var -> (RobinhoodConfig attribute, coercion callable)
_ENV_MAP = {
    "TRADINGAGENTS_ROBINHOOD_ENABLED": ("enabled", _coerce_bool),
    "TRADINGAGENTS_ROBINHOOD_MCP_URL": ("mcp_url", str),
    "TRADINGAGENTS_ROBINHOOD_GROUNDING": ("grounding_enabled", _coerce_bool),
    "TRADINGAGENTS_ROBINHOOD_TRADE_MODE": ("trade_mode", _coerce_mode),
    "TRADINGAGENTS_ROBINHOOD_DRY_RUN": ("dry_run", _coerce_bool),
    "TRADINGAGENTS_ROBINHOOD_MAX_ORDER_NOTIONAL": ("max_order_notional", float),
    "TRADINGAGENTS_ROBINHOOD_DEFAULT_ORDER_NOTIONAL": ("default_order_notional", float),
    "TRADINGAGENTS_ROBINHOOD_ORDER_TYPE": ("order_type", str),
    "TRADINGAGENTS_ROBINHOOD_TOKEN_PATH": ("token_storage_path", str),
    "TRADINGAGENTS_ROBINHOOD_CALLBACK_PORT": ("oauth_callback_port", int),
    "TRADINGAGENTS_ROBINHOOD_REQUEST_TIMEOUT": ("request_timeout", float),
}


def load_robinhood_config(config: Optional[Mapping[str, Any]] = None) -> RobinhoodConfig:
    """Build a :class:`RobinhoodConfig` from a config dict + environment.

    Resolution order (later wins): packaged defaults → ``config["robinhood"]``
    → ``TRADINGAGENTS_ROBINHOOD_*`` env vars. ``config`` is typically
    ``DEFAULT_CONFIG`` or a per-run copy of it.
    """
    merged: Dict[str, Any] = dict(DEFAULT_ROBINHOOD_CONFIG)
    if config:
        provided = config.get("robinhood") or {}
        for key, value in provided.items():
            if key == "tool_overrides" and isinstance(value, Mapping):
                merged_overrides = dict(merged.get("tool_overrides") or {})
                merged_overrides.update(value)
                merged["tool_overrides"] = merged_overrides
            else:
                merged[key] = value

    # trade_mode is primary. If the caller didn't set it explicitly but did set
    # the legacy ``auto_trade`` boolean, honour that so older configs keep
    # working ("auto" when True, else "manual").
    provided = (config or {}).get("robinhood") or {}
    if provided.get("trade_mode"):
        mode = provided["trade_mode"]
    elif "auto_trade" in provided:
        mode = "auto" if provided.get("auto_trade") else "manual"
    else:
        mode = merged.get("trade_mode") or "manual"
    mode = str(mode).strip().lower()
    if mode not in TRADE_MODES:
        mode = "manual"

    resolved = RobinhoodConfig(
        enabled=bool(merged["enabled"]),
        mcp_url=str(merged["mcp_url"]),
        grounding_enabled=bool(merged["grounding_enabled"]),
        trade_mode=mode,
        dry_run=bool(merged["dry_run"]),
        max_order_notional=float(merged["max_order_notional"]),
        default_order_notional=float(merged["default_order_notional"]),
        order_type=str(merged["order_type"]),
        token_storage_path=os.path.expanduser(str(merged["token_storage_path"])),
        oauth_callback_port=int(merged["oauth_callback_port"]),
        tool_overrides=dict(merged.get("tool_overrides") or {}),
        request_timeout=float(merged["request_timeout"]),
    )

    for env_var, (attr, coerce) in _ENV_MAP.items():
        raw = os.environ.get(env_var)
        if raw is None or raw == "":
            continue
        try:
            setattr(resolved, attr, coerce(raw))
        except (TypeError, ValueError):
            # A malformed env override should never crash a run; keep the
            # already-resolved value and let logging downstream surface issues.
            continue

    if resolved.token_storage_path:
        resolved.token_storage_path = os.path.expanduser(resolved.token_storage_path)
    return resolved
