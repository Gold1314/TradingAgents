"""Brokerage integration for TradingAgents.

This package adds an *additive, opt-in* execution layer that connects the
agent pipeline to a live brokerage via the Model Context Protocol (MCP).
The first (and currently only) integration targets the **Robinhood Trading
MCP** (https://agent.robinhood.com/mcp/trading).

Nothing here runs unless explicitly enabled in config / environment. With the
feature disabled (the default), importing this package has no side effects and
the rest of the framework behaves exactly as before.

Layers
------
* :mod:`tradingagents.brokers.config`  — typed, env-overridable settings.
* :mod:`tradingagents.brokers.intents` — order/result/snapshot data models.
* :mod:`tradingagents.brokers.oauth`   — OAuth token storage + browser flow.
* :mod:`tradingagents.brokers.robinhood_mcp` — the MCP broker client.
* :mod:`tradingagents.brokers.executor` — rating → order-intent → placement.
* :mod:`tradingagents.brokers.grounding` — account context for the agents.
"""

from tradingagents.brokers.config import RobinhoodConfig, load_robinhood_config
from tradingagents.brokers.executor import (
    AutoTradeExecutor,
    clamp_intent,
    rating_to_intent,
)
from tradingagents.brokers.grounding import build_account_context
from tradingagents.brokers.intents import (
    AccountSnapshot,
    ExecutionResult,
    OrderIntent,
)
from tradingagents.brokers.robinhood_mcp import RobinhoodBroker

__all__ = [
    "RobinhoodConfig",
    "load_robinhood_config",
    "AccountSnapshot",
    "ExecutionResult",
    "OrderIntent",
    "RobinhoodBroker",
    "AutoTradeExecutor",
    "rating_to_intent",
    "clamp_intent",
    "build_account_context",
]
