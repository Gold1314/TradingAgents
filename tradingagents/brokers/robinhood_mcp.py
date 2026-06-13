"""Robinhood Trading MCP broker client.

Wraps the OAuth-protected Robinhood Trading MCP server behind a small, typed,
**synchronous** facade so the rest of the framework (which is sync, and in the
web app runs inside a worker thread) can read the account and place orders
without touching asyncio directly.

Key design choices
-------------------
* **Dedicated event loop.** MCP tool calls are async. We run them on a private
  asyncio loop in a daemon thread and bridge via ``run_coroutine_threadsafe``,
  which is safe to call from any thread (the FastAPI worker thread included).
* **Tool discovery, not hard-coding.** Robinhood's exact MCP tool names/schemas
  aren't part of a stable public contract, so we *discover* tools at connect
  time and match them by keyword (``position``, ``account``/``buying_power``,
  ``order``…). Operators can pin exact names via ``tool_overrides`` once known.
* **Adaptive argument mapping.** When placing an order we inspect the resolved
  tool's JSON-schema and map our intent fields (symbol/side/type/qty/notional)
  onto whatever property names the server actually expects.
* **Fail-open reads.** Account reads never raise into the pipeline; on error we
  return an empty snapshot so a transient broker issue can't abort an analysis.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any, Dict, List, Optional, Tuple

from tradingagents.brokers.config import RobinhoodConfig
from tradingagents.brokers.intents import AccountSnapshot, OrderIntent
from tradingagents.brokers.oauth import FileTokenStorage, build_oauth_provider

logger = logging.getLogger(__name__)

try:
    from langchain_mcp_adapters.client import MultiServerMCPClient

    ADAPTERS_AVAILABLE = True
except Exception:  # noqa: BLE001
    ADAPTERS_AVAILABLE = False


# Keyword heuristics for discovering tools by capability.
_POSITION_KEYWORDS = ("position", "holding")
_ACCOUNT_KEYWORDS = ("buying_power", "buying power", "balance", "account", "portfolio")
_ORDER_VERB_KEYWORDS = ("place", "create", "submit", "buy", "sell")


class RobinhoodBroker:
    """Synchronous facade over the Robinhood Trading MCP."""

    SERVER_KEY = "robinhood-trading"

    def __init__(self, cfg: RobinhoodConfig):
        self.cfg = cfg
        self.storage = FileTokenStorage(cfg.token_storage_path)
        self._client: Optional["MultiServerMCPClient"] = None
        self._tools: Dict[str, Any] = {}
        self._connected = False
        self._connect_error: Optional[str] = None
        # Resolved once per connection: the agentic_allowed account number that
        # all reads/orders target. Robinhood rejects orders on non-agentic ones.
        self._account_number: Optional[str] = None

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    # ── event-loop bridge ────────────────────────────────────────────────
    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop and self._loop.is_running():
                return self._loop
            loop = asyncio.new_event_loop()

            def _run() -> None:
                asyncio.set_event_loop(loop)
                loop.run_forever()

            thread = threading.Thread(
                target=_run, name="robinhood-mcp-loop", daemon=True
            )
            thread.start()
            self._loop = loop
            self._loop_thread = thread
            return loop

    def _run(self, coro, timeout: Optional[float] = None):
        loop = self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result(timeout=timeout or self.cfg.request_timeout)

    # ── connection ───────────────────────────────────────────────────────
    @property
    def is_connected(self) -> bool:
        return self._connected

    def has_saved_credentials(self) -> bool:
        return self.storage.has_tokens()

    def connect(self, timeout: Optional[float] = None, oauth_provider: Any = None) -> Dict[str, Any]:
        """Authorise (if needed) and load the MCP tool catalogue.

        First call may open a browser for OAuth consent. Returns a status dict;
        does not raise on failure (the error is captured in the status).

        ``oauth_provider`` is an optional, pre-built ``OAuthClientProvider``. When
        omitted (the default desktop path) the broker builds the standard loopback
        provider. The mobile/server-mediated flow
        (``web/mobile/oauth_flow.py``) injects a provider whose redirect URI and
        handlers drive a phone-based ``ASWebAuthenticationSession`` instead.
        """
        if not ADAPTERS_AVAILABLE:
            self._connect_error = (
                "langchain-mcp-adapters is not installed. "
                "Run: pip install langchain-mcp-adapters"
            )
            return self.status()
        if not self.cfg.enabled:
            self._connect_error = "Robinhood integration is disabled in config."
            return self.status()

        try:
            self._run(self._async_connect(oauth_provider=oauth_provider), timeout=timeout or 300.0)
            self._connected = True
            self._connect_error = None
        except Exception as exc:  # noqa: BLE001 — surface, don't crash callers
            self._connected = False
            self._connect_error = str(exc)
            logger.warning("Robinhood MCP connect failed: %s", exc)
        return self.status()

    async def _async_connect(self, oauth_provider: Any = None) -> None:
        auth = oauth_provider or build_oauth_provider(
            server_url=self.cfg.mcp_url,
            storage=self.storage,
            callback_port=self.cfg.oauth_callback_port,
        )
        self._client = MultiServerMCPClient(
            {
                self.SERVER_KEY: {
                    "transport": "streamable_http",
                    "url": self.cfg.mcp_url,
                    "auth": auth,
                }
            }
        )
        tools = await self._client.get_tools()
        self._tools = {t.name: t for t in tools}
        logger.info(
            "Connected to Robinhood MCP — %d tools: %s",
            len(self._tools),
            ", ".join(sorted(self._tools)),
        )

    def status(self) -> Dict[str, Any]:
        return {
            **self.cfg.public_status(),
            "available": ADAPTERS_AVAILABLE,
            "connected": self._connected,
            "has_saved_credentials": self.has_saved_credentials(),
            "tools": sorted(self._tools.keys()),
            "error": self._connect_error,
        }

    def disconnect(self) -> None:
        self._connected = False
        self._tools = {}
        self._client = None
        self._account_number = None

    # ── tool resolution ──────────────────────────────────────────────────
    def _resolve_tool(self, category: str) -> Optional[Any]:
        override = (self.cfg.tool_overrides or {}).get(category)
        if override and override in self._tools:
            return self._tools[override]

        if category == "positions":
            keywords = _POSITION_KEYWORDS
        elif category == "portfolio":
            keywords = ("portfolio", "buying_power", "balance")
        elif category == "accounts":
            keywords = ("get_accounts", "accounts")
        elif category == "quotes":
            keywords = ("equity_quotes",)  # not index/option quotes
        elif category in ("account",):
            keywords = _ACCOUNT_KEYWORDS
        elif category == "review_order":
            return self._resolve_order_tool(review=True)
        elif category == "place_order":
            return self._resolve_order_tool()
        else:
            keywords = ()

        for name, tool in self._tools.items():
            low = name.lower()
            if any(k in low for k in keywords):
                return tool
        return None

    def _resolve_order_tool(self, review: bool = False) -> Optional[Any]:
        """Find the place-order tool (or the review/simulate tool when ``review``).

        Prefers equity tools and penalises read-only/option variants so that, on
        Robinhood, ``place_equity_order`` / ``review_equity_order`` win even when
        no explicit override is set."""
        ranked: List[Tuple[int, Any]] = []
        for name, tool in self._tools.items():
            low = name.lower()
            score = 0
            if "order" in low:
                score += 2
            if any(v in low for v in _ORDER_VERB_KEYWORDS):
                score += 1
            if "equity" in low:
                score += 1  # prefer equities over options for the default path
            if review:
                # Want the simulate/review tool, not the real placement one.
                score += 3 if "review" in low else -5
            else:
                if "review" in low:
                    score -= 5
                if any(bad in low for bad in ("cancel", "history", "list", "status")):
                    score -= 5
                if low.startswith("get_") or "get_" in low:
                    score -= 5
            if score > 0:
                ranked.append((score, tool))
        if not ranked:
            return None
        ranked.sort(key=lambda x: x[0], reverse=True)
        return ranked[0][1]

    # ── account-number resolution (agentic account) ──────────────────────
    def get_account_number(self) -> Optional[str]:
        """Resolve and cache the agentic_allowed account number for trading."""
        if self._account_number:
            return self._account_number
        if not self._connected:
            return None
        try:
            self._account_number = self._run(self._async_account_number())
        except Exception as exc:  # noqa: BLE001
            logger.warning("Robinhood account-number resolution failed: %s", exc)
        return self._account_number

    async def _async_account_number(self) -> Optional[str]:
        tool = self._resolve_tool("accounts")
        if tool is None:
            return None
        data = _envelope(_coerce_result(await tool.ainvoke({})))
        rows = data.get("accounts") if isinstance(data, dict) else data
        rows = [r for r in (rows or []) if isinstance(r, dict)]
        if not rows:
            return None
        # Trading is only allowed on an agentic_allowed account; prefer it.
        agentic = [r for r in rows if r.get("agentic_allowed")]
        chosen = (agentic or rows)[0]
        return chosen.get("account_number") or chosen.get("rhs_account_number")

    # ── account reads (fail-open) ────────────────────────────────────────
    def get_account_snapshot(self) -> AccountSnapshot:
        if not self._connected:
            return AccountSnapshot()
        try:
            return self._run(self._async_account_snapshot())
        except Exception as exc:  # noqa: BLE001 — never break the pipeline
            logger.warning("Robinhood account snapshot failed: %s", exc)
            return AccountSnapshot()

    async def _async_account_snapshot(self) -> AccountSnapshot:
        snapshot = AccountSnapshot()
        acct = await self._async_account_number()
        if acct:
            self._account_number = acct
            snapshot.account_number = acct

        portfolio_tool = self._resolve_tool("portfolio")
        if portfolio_tool is not None and acct:
            data = _envelope(_coerce_result(await portfolio_tool.ainvoke({"account_number": acct})))
            if isinstance(data, dict):
                bp = data.get("buying_power")
                if isinstance(bp, dict):  # Robinhood nests the numeric value
                    bp = bp.get("buying_power") or bp.get("unleveraged_buying_power")
                snapshot.buying_power = _to_float(bp)
                snapshot.portfolio_value = _to_float(
                    _pick(data, "total_value", "equity_value", "portfolio_value")
                )
                snapshot.currency = data.get("currency") or snapshot.currency
                snapshot.raw["portfolio"] = data

        positions_tool = self._resolve_tool("positions")
        if positions_tool is not None and acct:
            data = _envelope(_coerce_result(await positions_tool.ainvoke({"account_number": acct})))
            rows = data.get("positions") if isinstance(data, dict) else data
            snapshot.positions = [r for r in (rows or []) if isinstance(r, dict)]
            snapshot.raw["positions"] = rows

        return snapshot

    def get_holdings(self) -> List[Dict[str, Any]]:
        """Every equity position across *all* visible accounts (the real portfolio).

        Unlike :meth:`get_account_snapshot` (scoped to the agentic trading
        account), this reads positions from each account ``get_accounts`` returns
        — including the main brokerage account that actually holds stock. Each row
        is normalized to symbol / quantity / average_buy_price / cost_basis plus a
        masked ``account`` and ``agentic`` flag, and enriched with a live
        ``last_price`` → ``market_value`` and ``gain`` / ``gain_pct`` when quotes
        are available. Sorted by market value (cost basis fallback). Read-only and
        fail-open: any error yields an empty list.
        """
        if not self._connected:
            return []
        try:
            return self._run(self._async_holdings())
        except Exception as exc:  # noqa: BLE001 — never break the caller
            logger.warning("Robinhood holdings fetch failed: %s", exc)
            return []

    async def _async_holdings(self) -> List[Dict[str, Any]]:
        acc_tool = self._resolve_tool("accounts")
        pos_tool = self._resolve_tool("positions")
        if acc_tool is None or pos_tool is None:
            return []
        accounts = _envelope(_coerce_result(await acc_tool.ainvoke({})))
        rows = accounts.get("accounts") if isinstance(accounts, dict) else accounts
        rows = [r for r in (rows or []) if isinstance(r, dict)]
        holdings: List[Dict[str, Any]] = []
        for acc in rows:
            number = acc.get("account_number") or acc.get("rhs_account_number")
            if not number:
                continue
            masked = "…" + str(number)[-4:]
            agentic = bool(acc.get("agentic_allowed"))
            data = _envelope(_coerce_result(await pos_tool.ainvoke({"account_number": number})))
            prows = data.get("positions") if isinstance(data, dict) else data
            for pos in prows or []:
                if not isinstance(pos, dict):
                    continue
                qty = _to_float(pos.get("quantity"))
                if qty is None or qty <= 0:  # skip closed/zero lots
                    continue
                avg = _to_float(pos.get("average_buy_price") or pos.get("average_price"))
                holdings.append(
                    {
                        "symbol": pos.get("symbol") or pos.get("ticker") or pos.get("instrument_symbol"),
                        "quantity": qty,
                        "average_buy_price": avg,
                        "cost_basis": (qty * avg) if (qty is not None and avg is not None) else None,
                        "account": masked,
                        "agentic": agentic,
                    }
                )
        # Enrich with live prices → market value + gain/loss (best-effort).
        prices = await self._async_quotes(
            sorted({h["symbol"] for h in holdings if h.get("symbol")})
        )
        for h in holdings:
            px = prices.get(h.get("symbol"))
            if px is None or h.get("quantity") is None:
                continue
            h["last_price"] = px
            h["market_value"] = h["quantity"] * px
            cost = h.get("cost_basis")
            if cost is not None:
                h["gain"] = h["market_value"] - cost
                h["gain_pct"] = (h["gain"] / cost * 100.0) if cost else None
        holdings.sort(
            key=lambda h: (h.get("market_value") or h.get("cost_basis") or 0), reverse=True
        )
        return holdings

    async def _async_quotes(self, symbols: List[str]) -> Dict[str, float]:
        """Map ``symbol → last trade price`` via the equity-quotes tool.

        Batches in chunks of 20 (the MCP omits closes above that) and is
        fail-open: a failed chunk just leaves those symbols unpriced.
        """
        out: Dict[str, float] = {}
        if not symbols:
            return out
        tool = self._resolve_tool("quotes")
        if tool is None:
            return out
        for start in range(0, len(symbols), 20):
            chunk = symbols[start : start + 20]
            try:
                data = _envelope(_coerce_result(await tool.ainvoke({"symbols": chunk})))
            except Exception as exc:  # noqa: BLE001 — quotes are best-effort
                logger.warning("Robinhood quotes fetch failed: %s", exc)
                continue
            results = data.get("results") if isinstance(data, dict) else data
            for row in results or []:
                quote = row.get("quote") if isinstance(row, dict) else None
                if not isinstance(quote, dict):
                    continue
                sym = quote.get("symbol")
                price = _to_float(
                    quote.get("last_trade_price")
                    or quote.get("last_non_reg_trade_price")
                    or quote.get("previous_close")
                )
                if sym and price is not None:
                    out[sym] = price
        return out

    # ── order placement / review ─────────────────────────────────────────
    def place_order(self, intent: OrderIntent) -> Tuple[Optional[str], Any]:
        """Submit ``intent`` to the broker. Returns ``(order_id, raw_response)``.

        Raises on failure so the executor can record an ``error`` result. Never
        call this directly when ``cfg.dry_run`` is True — the executor guards it.
        """
        if not self._connected:
            raise RuntimeError("Robinhood broker is not connected.")
        tool = self._resolve_tool("place_order")
        if tool is None:
            raise RuntimeError(
                "No order-placement tool found on the Robinhood MCP. "
                "Set robinhood.tool_overrides.place_order to the correct name."
            )
        acct = self.get_account_number()
        if not acct:
            raise RuntimeError(
                "No agentic-enabled Robinhood account found. Open and fund an "
                "Agentic account (agentic_allowed=true) before trading."
            )
        args = _map_order_args(tool, intent, acct)
        logger.info("Placing %s order via MCP tool '%s': %s", intent.action, tool.name, args)
        parsed = _envelope(_coerce_result(self._run(tool.ainvoke(args))))
        order_id = None
        rec = _first_record(parsed)
        if isinstance(rec, dict):
            order_id = _pick(rec, "id", "order_id", "orderId", "ref_id")
        return (str(order_id) if order_id is not None else None), parsed

    def review_order(self, intent: OrderIntent) -> Optional[Any]:
        """Ask Robinhood to *simulate* the order (pre-trade checks), placing nothing.

        Used to give dry-run a real validation (buying power, halts, PDT, …).
        Returns the parsed review payload, or ``None`` if unavailable/failed.
        """
        if not self._connected:
            return None
        tool = self._resolve_tool("review_order")
        acct = self.get_account_number()
        if tool is None or not acct:
            return None
        try:
            args = _map_order_args(tool, intent, acct)
            return _envelope(_coerce_result(self._run(tool.ainvoke(args))))
        except Exception as exc:  # noqa: BLE001 — review is best-effort
            logger.warning("Robinhood order review failed: %s", exc)
            return None


# ── result parsing helpers ───────────────────────────────────────────────
def _coerce_result(result: Any) -> Any:
    """Best-effort decode of an MCP tool result into Python data.

    Handles the shapes the Robinhood MCP returns through langchain-mcp-adapters:
    a ``(content, artifact)`` tuple; a list of content blocks
    ``[{"type":"text","text":"<json>"}]``; or a plain (possibly JSON) string.
    Falls back to the original value when nothing parses.
    """
    if isinstance(result, tuple) and result:
        result = result[0]
    # MCP content-block list → concatenate the text blocks and parse as JSON.
    if isinstance(result, list) and result and all(
        isinstance(b, dict) and "text" in b for b in result
    ):
        joined = "".join(
            b.get("text", "") for b in result if b.get("type", "text") == "text"
        )
        try:
            return json.loads(joined)
        except (ValueError, TypeError):
            return joined
    if isinstance(result, (dict, list)):
        return result
    if isinstance(result, str):
        text = result.strip()
        try:
            return json.loads(text)
        except (ValueError, TypeError):
            return text
    return result


def _envelope(data: Any) -> Any:
    """Strip Robinhood's ``{"data": {...}, "guide": "..."}`` wrapper when present."""
    if isinstance(data, dict) and isinstance(data.get("data"), (dict, list)):
        return data["data"]
    return data


def _fmt_num(value: Any) -> str:
    """Robinhood expects numeric order fields as strings (e.g. '3' or '0.5')."""
    try:
        s = f"{float(value):.6f}".rstrip("0").rstrip(".")
        return s or "0"
    except (TypeError, ValueError):
        return str(value)


def _as_list(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    if isinstance(data, dict):
        for key in ("positions", "results", "data", "items"):
            inner = data.get(key)
            if isinstance(inner, list):
                return [d for d in inner if isinstance(d, dict)]
        return [data]
    return []


def _first_record(data: Any) -> Dict[str, Any]:
    if isinstance(data, dict):
        for key in ("results", "data", "account", "accounts"):
            inner = data.get(key)
            if isinstance(inner, list) and inner and isinstance(inner[0], dict):
                return inner[0]
            if isinstance(inner, dict):
                return inner
        return data
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    return {}


def _pick(record: Any, *keys: str) -> Any:
    if not isinstance(record, dict):
        return None
    for key in keys:
        if key in record and record[key] not in (None, ""):
            return record[key]
    return None


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(str(value).replace("$", "").replace(",", ""))
    except (ValueError, TypeError):
        return None


def _map_order_args(
    tool: Any, intent: OrderIntent, account_number: Optional[str] = None
) -> Dict[str, Any]:
    """Map an :class:`OrderIntent` onto the order tool's actual parameter names.

    Inspects ``tool.args`` (a JSON-schema-ish dict of property→spec) and fills
    account/symbol/side/type and either quantity or dollar_amount, using whatever
    property names the server exposes. Numeric values are stringified because
    the Robinhood MCP expects strings. Falls back to canonical names when the
    schema is unavailable.
    """
    schema_keys = set()
    try:
        schema_keys = set((tool.args or {}).keys())
    except Exception:  # noqa: BLE001
        schema_keys = set()

    def match(*candidates: str) -> Optional[str]:
        for cand in candidates:
            for key in schema_keys:
                if key.lower() == cand:
                    return key
        for cand in candidates:  # substring fallback
            for key in schema_keys:
                if cand in key.lower():
                    return key
        return None

    args: Dict[str, Any] = {}

    if account_number is not None:
        args[match("account_number", "account") or "account_number"] = account_number

    args[match("symbol", "ticker", "instrument") or "symbol"] = intent.ticker
    args[match("side", "action", "direction") or "side"] = intent.action
    args[match("type", "order_type") or "type"] = intent.order_type

    if intent.quantity is not None:
        args[match("quantity", "qty", "shares", "units") or "quantity"] = _fmt_num(
            intent.quantity
        )
    if intent.notional is not None:
        notional_key = match("dollar_amount", "notional", "dollars", "amount")
        if notional_key:
            args[notional_key] = f"{float(intent.notional):.2f}"
        elif intent.quantity is None:
            args["dollar_amount"] = f"{float(intent.notional):.2f}"

    return args
