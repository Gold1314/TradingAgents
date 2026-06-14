import Foundation

/// Response of `POST /api/runs` (`start_run()`). The server returns one of two
/// shapes discriminated by `cached`:
///   * `{"cached": false, "run_id": ..., "nodes": [...]}`  — a fresh run started
///   * `{"cached": true,  "run": {...}}`                   — a 60-min cache hit
enum StartRunResponse: Decodable {
    case started(runID: String, nodes: [String])
    case cached(CachedRun)

    private enum CodingKeys: String, CodingKey {
        case cached
        case runID = "run_id"
        case nodes
        case run
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let isCached = (try? container.decode(Bool.self, forKey: .cached)) ?? false
        if isCached {
            let run = try container.decode(CachedRun.self, forKey: .run)
            self = .cached(run)
        } else {
            let runID = try container.decode(String.self, forKey: .runID)
            let nodes = try container.decode([String].self, forKey: .nodes)
            self = .started(runID: runID, nodes: nodes)
        }
    }
}

/// A persisted run served from the 60-minute cache. Shape mirrors what
/// `renderCached()` consumes in `web/static/index.html` (from `db.get_recent_run`).
struct CachedRun: Decodable, Equatable {
    let ticker: String
    let tradeDate: String
    let assetType: String?
    let decision: String?
    let finalContent: String?
    let identity: String?
    let createdAt: String?
    let agents: [CachedAgentOutput]

    enum CodingKeys: String, CodingKey {
        case ticker
        case tradeDate = "trade_date"
        case assetType = "asset_type"
        case decision
        case finalContent = "final_content"
        case identity
        case createdAt = "created_at"
        case agents
    }
}

/// One persisted agent report inside a cached run (`{seq, agent, content}`).
struct CachedAgentOutput: Decodable, Equatable, Identifiable {
    let seq: Int
    let agent: String
    let content: String

    var id: Int { seq }
}

/// Response of `GET /api/runs/{run_id}` (`run_status()`), used for session restore.
struct RunStatus: Decodable, Equatable {
    let runID: String
    let active: Bool
    let finished: Bool
    let nodes: [String]
    let events: Int

    enum CodingKeys: String, CodingKey {
        case runID = "run_id"
        case active
        case finished
        case nodes
        case events
    }
}
