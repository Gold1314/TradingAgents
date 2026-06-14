import Foundation

/// Owns one active analysis: it consumes the decoded SSE stream and projects it
/// into observable UI state (planned nodes, per-agent cards, verdict, economics,
/// progress, account/trade). This is the Swift port of `handleEvent()` and its
/// helpers in `web/static/index.html`, plus the chart fetch and session restore.
@MainActor
final class RunSessionViewModel: ObservableObject {
    enum Phase: Equatable {
        case idle
        case connecting
        case streaming
        case finished
        case failed(String)
    }

    // Run identity.
    @Published private(set) var runID: String?
    @Published private(set) var request: RunRequest?

    // Stream / status.
    @Published private(set) var phase: Phase = .idle
    @Published private(set) var statusMessage: String = ""
    @Published private(set) var isCached = false
    @Published private(set) var cacheCreatedAt: String?

    // Pipeline + feed.
    @Published private(set) var plannedNodes: [String] = []
    @Published private(set) var agents: [String: AgentCardState] = [:]
    @Published private(set) var activeNode: String?
    @Published private(set) var completedNodes: Set<String> = []
    @Published private(set) var identity: String?

    // Verdict.
    @Published private(set) var decision: String?
    @Published private(set) var verdict: Verdict?
    @Published private(set) var rationale: String?

    // Economics.
    @Published private(set) var usageByNode: [String: UsageEvent] = [:]
    @Published private(set) var usageTotals: UsageSummaryEvent.UsageTotals?

    // Robinhood (decoded for completeness; trading UI deferred).
    @Published private(set) var account: AccountEvent?
    @Published private(set) var lastTrade: TradeEvent?

    // Price chart.
    @Published private(set) var chart: ChartPayload?
    @Published private(set) var chartError: String?

    private let api: APIClient
    private let stream: RunStreamClient
    private let baseURL: URL
    private let activeRunStore: ActiveRunStore
    private var streamTask: Task<Void, Never>?
    private var lastEventID: Int?
    private var chartLoaded = false

    init(api: APIClient, stream: RunStreamClient, baseURL: URL, activeRunStore: ActiveRunStore) {
        self.api = api
        self.stream = stream
        self.baseURL = baseURL
        self.activeRunStore = activeRunStore
    }

    /// Agent cards in planned pipeline order (only those we've seen or planned).
    var orderedAgents: [AgentCardState] {
        plannedNodes.compactMap { agents[$0] }
    }

    /// Stage rollup: planned/completed/active per stage for the stepper.
    func stageProgress(_ stage: Stage) -> (total: Int, completed: Int, isActive: Bool) {
        let inStage = plannedNodes.filter { AgentNode.metadata(for: $0).stage == stage }
        let completed = inStage.filter { completedNodes.contains($0) }.count
        let isActive = activeNode.map { AgentNode.metadata(for: $0).stage == stage } ?? false
        return (inStage.count, completed, isActive)
    }

    // MARK: - Lifecycle

    /// Begin a fresh run (from `POST /api/runs` → `.started`).
    func startStreaming(runID: String, nodes: [String], request: RunRequest) {
        self.runID = runID
        self.request = request
        self.plannedNodes = nodes
        self.isCached = false
        seedPlannedAgents(nodes)
        activeRunStore.save(.init(runID: runID, request: request, nodes: nodes))
        connect(lastEventID: nil)
        loadChartIfNeeded()
    }

    /// Restore a run on relaunch: confirm it still exists via `GET /api/runs/{id}`,
    /// then reconnect the SSE stream (full replay on a fresh connection).
    func restore(runID: String, request: RunRequest, fallbackNodes: [String]) async {
        self.runID = runID
        self.request = request
        phase = .connecting
        do {
            let status = try await api.runStatus(runID: runID)
            plannedNodes = status.nodes.isEmpty ? fallbackNodes : status.nodes
            seedPlannedAgents(plannedNodes)
            connect(lastEventID: nil) // fresh connect replays from the start
            loadChartIfNeeded()
        } catch {
            activeRunStore.clear()
            phase = .failed((error as? APIError)?.errorDescription ?? error.localizedDescription)
        }
    }

    /// Populate from a 60-minute cache hit (`POST /api/runs` → `.cached`).
    func renderCached(_ run: CachedRun) {
        isCached = true
        cacheCreatedAt = run.createdAt
        request = RunRequest(
            ticker: run.ticker,
            tradeDate: run.tradeDate,
            assetType: run.assetType ?? "stock"
        )
        let sorted = run.agents.sorted { $0.seq < $1.seq }
        var nodes: [String] = []
        for output in sorted where !nodes.contains(output.agent) { nodes.append(output.agent) }
        plannedNodes = nodes
        seedPlannedAgents(nodes)
        for output in sorted {
            agents[output.agent] = AgentCardState(node: output.agent, status: .done, content: output.content)
            completedNodes.insert(output.agent)
        }
        identity = run.identity
        applyVerdict(decision: run.decision ?? "Hold", content: run.finalContent ?? "")
        statusMessage = "from cache"
        phase = .finished
        loadChartIfNeeded()
    }

    func stop() {
        streamTask?.cancel()
        streamTask = nil
    }

    // MARK: - Streaming

    private func connect(lastEventID: Int?) {
        streamTask?.cancel()
        phase = .connecting
        let runID = self.runID
        guard let runID else { return }
        let eventStream = stream.events(baseURL: baseURL, runID: runID, lastEventID: lastEventID)
        streamTask = Task { [weak self] in
            do {
                for try await streamed in eventStream {
                    guard let self else { return }
                    self.lastEventID = streamed.id ?? self.lastEventID
                    self.apply(streamed.event)
                }
                // Stream finished without throwing.
                if let self, self.phase == .streaming || self.phase == .connecting {
                    self.phase = .finished
                }
            } catch is CancellationError {
                // expected on stop()
            } catch {
                self?.phase = .failed((error as? APIError)?.errorDescription ?? error.localizedDescription)
            }
        }
    }

    // MARK: - Event projection (mirrors handleEvent in index.html)

    private func apply(_ event: AgentEvent) {
        if phase == .connecting { phase = .streaming }
        switch event {
        case .nodes(let payload):
            plannedNodes = payload.nodes
            seedPlannedAgents(payload.nodes)

        case .status(let payload):
            statusMessage = payload.message

        case .memory:
            break // lifecycle only; silent in the UI (web parity)

        case .identity(let payload):
            identity = payload.content

        case .account(let payload):
            account = payload

        case .agent(let payload):
            upsertAgent(node: payload.node, status: payload.status, content: payload.content)

        case .graph:
            break // tool-node activity; intentionally ignored (web parity)

        case .progress(let payload):
            activeNode = payload.active
            completedNodes.formUnion(payload.completed)

        case .usage(let payload):
            usageByNode[payload.node] = payload

        case .usageSummary(let payload):
            for (node, entry) in payload.nodes { usageByNode[node] = entry }
            usageTotals = payload.totals

        case .final(let payload):
            applyVerdict(decision: payload.decision, content: payload.content)

        case .trade(let payload):
            lastTrade = payload

        case .error(let payload):
            phase = .failed(payload.message)

        case .done:
            phase = .finished
            statusMessage = statusMessage.isEmpty ? "complete" : statusMessage
            activeRunStore.clear()

        case .unknown:
            break // forward-compatible: ignore unrecognized event types
        }
    }

    private func seedPlannedAgents(_ nodes: [String]) {
        for node in nodes where agents[node] == nil {
            agents[node] = AgentCardState(node: node, status: .running, content: nil)
        }
    }

    private func upsertAgent(node: String, status: AgentReportEvent.AgentStatus, content: String?) {
        var card = agents[node] ?? AgentCardState(node: node, status: status, content: nil)
        card.status = status
        if let content, !content.isEmpty { card.content = content }
        agents[node] = card
        if status == .done {
            completedNodes.insert(node)
        }
    }

    private func applyVerdict(decision: String, content: String) {
        self.decision = decision
        self.verdict = Verdict(decision: decision)
        self.rationale = content
    }

    // MARK: - Chart

    private func loadChartIfNeeded() {
        guard !chartLoaded, let request else { return }
        chartLoaded = true
        Task { [weak self] in
            guard let self else { return }
            do {
                let payload = try await self.api.fetchChart(
                    ticker: request.ticker,
                    tradeDate: request.tradeDate,
                    assetType: request.assetType
                )
                self.chart = payload
            } catch {
                self.chartError = (error as? APIError)?.errorDescription ?? error.localizedDescription
            }
        }
    }
}
