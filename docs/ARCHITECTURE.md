# TradingAgents — Architecture & End-to-End Flow

> A practical, diagram-first walkthrough of how a single call —
> `propagate("AAPL", "2024-05-10")` — turns a ticker + date into a
> **Buy / Overweight / Hold / Underweight / Sell** decision.

TradingAgents is a **multi-agent LLM framework** built on **LangGraph**. It models a
real trading desk: specialized agents analyze a security, debate it, propose a
trade, stress-test the risk, and a Portfolio Manager issues the final call.

This document is the source-of-truth map of the runtime. File references point at
the actual modules so you can jump from a box in a diagram to the code.

---

## 1. The 10,000-foot view

```mermaid
flowchart LR
    IN["INPUT<br/>ticker + date + config"] --> ENG["TradingAgentsGraph<br/>(orchestrator)"]
    ENG --> GRAPH["LangGraph pipeline<br/>(agents as nodes)"]
    GRAPH --> OUT["OUTPUT<br/>5-tier rating + full report"]

    DATA[("Market data<br/>yfinance / Alpha Vantage")] -.feeds.-> GRAPH
    LLM[("LLM provider<br/>OpenAI / Anthropic / …")] -.reasons.-> GRAPH
    MEM[("Persistence<br/>~/.tradingagents")] -.remembers.-> ENG
    ENG -.writes.-> MEM
```

**One sentence:** the orchestrator builds a LangGraph state machine, streams the
shared state through a fixed sequence of LLM agents (each able to call
deterministic market-data tools), and the terminal node emits the decision —
which is also persisted for cross-run learning.

---

## 2. The four layers

```mermaid
flowchart TB
    subgraph L1["1 · Entry points"]
        CLI["cli/main.py<br/>interactive CLI"]
        MAIN["main.py<br/>programmatic"]
    end
    subgraph L2["2 · Orchestration  (tradingagents/graph/)"]
        TG["trading_graph.py<br/>TradingAgentsGraph"]
        SETUP["setup.py<br/>graph wiring"]
        COND["conditional_logic.py<br/>loop control"]
        PROP["propagation.py<br/>initial state"]
        SIG["signal_processing.py<br/>rating extract"]
        REF["reflection.py<br/>outcome learning"]
        CKPT["checkpointer.py<br/>resume"]
    end
    subgraph L3["3 · Agents  (tradingagents/agents/)"]
        AN["analysts/ ×4"]
        RE["researchers/ bull+bear"]
        MG["managers/ research+portfolio"]
        TR["trader/"]
        RK["risk_mgmt/ ×3"]
    end
    subgraph L4["4 · Infrastructure"]
        DF["dataflows/<br/>market data + routing"]
        LC["llm_clients/<br/>provider abstraction"]
        CFG["default_config.py<br/>config + env overrides"]
    end

    L1 --> TG
    TG --> SETUP --> L3
    TG --> PROP & SIG & REF & CKPT
    SETUP --> COND
    L3 --> DF
    L3 --> LC
    TG --> CFG
```

| Layer | Responsibility | Key files |
|---|---|---|
| **Entry** | Start a run (interactive or code) | `cli/main.py`, `main.py` |
| **Orchestration** | Build graph, manage state, persist, learn | `graph/trading_graph.py`, `graph/setup.py` |
| **Agents** | The LLM-powered reasoning roles | `agents/analysts/…`, `agents/researchers/…`, etc. |
| **Infrastructure** | Data, LLM providers, config | `dataflows/`, `llm_clients/`, `default_config.py` |

---

## 3. Inputs and outputs

### Inputs

```mermaid
flowchart LR
    subgraph ARGS ["Runtime args"]
        T["ticker e.g. AAPL"]
        D["trade_date e.g. 2024-05-10"]
        A["asset_type stock | crypto"]
    end
    subgraph CFG ["Config (default_config.py + .env)"]
        P["llm_provider"]
        M["deep_think_llm / quick_think_llm"]
        R["max_debate_rounds / max_risk_rounds"]
        TMP["temperature"]
        AS["selected_analysts"]
    end
    subgraph SEC ["Secrets (.env)"]
        K["ANTHROPIC_API_KEY / OPENAI_API_KEY / …"]
    end
    subgraph LIVE ["Live data"]
        Y["Yahoo Finance / Alpha Vantage"]
        S["StockTwits / Reddit / news"]
    end
```

### Outputs

| Output | Where | Description |
|---|---|---|
| **5-tier rating** | return value of `propagate()` | `Buy / Overweight / Hold / Underweight / Sell` |
| **Full decision** | `final_trade_decision` in state | PM's complete markdown reasoning |
| **All agent reports** | full-state JSON | `~/.tradingagents/logs/<TICKER>/TradingAgentsStrategy_logs/full_states_log_<date>.json` |
| **Decision memory** | markdown log | `~/.tradingagents/memory/trading_memory.md` (tagged `pending` until outcome known) |
| **Checkpoints** *(opt-in)* | SQLite | `~/.tradingagents/cache/checkpoints/<TICKER>.db` |

---

## 4. The full pipeline (the heart of the system)

This is the compiled LangGraph — verified against the live graph topology.

```mermaid
flowchart TD
    START([__start__]) --> MA[Market Analyst]

    %% Analyst stage: each analyst has a tool loop, then clears messages
    MA -->|tool calls?| MAT{has tool calls?}
    MAT -->|yes| TMA[tools_market] --> MA
    MAT -->|no| CMA[Msg Clear Market] --> SA[Sentiment Analyst]

    SA --> SAT{has tool calls?}
    SAT -->|yes| TSA[tools_social] --> SA
    SAT -->|no| CSA[Msg Clear Sentiment] --> NA[News Analyst]

    NA --> NAT{has tool calls?}
    NAT -->|yes| TNA[tools_news] --> NA
    NAT -->|no| CNA[Msg Clear News] --> FA[Fundamentals Analyst]

    FA --> FAT{has tool calls?}
    FAT -->|yes| TFA[tools_fundamentals] --> FA
    FAT -->|no| CFA[Msg Clear Fundamentals] --> BULL[Bull Researcher]

    %% Research debate loop
    BULL --> DBT{count ≥ 2×rounds?}
    DBT -->|no| BEAR[Bear Researcher] --> DBT2{count ≥ 2×rounds?}
    DBT2 -->|no| BULL
    DBT -->|yes| RM[Research Manager]
    DBT2 -->|yes| RM

    RM --> TRADER[Trader]

    %% Risk debate loop (3-way)
    TRADER --> AGG[Aggressive Analyst]
    AGG --> RKT{count ≥ 3×rounds?}
    RKT -->|no| CONS[Conservative Analyst] --> RKT2{count ≥ 3×rounds?}
    RKT2 -->|no| NEU[Neutral Analyst] --> RKT3{count ≥ 3×rounds?}
    RKT3 -->|no| AGG
    RKT -->|yes| PM[Portfolio Manager]
    RKT2 -->|yes| PM
    RKT3 -->|yes| PM

    PM --> END([__end__])

    classDef quick fill:#e8f0fe,stroke:#4285f4;
    classDef deep fill:#fce8e6,stroke:#ea4335;
    classDef tool fill:#e6f4ea,stroke:#34a853;
    class MA,SA,NA,FA,BULL,BEAR,TRADER,AGG,CONS,NEU quick;
    class RM,PM deep;
    class TMA,TSA,TNA,TFA tool;
```

**Legend:** blue = `quick_think_llm`, red = `deep_think_llm` (managers), green = deterministic tool nodes.

**Wiring lives in** `graph/setup.py`; **loop conditions in** `graph/conditional_logic.py`.

### Stage-by-stage

| Stage | Agents | Model | Output written to state |
|---|---|---|---|
| **1. Analysis** | Market, Sentiment, News, Fundamentals (sequential) | quick | `market_report`, `sentiment_report`, `news_report`, `fundamentals_report` |
| **2. Research debate** | Bull ↔ Bear | quick | `investment_debate_state` |
| **3. Research verdict** | Research Manager | **deep** | `investment_plan` |
| **4. Trade proposal** | Trader | quick | `trader_investment_plan` |
| **5. Risk debate** | Aggressive → Conservative → Neutral (cycle) | quick | `risk_debate_state` |
| **6. Final decision** | Portfolio Manager | **deep** | `final_trade_decision` |

**Loop termination (from `conditional_logic.py`):**
- Research debate ends when `count ≥ 2 × max_debate_rounds` (Bull + Bear = 2 speakers).
- Risk debate ends when `count ≥ 3 × max_risk_discuss_rounds` (3 speakers).
- With the defaults (`1`/`1`), that's one full round each.

---

## 5. The analyst tool loop (zoom-in)

Every analyst follows the identical ReAct-style pattern. The LLM decides which
tools to call; the tool node executes them; control returns to the LLM; when it
stops calling tools it writes its report and a "clear messages" node prunes the
tool chatter before the next analyst starts.

```mermaid
sequenceDiagram
    participant LG as LangGraph
    participant AN as Analyst (LLM)
    participant TN as Tool Node
    participant DF as dataflows

    LG->>AN: state (with instrument_context)
    AN->>AN: pick tools / indicators
    AN-->>LG: AIMessage with tool_calls
    LG->>TN: route (should_continue_X == "tools_X")
    TN->>DF: get_stock_data / get_indicators / get_verified_market_snapshot
    DF-->>TN: data (or NO_DATA_AVAILABLE sentinel)
    TN-->>AN: ToolMessage(s)
    AN-->>LG: final report (no tool_calls)
    LG->>LG: Msg Clear X (prune messages)
    LG->>LG: next analyst
```

> **Note (fixed in this repo):** the tools the analyst is *bound to* must also be
> *registered in its tool node* (`trading_graph._create_tool_nodes`). The market
> analyst's `get_verified_market_snapshot` was bound but unregistered, so the call
> failed at runtime — now fixed, with a contract test in
> `tests/test_tool_node_registration.py`.

---

## 6. The shared state (the data contract)

All agents read and write one `AgentState` object (`agents/utils/agent_states.py`).
Each agent appends its slice; nothing is hidden in side channels.

```mermaid
classDiagram
    class AgentState {
        +str company_of_interest
        +str asset_type
        +str instrument_context
        +str trade_date
        +str past_context
        +list messages
        +str market_report
        +str sentiment_report
        +str news_report
        +str fundamentals_report
        +InvestDebateState investment_debate_state
        +str investment_plan
        +str trader_investment_plan
        +RiskDebateState risk_debate_state
        +str final_trade_decision
    }
    note for AgentState "instrument_context = resolved identity (anti-hallucination).\npast_context = memory-log lessons injected at start.\nReports written by analysts; investment_plan by Research Mgr;\ntrader_investment_plan by Trader;\nfinal_trade_decision by Portfolio Mgr (terminal field)."
```

---

## 7. Data layer — how numbers reach the agents

Tools never hand raw broker symbols to a vendor and never let the LLM invent
values. The pipeline normalizes symbols, resolves identity, routes to a vendor
with fallback, and grounds exact numbers in a verified snapshot.

```mermaid
flowchart TD
    CALL["agent tool call e.g. get_stock_data('XAUUSD')"] --> NORM["symbol_utils.normalize_symbol<br/>XAUUSD → GC=F"]
    NORM --> ROUTE["interface.route_to_vendor"]
    ROUTE --> V1{primary vendor}
    V1 -->|ok| RES["data → ToolMessage"]
    V1 -->|rate-limited / error| V2{fallback vendor}
    V2 -->|ok| RES
    V2 -->|no data anywhere| SENT["NO_DATA_AVAILABLE sentinel<br/>'do not fabricate values'"]

    subgraph GROUND ["Grounding (deterministic, no LLM)"]
        ID["resolve_instrument_identity<br/>AAPL → Apple Inc., Technology"]
        SNAP["build_verified_market_snapshot<br/>ground-truth OHLCV + indicators"]
    end
    ID -.injected into every prompt.-> CALL
    SNAP -.source of truth for prices.-> CALL
```

| Concern | Mechanism | File |
|---|---|---|
| Broker → Yahoo symbols | `normalize_symbol` (forex `=X`, crypto `-USD`, futures aliases) | `dataflows/symbol_utils.py` |
| Vendor failover | `route_to_vendor` (yfinance ↔ Alpha Vantage) | `dataflows/interface.py` |
| "No data" vs "fabricate" | `NO_DATA_AVAILABLE` sentinel | `dataflows/interface.py` |
| Wrong-company hallucination | `resolve_instrument_identity` + `instrument_context` | `agents/utils/agent_utils.py` |
| Fabricated prices | `build_verified_market_snapshot` | `dataflows/market_data_validator.py` |

---

## 8. LLM layer — two models, many providers

```mermaid
flowchart LR
    CFG["config: llm_provider + models"] --> FAC["llm_clients/factory.py<br/>create_llm_client"]
    FAC --> OAI["openai_client.py<br/>(OpenAI + xAI/DeepSeek/Qwen/<br/>GLM/MiniMax/OpenRouter/Ollama)"]
    FAC --> ANT["anthropic_client.py"]
    FAC --> GGL["google_client.py"]
    FAC --> AZ["azure_client.py"]

    OAI & ANT & GGL & AZ --> DEEP["deep_think_llm<br/>Research Mgr + Portfolio Mgr"]
    OAI & ANT & GGL & AZ --> QUICK["quick_think_llm<br/>analysts + debaters + trader"]
```

- **`deep_think_llm`** — heavier reasoning, used by the two managers.
- **`quick_think_llm`** — cheaper/faster, used by analysts, researchers, trader, and risk debaters.
- Provider quirks (DeepSeek reasoning round-trip, MiniMax `reasoning_split`, capability-gated structured output) are isolated in client subclasses.

---

## 9. Persistence & the cross-run learning loop

TradingAgents gets smarter across runs. A decision is logged as `pending`; on the
**next run of the same ticker**, the realized return is fetched, a one-paragraph
reflection is generated, and recent lessons are injected into the Portfolio
Manager's prompt.

```mermaid
flowchart TD
    subgraph R1 ["Run 1 — AAPL @ date1"]
        P1["propagate()"] --> D1["decision"]
        D1 --> LOG1["memory log:<br/>[date1 | AAPL | Hold | pending]"]
    end
    subgraph R2 ["Run 2 — AAPL @ date2 (later)"]
        P2["propagate() start"] --> RESOLVE["_resolve_pending_entries<br/>fetch return vs benchmark"]
        RESOLVE --> REFL["Reflector.reflect_on_final_decision<br/>2-4 sentence lesson"]
        REFL --> UPD["memory log updated:<br/>[date1 | AAPL | Hold | +3.2% | +0.8% | 5d] + REFLECTION"]
        UPD --> INJ["get_past_context →<br/>injected into Portfolio Manager prompt"]
        INJ --> RUN2["graph runs with memory of run 1"]
    end
    LOG1 -.read next run.-> RESOLVE
```

| Feature | Trigger | File |
|---|---|---|
| Decision log | always on | `agents/utils/memory.py` |
| Outcome + reflection | next same-ticker run | `graph/reflection.py`, `trading_graph._resolve_pending_entries` |
| Benchmark (alpha) | per-market suffix map (SPY, ^N225, …) | `trading_graph._resolve_benchmark` |
| Checkpoint resume | `--checkpoint` flag | `graph/checkpointer.py` |

---

## 10. Full request lifecycle (sequence)

```mermaid
sequenceDiagram
    actor User
    participant TG as TradingAgentsGraph
    participant MEM as MemoryLog
    participant ID as Identity/Data
    participant G as LangGraph
    participant PM as Portfolio Manager
    participant SIG as SignalProcessor

    User->>TG: propagate("AAPL", "2024-05-10")
    TG->>MEM: _resolve_pending_entries (reflect on past AAPL runs)
    TG->>MEM: get_past_context  (lessons for the prompt)
    TG->>ID: resolve_instrument_context (Apple Inc., Technology)
    TG->>G: create_initial_state + stream/invoke
    Note over G: Analysts → Research debate → Trader → Risk debate
    G->>PM: synthesize risk debate
    PM-->>G: final_trade_decision (structured → markdown)
    G-->>TG: final_state
    TG->>TG: _log_state → full_states_log_<date>.json
    TG->>MEM: store_decision (pending)
    TG->>SIG: process_signal(final_trade_decision)
    SIG-->>TG: "Hold"
    TG-->>User: (final_state, "Hold")
```

---

## 11. Module map (where to look)

```
tradingagents/
├── graph/
│   ├── trading_graph.py      # orchestrator: propagate(), tool nodes, persistence, reflection
│   ├── setup.py              # builds & wires the LangGraph (nodes + edges)
│   ├── conditional_logic.py  # tool loops + debate termination
│   ├── propagation.py        # initial AgentState
│   ├── signal_processing.py  # extract 5-tier rating
│   ├── reflection.py         # outcome → lesson
│   └── checkpointer.py       # SQLite resume
├── agents/
│   ├── analysts/             # market, sentiment, news, fundamentals
│   ├── researchers/          # bull, bear
│   ├── managers/             # research_manager (deep), portfolio_manager (deep)
│   ├── trader/               # trader
│   ├── risk_mgmt/            # aggressive, conservative, neutral
│   └── utils/                # agent_states, agent_utils (tools), memory, schemas, structured
├── dataflows/                # symbol_utils, interface (routing), market_data_validator, vendors
├── llm_clients/              # factory + per-provider clients + capabilities
└── default_config.py         # config + TRADINGAGENTS_* env overrides
```

---

## 12. Run it yourself

```python
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

config = DEFAULT_CONFIG.copy()
config["llm_provider"] = "anthropic"
config["deep_think_llm"] = "claude-sonnet-4-6"   # managers
config["quick_think_llm"] = "claude-haiku-4-5"   # analysts/debaters
config["max_debate_rounds"] = 1
config["max_risk_discuss_rounds"] = 1

ta = TradingAgentsGraph(
    selected_analysts=["market", "social", "news", "fundamentals"],
    debug=True,                # stream each agent's output live
    config=config,
)
final_state, decision = ta.propagate("AAPL", "2024-05-10")
print(decision)              # -> "Hold"  (the 5-tier rating)
```

Tip: start with `selected_analysts=["market"]` to watch a minimal run (≈3 active
nodes) before scaling to the full four.

> **Disclaimer:** TradingAgents is a research framework, not financial advice. It
> trades against a simulated exchange and its output varies run-to-run by design
> (see the README "Reproducibility" section).
