import Foundation

/// Process-wide dependency container.
///
/// Owns the configurable backend base URL and the long-lived service objects.
/// The base URL is resolved (in priority order) from a user override persisted
/// in `UserDefaults` (set from the Settings screen), then the build-time
/// `STOCKAGENTS_BASE_URL` Info.plist key, then a localhost fallback. No URLs or
/// secrets are hardcoded in source per the project rules.
@MainActor
final class AppEnvironment: ObservableObject {
    static let baseURLDefaultsKey = "stockagents.baseURL"

    @Published private(set) var baseURL: URL

    let auth: AuthService
    let api: APIClient
    let stream: RunStreamClient
    let activeRunStore: ActiveRunStore

    init() {
        let resolved = AppEnvironment.resolveBaseURL()
        self.baseURL = resolved

        let keychain = KeychainStore(service: "com.stockagents.app")
        let auth = AuthService(keychain: keychain)
        self.auth = auth
        self.api = APIClient(baseURL: resolved, auth: auth)
        self.stream = RunStreamClient(auth: auth)
        self.activeRunStore = ActiveRunStore()
    }

    // MARK: - View model factories

    func makeSetupViewModel() -> RunSetupViewModel {
        RunSetupViewModel(api: api)
    }

    /// Login screen VM (Supabase sign-in). The auth gate shows `LoginView` with
    /// this when `auth.state == .signedOut`.
    func makeLoginViewModel() -> LoginViewModel {
        LoginViewModel(auth: auth)
    }

    func makeSessionViewModel() -> RunSessionViewModel {
        RunSessionViewModel(api: api, stream: stream, baseURL: baseURL, activeRunStore: activeRunStore)
    }

    /// Robinhood connect screen VM. A fresh `BrokerConnectService` is created per
    /// flow so each `ASWebAuthenticationSession` has a clean presentation anchor.
    func makeBrokerConnectViewModel() -> BrokerConnectViewModel {
        BrokerConnectViewModel(api: api, connectService: BrokerConnectService())
    }

    /// Order-ticket VM for a finished run's proposed trade (plan §5.3).
    func makeOrderTicketViewModel(runID: String, trade: TradeEvent) -> OrderTicketViewModel {
        OrderTicketViewModel(runID: runID, trade: trade, api: api, biometrics: BiometricAuthenticator())
    }

    /// Update the backend base URL at runtime (from Settings) and rewire the
    /// REST client. SSE clients read `api.baseURL` lazily when they start, so a
    /// new run after a change uses the new host.
    func updateBaseURL(_ url: URL) {
        UserDefaults.standard.set(url.absoluteString, forKey: AppEnvironment.baseURLDefaultsKey)
        baseURL = url
        api.baseURL = url
    }

    private static func resolveBaseURL() -> URL {
        if let override = UserDefaults.standard.string(forKey: baseURLDefaultsKey),
           let url = URL(string: override) {
            return url
        }
        if let configured = Bundle.main.object(forInfoDictionaryKey: "STOCKAGENTS_BASE_URL") as? String,
           let url = URL(string: configured) {
            return url
        }
        return URL(string: "http://localhost:8000")!
    }
}
