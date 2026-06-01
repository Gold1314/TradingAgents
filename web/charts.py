"""Builds a candlestick + indicator payload for the web UI's price chart.

Reuses the exact data layer the Market Analyst uses — ``load_ohlcv`` (cached,
look-ahead-safe) for OHLCV and ``stockstats.wrap`` for the technical indicators
— so the chart shows the same numbers the agent reasoned over. Returns plain
JSON-serializable dicts shaped for TradingView Lightweight Charts.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

import pandas as pd
from stockstats import wrap

from tradingagents.dataflows.stockstats_utils import load_ohlcv
from tradingagents.dataflows.symbol_utils import normalize_symbol

# Indicators overlaid on / shown beside the price chart (same names the analyst
# selects from). Each maps to a stockstats column.
CHART_INDICATORS = (
    "close_50_sma",
    "close_200_sma",
    "close_10_ema",
    "boll",
    "boll_ub",
    "boll_lb",
    "rsi",
    "macd",
    "macds",
    "macdh",
)


def _clean_series(dates: List[str], values: List[Any]) -> List[Dict[str, Any]]:
    """Zip dates+values into [{time, value}], dropping NaN/inf (e.g. 200 SMA warmup)."""
    out: List[Dict[str, Any]] = []
    for d, v in zip(dates, values):
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isnan(f) or math.isinf(f):
            continue
        out.append({"time": d, "value": round(f, 4)})
    return out


def build_chart_payload(
    ticker: str, trade_date: str, lookback: int = 180
) -> Dict[str, Any]:
    """Return candles + volume + indicator series for the last ``lookback`` rows
    on or before ``trade_date``. Raises on missing data."""
    df = load_ohlcv(ticker, trade_date).copy()
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date"]).sort_values("Date")
    df = df[df["Date"] <= pd.to_datetime(trade_date)]
    if df.empty:
        raise ValueError(f"no OHLCV rows for {ticker} on or before {trade_date}")

    # Compute every indicator on the full frame (stockstats needs history for
    # warmup), then slice the trailing window for display.
    stock = wrap(df.copy())
    ind_full: Dict[str, Optional[List[Any]]] = {}
    for name in CHART_INDICATORS:
        try:
            ind_full[name] = list(stock[name].to_numpy())
        except Exception:  # noqa: BLE001 — a bad indicator must not sink the chart
            ind_full[name] = None

    n = len(df)
    k = max(1, min(int(lookback), n))
    start = n - k

    dates = [d.strftime("%Y-%m-%d") for d in df["Date"].iloc[start:]]
    o = df["Open"].iloc[start:].tolist()
    h = df["High"].iloc[start:].tolist()
    low = df["Low"].iloc[start:].tolist()
    c = df["Close"].iloc[start:].tolist()
    v = df["Volume"].iloc[start:].tolist()

    candles: List[Dict[str, Any]] = []
    volume: List[Dict[str, Any]] = []
    for i, d in enumerate(dates):
        candles.append(
            {
                "time": d,
                "open": round(float(o[i]), 2),
                "high": round(float(h[i]), 2),
                "low": round(float(low[i]), 2),
                "close": round(float(c[i]), 2),
            }
        )
        up = c[i] >= o[i]
        volume.append(
            {
                "time": d,
                "value": float(v[i]) if not pd.isna(v[i]) else 0.0,
                "color": "rgba(52,211,153,.45)" if up else "rgba(251,113,133,.45)",
            }
        )

    indicators: Dict[str, List[Dict[str, Any]]] = {}
    for name, arr in ind_full.items():
        if arr is None:
            continue
        series = _clean_series(dates, arr[start:])
        if series:
            indicators[name] = series

    return {
        "symbol": normalize_symbol(ticker),
        "from": dates[0],
        "to": dates[-1],
        "candles": candles,
        "volume": volume,
        "indicators": indicators,
    }
