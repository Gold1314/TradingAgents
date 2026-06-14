import Foundation

/// Persists the in-flight run handle so the app can restore a streaming run on
/// relaunch — the native equivalent of the web app's `sessionStorage` +
/// `tryRestoreActiveRun()`. On launch the app reads this, calls
/// `GET /api/runs/{id}`, and reconnects the SSE stream (plan §3.2).
struct ActiveRunStore {
    private static let key = "stockagents.activeRun"
    private let defaults: UserDefaults

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
    }

    /// What we need to fully reconstruct + reconnect a run after relaunch.
    struct ActiveRun: Codable, Equatable {
        let runID: String
        let request: RunRequest
        let nodes: [String]
    }

    func save(_ run: ActiveRun) {
        guard let data = try? JSONEncoder().encode(run) else { return }
        defaults.set(data, forKey: ActiveRunStore.key)
    }

    func load() -> ActiveRun? {
        guard let data = defaults.data(forKey: ActiveRunStore.key) else { return nil }
        return try? JSONDecoder().decode(ActiveRun.self, from: data)
    }

    func clear() {
        defaults.removeObject(forKey: ActiveRunStore.key)
    }
}
