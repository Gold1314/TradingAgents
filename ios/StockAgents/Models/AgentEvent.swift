import Foundation

/// The full SSE event union streamed by `GET /api/runs/{run_id}/events`.
///
/// Each SSE `data:` frame is a JSON object whose `type` field discriminates the
/// payload. This enum mirrors every event `manager.emit(...)` produces in
/// `web/server.py` and that `handleEvent()` consumes in `index.html`.
///
/// Decoding strategy (per plan §3.3): read `type` first, then decode the
/// matching associated payload. The `switch` in `init(from:)` is written to be
/// exhaustive over `EventType` so a newly added server event type forces a
/// compile-time decision here (applying the repo's exhaustive-switch rule to
/// Swift).
enum AgentEvent: Decodable, Equatable {
    case nodes(NodesEvent)
    case status(StatusEvent)
    case memory(MemoryEvent)
    case identity(IdentityEvent)
    case account(AccountEvent)
    case agent(AgentReportEvent)
    case graph(GraphEvent)
    case progress(ProgressEvent)
    case usage(UsageEvent)
    case usageSummary(UsageSummaryEvent)
    case final(FinalEvent)
    case trade(TradeEvent)
    case error(ErrorEvent)
    case done
    /// Forward-compatibility escape hatch: an unrecognized `type` is preserved
    /// rather than throwing, so the stream never dies on a benign new event.
    case unknown(String)

    /// Discriminator values exactly as emitted by the backend.
    enum EventType: String, Decodable {
        case nodes
        case status
        case memory
        case identity
        case account
        case agent
        case graph
        case progress
        case usage
        case usageSummary = "usage_summary"
        case final
        case trade
        case error
        case done
    }

    private enum CodingKeys: String, CodingKey {
        case type
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let rawType = try container.decode(String.self, forKey: .type)
        guard let type = EventType(rawValue: rawType) else {
            self = .unknown(rawType)
            return
        }
        let single = decoder
        switch type {
        case .nodes:
            self = .nodes(try NodesEvent(from: single))
        case .status:
            self = .status(try StatusEvent(from: single))
        case .memory:
            self = .memory(try MemoryEvent(from: single))
        case .identity:
            self = .identity(try IdentityEvent(from: single))
        case .account:
            self = .account(try AccountEvent(from: single))
        case .agent:
            self = .agent(try AgentReportEvent(from: single))
        case .graph:
            self = .graph(try GraphEvent(from: single))
        case .progress:
            self = .progress(try ProgressEvent(from: single))
        case .usage:
            self = .usage(try UsageEvent(from: single))
        case .usageSummary:
            self = .usageSummary(try UsageSummaryEvent(from: single))
        case .final:
            self = .final(try FinalEvent(from: single))
        case .trade:
            self = .trade(try TradeEvent(from: single))
        case .error:
            self = .error(try ErrorEvent(from: single))
        case .done:
            self = .done
        }
    }
}

// MARK: - Payloads

/// `nodes` — planned agent order; build the stepper/pipeline.
struct NodesEvent: Decodable, Equatable {
    let nodes: [String]
}

/// `status` — status pill text.
struct StatusEvent: Decodable, Equatable {
    let message: String
}

/// `memory` — cross-run memory lifecycle (mostly silent in the UI).
struct MemoryEvent: Decodable, Equatable {
    let phase: String
    let status: String?
    let resolved: Int?
    let hasContext: Bool?

    enum CodingKeys: String, CodingKey {
        case phase
        case status
        case resolved
        case hasContext = "has_context"
    }
}

/// `identity` — resolved instrument identity block.
struct IdentityEvent: Decodable, Equatable {
    let content: String
}

/// `account` — Robinhood account grounding (decoded for completeness; trading
/// UI deferred). `position` is intentionally loosely typed (broker-dependent).
struct AccountEvent: Decodable, Equatable {
    let connected: Bool
    let buyingPower: Double?
    let portfolioValue: Double?
    let position: JSONValue?
    let message: String?

    enum CodingKeys: String, CodingKey {
        case connected
        case buyingPower = "buying_power"
        case portfolioValue = "portfolio_value"
        case position
        case message
    }
}

/// `agent` — one agent card; `content` carries the markdown report when done.
struct AgentReportEvent: Decodable, Equatable {
    let node: String
    let status: AgentStatus
    let content: String?

    enum AgentStatus: String, Decodable, Equatable {
        case running
        case done
    }
}

/// `graph` — tool-node activity (UI ignores; modeled for completeness).
struct GraphEvent: Decodable, Equatable {
    let node: String
    let status: String
    let tools: [String]
}

/// `progress` — stepper progress sync.
struct ProgressEvent: Decodable, Equatable {
    let active: String?
    let completed: [String]
    let node: String
    let status: String?
}

/// `usage` — per-agent token/cost (live economics). Also reused as the per-node
/// entry shape inside `usage_summary.nodes`.
struct UsageEvent: Decodable, Equatable {
    let node: String
    let inputTokens: Int
    let outputTokens: Int
    let totalTokens: Int
    let calls: Int
    let model: String?
    let cost: Double?

    enum CodingKeys: String, CodingKey {
        case node
        case inputTokens = "input_tokens"
        case outputTokens = "output_tokens"
        case totalTokens = "total_tokens"
        case calls
        case model
        case cost
    }
}

/// `usage_summary` — authoritative end-of-run economics.
struct UsageSummaryEvent: Decodable, Equatable {
    let nodes: [String: UsageEvent]
    let totals: UsageTotals

    struct UsageTotals: Decodable, Equatable {
        let inputTokens: Int
        let outputTokens: Int
        let totalTokens: Int
        let cost: Double?

        enum CodingKeys: String, CodingKey {
            case inputTokens = "input_tokens"
            case outputTokens = "output_tokens"
            case totalTokens = "total_tokens"
            case cost
        }
    }
}

/// `final` — the 5-tier verdict + PM rationale.
struct FinalEvent: Decodable, Equatable {
    let decision: String
    let content: String
}

/// `trade` — Robinhood order lifecycle (proposed/placed/dry_run/skipped/error).
/// Decoded for contract completeness; the order ticket UI + Face ID gate are a
/// later milestone (see README / plan §5.3).
struct TradeEvent: Decodable, Equatable {
    let status: String
    let tradeMode: String?
    let canPlaceRealOrders: Bool?
    let dryRun: Bool?
    let orderID: String?
    let message: String?
    let intent: OrderIntent?

    enum CodingKeys: String, CodingKey {
        case status
        case tradeMode = "trade_mode"
        case canPlaceRealOrders = "can_place_real_orders"
        case dryRun = "dry_run"
        case orderID = "order_id"
        case message
        case intent
    }
}

/// `error` — run failed.
struct ErrorEvent: Decodable, Equatable {
    let message: String
    let trace: String?
}
