import Foundation

/// Real Supabase Auth (GoTrue) sign-in for the multi-user backend (plan §3.4.1).
///
/// The app authenticates **directly against Supabase GoTrue's REST API** with
/// `URLSession` (zero third-party deps — the `supabase-swift` SPM package is
/// intentionally NOT added). A successful sign-in yields a normal Supabase user
/// access token + refresh token; the access token is injected as
/// `Authorization: Bearer <token>` on every `APIClient` / `RunStreamClient`
/// request, and the backend (`web/mobile/auth.py`, `MOBILE_AUTH_MODE=supabase`)
/// validates it (signature + exp + aud + iss).
///
/// Tokens are persisted in the **Keychain** (`KeychainStore`,
/// `kSecAttrAccessibleWhenUnlockedThisDeviceOnly`). The bearer header wiring in
/// the networking layer is unchanged; this type just fills in the real sign-in,
/// refresh, and sign-out flows and exposes an observable `state` the UI drives.
@MainActor
final class AuthService: ObservableObject {
    private static let tokenKey = "auth.accessToken"
    private static let refreshKey = "auth.refreshToken"
    private static let expiryKey = "auth.expiresAt"
    private static let userIDKey = "auth.userID"
    private static let emailKey = "auth.email"

    private let keychain: KeychainStore
    private let config: SupabaseConfig?
    private let session: URLSession
    private let decoder = JSONDecoder()
    private let encoder = JSONEncoder()

    /// Observable sign-in state for the UI (gate login vs. main app).
    @Published private(set) var state: AuthState

    /// Coalesces concurrent refreshes (e.g. several 401s in flight at once) so
    /// only one refresh-token exchange runs and all callers await its result.
    private var refreshTask: Task<Bool, Never>?

    init(keychain: KeychainStore, config: SupabaseConfig? = SupabaseConfig.resolve(), session: URLSession = .shared) {
        self.keychain = keychain
        self.config = config
        self.session = session
        self.state = AuthService.restoreState(from: keychain)
    }

    /// Whether a Supabase project URL + anon key are configured. When false the
    /// login screen shows setup guidance instead of a doomed sign-in attempt.
    var isConfigured: Bool { config != nil }

    var isSignedIn: Bool {
        if case .signedIn = state { return true }
        return false
    }

    // MARK: - Token accessors (read by the networking layer)

    /// Current bearer token, if any.
    var accessToken: String? {
        keychain.get(AuthService.tokenKey)
    }

    /// Header value injected on every authenticated request, or nil when signed
    /// out (the legacy/open endpoints simply ignore its absence).
    var authorizationHeader: String? {
        accessToken.map { "Bearer \($0)" }
    }

    // MARK: - Sign in

    /// `POST {SUPABASE_URL}/auth/v1/token?grant_type=password` with the anon key
    /// as the `apikey` header. Persists the resulting session and flips `state`
    /// to `.signedIn`.
    func signIn(email: String, password: String) async throws {
        guard let config else { throw AuthError.notConfigured }
        let body = try encoder.encode(["email": email, "password": password])
        let session = try await requestToken(grantType: "password", body: body, config: config)
        persist(session)
        state = .signedIn(session.authUser)
    }

    // MARK: - Refresh

    /// Exchange the stored refresh token for a fresh session (called on a 401).
    /// Throws on failure so callers can distinguish a recoverable refresh from a
    /// hard sign-out. Most callers should use `attemptRefresh()` instead.
    func refresh() async throws {
        guard let config else { throw AuthError.notConfigured }
        guard let refreshToken = keychain.get(AuthService.refreshKey) else {
            throw AuthError.noRefreshToken
        }
        let body = try encoder.encode(["refresh_token": refreshToken])
        let session = try await requestToken(grantType: "refresh_token", body: body, config: config)
        persist(session)
        state = .signedIn(session.authUser)
    }

    /// Best-effort, coalesced refresh used by `APIClient` on a 401. Returns
    /// `true` if a fresh access token is now stored. Concurrent callers share a
    /// single in-flight refresh.
    func attemptRefresh() async -> Bool {
        if let refreshTask { return await refreshTask.value }
        let task = Task { () -> Bool in
            do {
                try await self.refresh()
                return true
            } catch {
                return false
            }
        }
        refreshTask = task
        let result = await task.value
        refreshTask = nil
        return result
    }

    // MARK: - Sign out

    /// Clear the local session and flip `state` to `.signedOut`. Also makes a
    /// best-effort GoTrue `logout` call to revoke the refresh token server-side
    /// (failures are ignored — local sign-out always succeeds).
    func signOut() {
        let token = accessToken
        clearTokens()
        state = .signedOut
        if let config, let token {
            Task { await revokeRemotely(token: token, config: config) }
        }
    }

    // MARK: - Persistence

    private func persist(_ session: SupabaseSession) {
        keychain.set(session.accessToken, for: AuthService.tokenKey)
        keychain.set(session.refreshToken, for: AuthService.refreshKey)
        keychain.set(String(session.expiryEpoch), for: AuthService.expiryKey)
        if let user = session.user {
            keychain.set(user.id, for: AuthService.userIDKey)
            if let email = user.email {
                keychain.set(email, for: AuthService.emailKey)
            }
        }
    }

    private func clearTokens() {
        keychain.delete(AuthService.tokenKey)
        keychain.delete(AuthService.refreshKey)
        keychain.delete(AuthService.expiryKey)
        keychain.delete(AuthService.userIDKey)
        keychain.delete(AuthService.emailKey)
    }

    /// Restore sign-in state on launch. A stored access *or* refresh token is
    /// treated as signed-in: an expired access token is recovered by the
    /// 401→refresh path on the first authenticated call.
    private static func restoreState(from keychain: KeychainStore) -> AuthState {
        let hasAccess = keychain.get(tokenKey) != nil
        let hasRefresh = keychain.get(refreshKey) != nil
        guard hasAccess || hasRefresh else { return .signedOut }
        let id = keychain.get(userIDKey) ?? ""
        return .signedIn(AuthUser(id: id, email: keychain.get(emailKey)))
    }

    // MARK: - GoTrue REST

    private func requestToken(grantType: String, body: Data, config: SupabaseConfig) async throws -> SupabaseSession {
        var components = URLComponents(url: config.authBaseURL.appendingPathComponent("token"), resolvingAgainstBaseURL: false)
        components?.queryItems = [URLQueryItem(name: "grant_type", value: grantType)]
        guard let url = components?.url else { throw AuthError.notConfigured }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 30
        request.httpBody = body
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue(config.anonKey, forHTTPHeaderField: "apikey")

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch {
            throw AuthError.transport(error.localizedDescription)
        }
        guard let http = response as? HTTPURLResponse else {
            throw AuthError.transport("No HTTP response from Supabase.")
        }
        guard (200...299).contains(http.statusCode) else {
            throw AuthError.server(status: http.statusCode, message: AuthService.errorMessage(from: data))
        }
        do {
            return try decoder.decode(SupabaseSession.self, from: data)
        } catch {
            throw AuthError.decoding(String(describing: error))
        }
    }

    private func revokeRemotely(token: String, config: SupabaseConfig) async {
        var request = URLRequest(url: config.authBaseURL.appendingPathComponent("logout"))
        request.httpMethod = "POST"
        request.timeoutInterval = 15
        request.setValue(config.anonKey, forHTTPHeaderField: "apikey")
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        _ = try? await session.data(for: request)
    }

    private static func errorMessage(from data: Data) -> String? {
        guard let body = try? JSONDecoder().decode(SupabaseAuthErrorBody.self, from: data) else { return nil }
        return body.displayMessage
    }

    // MARK: - Errors

    enum AuthError: LocalizedError, Equatable {
        case notConfigured
        case noRefreshToken
        case server(status: Int, message: String?)
        case transport(String)
        case decoding(String)

        var errorDescription: String? {
            switch self {
            case .notConfigured:
                return "Sign-in isn't configured. Set the Supabase URL and anon key (see Settings / ios README)."
            case .noRefreshToken:
                return "Your session expired. Please sign in again."
            case .server(let status, let message):
                if let message, !message.isEmpty { return message }
                if status == 400 || status == 401 { return "Invalid email or password." }
                return "Sign-in failed (HTTP \(status))."
            case .transport(let message):
                return "Network error: \(message)"
            case .decoding(let message):
                return "Could not read the sign-in response: \(message)"
            }
        }
    }
}
