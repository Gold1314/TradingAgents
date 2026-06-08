"""Lightweight per-node token + cost instrumentation for the web UI.

A LangChain callback handler that attributes every LLM call's token usage to the
LangGraph node that issued it (via the ``langgraph_node`` run metadata), then a
small, editable price table turns exact token counts into an *estimated* dollar
cost.

Design notes
------------
* **Token counts are EXACT** — taken from each provider's own usage reporting
  (``AIMessage.usage_metadata``, falling back to ``llm_output['token_usage']``).
* **Cost is an ESTIMATE** derived from the ``PRICES`` table below. The model
  names in this project are forward-looking, so treat these as best-effort
  defaults, not quotes — edit them to match your provider's live rates.
* **Fail-open**: any error inside the handler is swallowed so instrumentation
  can never break a run (mirrors the rest of the additive web layer). The
  handler is attached only to ``graph.stream`` via ``config['callbacks']``; the
  pipeline code is untouched.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, Optional, Tuple

from langchain_core.callbacks import BaseCallbackHandler

# Approximate USD per 1,000,000 tokens, as (input, output). EDIT THESE to match
# your provider's live pricing — they are best-effort defaults, not quotes. Keys
# are matched as case-insensitive substrings of the resolved model name, longest
# key first, so "gpt-5.5-pro" wins over "gpt-5.5".
PRICES: Dict[str, Tuple[float, float]] = {
    # ── OpenAI ──────────────────────────────────────────────────────────────
    "gpt-5.5-pro": (30.0, 180.0),  # anchored to the figure cited in 9router docs
    "gpt-5.5": (1.25, 10.0),
    "gpt-5.4-mini": (0.25, 2.0),
    "gpt-5.4-nano": (0.05, 0.40),
    "gpt-5.4": (1.0, 8.0),
    "gpt-5.2": (1.0, 8.0),
    "gpt-4.1": (2.0, 8.0),
    # ── Anthropic ───────────────────────────────────────────────────────────
    "claude-opus": (15.0, 75.0),
    "claude-sonnet": (3.0, 15.0),
    "claude-haiku": (0.80, 4.0),
    # ── Google ──────────────────────────────────────────────────────────────
    "gemini-3.1-pro": (1.25, 10.0),
    "gemini-3.5-flash": (0.30, 2.50),
    "gemini-2.5-pro": (1.25, 10.0),
    "gemini-2.5-flash": (0.30, 2.50),
}


def price_for(model: Optional[str]) -> Optional[Tuple[float, float]]:
    """Return the (input, output) per-1M rate for a model, or None if unknown."""
    if not model:
        return None
    m = model.lower()
    for key in sorted(PRICES, key=len, reverse=True):
        if key in m:
            return PRICES[key]
    return None


def estimate_cost(
    model: Optional[str], input_tokens: int, output_tokens: int
) -> Optional[float]:
    """Estimate USD cost from token counts, or None when the model is unpriced."""
    rate = price_for(model)
    if rate is None:
        return None
    p_in, p_out = rate
    return (input_tokens / 1_000_000.0) * p_in + (output_tokens / 1_000_000.0) * p_out


class TokenUsageTracker(BaseCallbackHandler):
    """Accumulates per-node token usage from graph-level LLM callbacks.

    LangGraph stamps every nested LLM run with ``metadata['langgraph_node']`` —
    the node display name (e.g. "Market Analyst", "Portfolio Manager"). We record
    that at ``on_*_start`` (keyed by run id) and read it back at ``on_llm_end`` to
    attribute the call's tokens to the right agent.
    """

    raise_error = False  # a callback error must never abort the run

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # node -> {"input": int, "output": int, "calls": int, "model": str|None}
        self._by_node: Dict[str, Dict[str, Any]] = {}
        # run_id -> node name (captured at start, consumed at end)
        self._run_node: Dict[str, str] = {}

    # ── start: remember which node owns this LLM run ────────────────────────
    def _record_start(self, run_id: Any, metadata: Optional[dict]) -> None:
        try:
            node = (metadata or {}).get("langgraph_node") or "Ungrouped"
            if run_id is not None:
                self._run_node[str(run_id)] = node
        except Exception:  # noqa: BLE001 — instrumentation must never raise
            pass

    def on_chat_model_start(
        self, serialized, messages, *, run_id=None, metadata=None, **kwargs
    ) -> None:
        self._record_start(run_id, metadata)

    def on_llm_start(
        self, serialized, prompts, *, run_id=None, metadata=None, **kwargs
    ) -> None:
        self._record_start(run_id, metadata)

    # ── end: pull usage off the response, attribute it to the node ──────────
    def on_llm_end(self, response, *, run_id=None, **kwargs) -> None:
        try:
            node = self._run_node.pop(str(run_id), "Ungrouped")
            input_tokens, output_tokens, model = self._extract(response)
            if input_tokens == 0 and output_tokens == 0:
                return
            with self._lock:
                slot = self._by_node.setdefault(
                    node, {"input": 0, "output": 0, "calls": 0, "model": model}
                )
                slot["input"] += input_tokens
                slot["output"] += output_tokens
                slot["calls"] += 1
                if model:
                    slot["model"] = model
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _extract(response) -> Tuple[int, int, Optional[str]]:
        """Return (input_tokens, output_tokens, model_name) from an LLMResult."""
        input_tokens = output_tokens = 0
        model: Optional[str] = None

        # Preferred: the standardized usage_metadata on each generation message.
        try:
            for gen_list in getattr(response, "generations", []) or []:
                for gen in gen_list:
                    msg = getattr(gen, "message", None)
                    if msg is None:
                        continue
                    um = getattr(msg, "usage_metadata", None)
                    if um:
                        input_tokens += int(um.get("input_tokens", 0) or 0)
                        output_tokens += int(um.get("output_tokens", 0) or 0)
                    rm = getattr(msg, "response_metadata", {}) or {}
                    model = model or rm.get("model_name") or rm.get("model")
        except Exception:  # noqa: BLE001
            pass

        # Fallback: provider llm_output token_usage (OpenAI-style aggregates).
        if input_tokens == 0 and output_tokens == 0:
            try:
                out = getattr(response, "llm_output", None) or {}
                model = model or out.get("model_name") or out.get("model")
                usage = out.get("token_usage") or out.get("usage") or {}
                input_tokens = int(usage.get("prompt_tokens", 0) or 0)
                output_tokens = int(usage.get("completion_tokens", 0) or 0)
            except Exception:  # noqa: BLE001
                pass

        return input_tokens, output_tokens, model

    # ── read paths for the server ───────────────────────────────────────────
    def node_snapshot(self, node: str) -> Optional[Dict[str, Any]]:
        """Current cumulative usage for one node, shaped as a UI event."""
        with self._lock:
            slot = self._by_node.get(node)
            return self._as_event(node, slot) if slot else None

    def summary(self) -> Dict[str, Any]:
        """Full per-node breakdown plus run totals."""
        with self._lock:
            nodes = {n: self._as_event(n, s) for n, s in self._by_node.items()}
        total_in = sum(v["input_tokens"] for v in nodes.values())
        total_out = sum(v["output_tokens"] for v in nodes.values())
        total_cost = 0.0
        cost_known = False
        for v in nodes.values():
            if v["cost"] is not None:
                total_cost += v["cost"]
                cost_known = True
        return {
            "nodes": nodes,
            "totals": {
                "input_tokens": total_in,
                "output_tokens": total_out,
                "total_tokens": total_in + total_out,
                "cost": round(total_cost, 6) if cost_known else None,
            },
        }

    @staticmethod
    def _as_event(node: str, slot: Dict[str, Any]) -> Dict[str, Any]:
        cost = estimate_cost(slot.get("model"), slot["input"], slot["output"])
        return {
            "node": node,
            "input_tokens": slot["input"],
            "output_tokens": slot["output"],
            "total_tokens": slot["input"] + slot["output"],
            "calls": slot["calls"],
            "model": slot.get("model"),
            "cost": round(cost, 6) if cost is not None else None,
        }
