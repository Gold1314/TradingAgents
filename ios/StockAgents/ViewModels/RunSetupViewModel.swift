import Foundation

/// Drives `RunSetupView`: loads form defaults from `GET /api/config`, holds the
/// editable form state, validates it (mirroring the server's 400s in
/// `start_run`), and starts a run via `POST /api/runs`.
@MainActor
final class RunSetupViewModel: ObservableObject {
    enum LoadState: Equatable {
        case idle
        case loading
        case loaded
        case failed(String)
    }

    // Config / bootstrap state.
    @Published private(set) var configState: LoadState = .idle
    @Published private(set) var config: RunConfig?

    // Editable form fields (mirror the web form in index.html).
    @Published var ticker: String = "NVDA"
    @Published var tradeDate: Date = Date()
    @Published var assetType: AssetType = .stock
    /// Wire keys of selected analysts (e.g. ["market","social",...]).
    @Published var selectedAnalysts: Set<String> = ["market", "social", "news", "fundamentals"]
    // Advanced disclosure.
    @Published var provider: String = ""
    @Published var deepModel: String = ""
    @Published var quickModel: String = ""
    @Published var maxDebateRounds: String = ""
    @Published var maxRiskRounds: String = ""

    // Start-run state.
    @Published private(set) var isStarting = false
    @Published var startError: String?

    private let api: APIClient

    init(api: APIClient) {
        self.api = api
    }

    /// Analysts to render as toggles, in stable display order.
    var availableAnalysts: [(key: String, display: String)] {
        guard let config else {
            return RunConfig.analystOrder.map { ($0, AgentNode.analystDisplayName[$0] ?? $0) }
        }
        let ordered = config.analysts.isEmpty ? RunConfig.analystOrder : config.analysts
        return ordered.map { key in
            (key, config.analystDisplay[key] ?? AgentNode.analystDisplayName[key] ?? key)
        }
    }

    var canStart: Bool {
        !ticker.trimmingCharacters(in: .whitespaces).isEmpty && !selectedAnalysts.isEmpty && !isStarting
    }

    func loadConfig() async {
        configState = .loading
        do {
            let config = try await api.fetchConfig()
            self.config = config
            applyDefaults(from: config)
            configState = .loaded
        } catch {
            // Non-fatal: the form still works with built-in defaults.
            configState = .failed((error as? APIError)?.errorDescription ?? error.localizedDescription)
        }
    }

    private func applyDefaults(from config: RunConfig) {
        // Provider/model fields stay blank (placeholder), matching the web form:
        // the server applies its own defaults when these are omitted.
        if let rounds = config.maxDebateRounds { maxDebateRounds = String(rounds) }
        if let rounds = config.maxRiskRounds { maxRiskRounds = String(rounds) }
        // Keep current analyst selection if it intersects the server's set.
        let serverKeys = Set(config.analysts.isEmpty ? RunConfig.analystOrder : config.analysts)
        if selectedAnalysts.isDisjoint(with: serverKeys) {
            selectedAnalysts = serverKeys
        }
    }

    func toggleAnalyst(_ key: String) {
        if selectedAnalysts.contains(key) {
            selectedAnalysts.remove(key)
        } else {
            selectedAnalysts.insert(key)
        }
    }

    /// Build the `RunRequest` from the current form state. Analysts are ordered
    /// to match the server's canonical analyst order so the planned node list is
    /// deterministic.
    func makeRequest(force: Bool = false) -> RunRequest {
        let orderedAnalysts = (config?.analysts.isEmpty == false ? config!.analysts : RunConfig.analystOrder)
            .filter { selectedAnalysts.contains($0) }
        return RunRequest(
            ticker: ticker.trimmingCharacters(in: .whitespaces).uppercased(),
            tradeDate: Self.dateString(tradeDate),
            analysts: orderedAnalysts,
            assetType: assetType.rawValue,
            provider: provider.trimmedOrNil,
            deepModel: deepModel.trimmedOrNil,
            quickModel: quickModel.trimmedOrNil,
            maxDebateRounds: Int(maxDebateRounds.trimmingCharacters(in: .whitespaces)),
            maxRiskRounds: Int(maxRiskRounds.trimmingCharacters(in: .whitespaces)),
            force: force
        )
    }

    /// Start a run. Returns the response so the view can route to the session
    /// screen (fresh) or render a cached run inline.
    func start(force: Bool = false) async -> StartRunResponse? {
        guard canStart else {
            startError = "Enter a ticker and pick at least one analyst."
            return nil
        }
        isStarting = true
        startError = nil
        defer { isStarting = false }
        do {
            return try await api.startRun(makeRequest(force: force))
        } catch {
            startError = (error as? APIError)?.errorDescription ?? error.localizedDescription
            return nil
        }
    }

    static func dateString(_ date: Date) -> String {
        ChartPayload.dateFormatter.string(from: date)
    }
}

private extension String {
    var trimmedOrNil: String? {
        let trimmed = trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }
}
