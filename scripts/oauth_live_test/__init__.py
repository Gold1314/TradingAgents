"""Self-contained live OAuth test harness for the Robinhood Trading MCP.

This package is **additive and isolated** (per ios-app-plan §1.6): it does not
import from or modify ``web/server.py`` and does not change the behaviour of any
existing broker module. It only *reuses* the read-only OAuth machinery
(``tradingagents.brokers.oauth`` + the installed ``mcp`` SDK) so an operator who
holds a real Robinhood Agentic account can run the remaining live-credentialed
checklist from ios-app-plan §5.1.5.G.

Safety model
------------
* The default commands (``discover``, ``build-authorize-url``) are **read-only
  and unauthenticated** — they never need credentials and never touch tokens.
* Any command that exchanges or refreshes real tokens is **opt-in** behind a
  ``--live`` flag and stops at confirming token acquisition/refresh. **No
  command ever places, reviews, or simulates a brokerage order.**

Run it as a module from the repo root::

    python -m scripts.oauth_live_test --help
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
