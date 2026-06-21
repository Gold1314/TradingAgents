import Foundation

/// REST client for the StockAgents backend.
///
/// Responsibilities (plan §3.3): configurable base URL, bearer-token injection,
/// JSON encode/decode against the typed models, non-2xx → `APIError` mapping,
/// and per-call timeouts. SSE is handled separately by `RunStreamClient`.
final class APIClient {
    /// Mutable so the Settings screen can repoint the app at a new backend.
    var baseURL: URL
    private let auth: AuthService
    private let session: URLSession
    private let decoder = JSONDecoder()
    private let encoder = JSONEncoder()

    init(baseURL: URL, auth: AuthService, session: URLSession = .shared) {
        self.baseURL = baseURL
        self.auth = auth
        self.session = session
    }

    // MARK: - Endpoints

    /// `GET /api/config` — public bootstrap call; drives the run form defaults.
    func fetchConfig() async throws -> RunConfig {
        try await get("/api/config")
    }

    /// `POST /api/runs` — start a run or return a 60-minute cache hit.
    func startRun(_ request: RunRequest) async throws -> StartRunResponse {
        try await post("/api/runs", body: request)
    }

    /// `GET /api/runs/{run_id}` — run status for session restore.
    func runStatus(runID: String) async throws -> RunStatus {
        try await get("/api/runs/\(runID)")
    }

    /// `GET /api/chart` — candles + indicators for the Market Analyst card.
    func fetchChart(ticker: String, tradeDate: String, assetType: String = "stock", lookback: Int = 180) async throws -> ChartPayload {
        var components = URLComponents()
        components.queryItems = [
            URLQueryItem(name: "ticker", value: ticker),
            URLQueryItem(name: "trade_date", value: tradeDate),
            URLQueryItem(name: "asset_type", value: assetType),
            URLQueryItem(name: "lookback", value: String(lookback)),
        ]
        let query = components.percentEncodedQuery ?? ""
        return try await get("/api/chart?\(query)")
    }

    // MARK: - v2 mobile endpoints (auth + per-user broker)
    //
    // These hit the additive `/api/v2/*` router (`web/mobile/router.py`), which
    // is gated behind `MOBILE_API_ENABLED` and requires a bearer token
    // (`current_user`). The bearer header is injected by `makeRequest` from
    // `AuthService` exactly like the legacy endpoints — so when no app token is
    // present these return 401 (mapped to `APIError.unauthorized`).

    /// `GET /api/v2/robinhood/authorize` — start the server-mediated OAuth flow
    /// (plan §5.1.3). Returns the authorization URL to open in the system browser
    /// plus an opaque server-side connect-session id.
    func robinhoodAuthorize() async throws -> RobinhoodAuthorizeResponse {
        try await get("/api/v2/robinhood/authorize")
    }

    /// `GET /api/v2/robinhood/status` — per-user connection status. Polled after
    /// the auth sheet bounces back; the server is the source of truth (plan
    /// §5.1.3 step 5).
    func robinhoodStatus() async throws -> RobinhoodStatus {
        try await get("/api/v2/robinhood/status")
    }

    /// Submit the LIVE order proposed by a finished run.
    ///
    /// The body carries only the ticket *edits* (`action/quantity/notional/
    /// order_type`); the server re-clamps to `max_order_notional`/buying power
    /// and **forces** the ticker to the run's own. The response is the
    /// `trade`-shaped payload from `_trade_event(...)`, which decodes into
    /// `TradeEvent` (the extra `type` key is ignored).
    ///
    /// Targets the per-user-safe v2 route `POST /api/v2/runs/{run_id}/orders`
    /// (`web/mobile/orders.py` → `place_user_order`), which resolves the broker
    /// via the per-user `BrokerRegistry` (never the `get_broker()` singleton),
    /// forces the ticker to the run's own, re-clamps size, and enforces the
    /// server's dry-run/live policy. Requires a bearer token (`current_user`),
    /// injected by `authorizationHeader`. The body carries only ticket edits and
    /// must never include a ticker.
    func submitOrder(runID: String, edits: PlaceOrderRequest) async throws -> TradeEvent {
        try await post("\(APIClient.runsPath)/\(runID)/orders", body: edits)
    }

    /// Base path for per-user v2 run resources (order submission lives at
    /// `\(runsPath)/{run_id}/orders`).
    static let runsPath = "/api/v2/runs"

    // MARK: - Voice-conversational endpoints (additive, default OFF server-side)

    /// `GET /api/voice/config` — capability probe driving the Talk button
    /// visibility. Returns `enabled=false` (and `ready=false`) when the
    /// server has the feature off, in which case the UI hides the button.
    func fetchVoiceConfig() async throws -> VoiceConfig {
        try await get("/api/voice/config")
    }

    /// `POST /api/voice/sessions` — mint a LiveKit room JWT for a one-on-one
    /// voice call with `agentID` on the finished run `runID`. The body is
    /// `{run_id, agent_id}`. The server gates on the agent being `done`
    /// (409 otherwise) and on the user's daily voice-minute cap (429).
    func startVoiceSession(runID: String, agentID: String) async throws -> VoiceSessionStartResponse {
        let body = VoiceSessionRequest(runID: runID, agentID: agentID)
        return try await post("/api/voice/sessions", body: body)
    }

    /// `POST /api/voice/sessions/{id}/reconcile` — Portfolio Manager only.
    /// Submits the user's objection and returns the (possibly flipped)
    /// updated rationale. Never re-runs the pipeline; one focused LLM call.
    func reconcilePortfolioManager(sessionID: String, objection: String) async throws -> VoiceReconcileResponse {
        let body = VoiceReconcileRequest(objection: objection)
        return try await post(
            "/api/voice/sessions/\(sessionID)/reconcile",
            body: body
        )
    }

    // MARK: - Verbs

    private func get<Response: Decodable>(_ path: String) async throws -> Response {
        try await perform(path: path, method: "GET", body: nil)
    }

    private func post<Body: Encodable, Response: Decodable>(_ path: String, body: Body) async throws -> Response {
        let data = try encoder.encode(body)
        return try await perform(path: path, method: "POST", body: data)
    }

    /// Build → send, with a single transparent token refresh on `401`
    /// (plan §3.4.1 "refreshes on 401"). The request is rebuilt after the
    /// refresh so the retry carries the *new* bearer header. If the refresh
    /// fails, the session is cleared (→ signed-out) and the `401` propagates.
    private func perform<Response: Decodable>(path: String, method: String, body: Data?) async throws -> Response {
        let request = try await makeRequest(path: path, method: method, body: body)
        do {
            return try await send(request)
        } catch APIError.unauthorized {
            guard await auth.attemptRefresh() else {
                await auth.signOut()
                throw APIError.unauthorized
            }
            let retry = try await makeRequest(path: path, method: method, body: body)
            return try await send(retry)
        }
    }

    // MARK: - Plumbing

    private func makeRequest(path: String, method: String, body: Data?) async throws -> URLRequest {
        guard let url = URL(string: path, relativeTo: baseURL) else {
            throw APIError.invalidURL
        }
        var request = URLRequest(url: url)
        request.httpMethod = method
        // Runs are slow to *finish* but the POST returns fast with a run_id; a
        // generous timeout still protects against a wedged connection.
        request.timeoutInterval = 60
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        if let header = await auth.authorizationHeader {
            request.setValue(header, forHTTPHeaderField: "Authorization")
        }
        return request
    }

    private func send<Response: Decodable>(_ request: URLRequest) async throws -> Response {
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch let error as URLError where error.code == .cancelled {
            throw APIError.cancelled
        } catch {
            throw APIError.transport(error.localizedDescription)
        }

        guard let http = response as? HTTPURLResponse else {
            throw APIError.transport("No HTTP response")
        }
        try APIClient.validate(http, data: data)

        do {
            return try decoder.decode(Response.self, from: data)
        } catch {
            throw APIError.decoding(String(describing: error))
        }
    }

    /// Map a non-2xx status to a typed `APIError`, extracting FastAPI's
    /// `{"detail": ...}` body when present.
    static func validate(_ http: HTTPURLResponse, data: Data) throws {
        guard !(200...299).contains(http.statusCode) else { return }
        switch http.statusCode {
        case 401:
            throw APIError.unauthorized
        case 429:
            let retryAfter = http.value(forHTTPHeaderField: "Retry-After").flatMap(Int.init)
            throw APIError.rateLimited(retryAfter: retryAfter)
        default:
            throw APIError.http(status: http.statusCode, detail: Self.detail(from: data))
        }
    }

    private static func detail(from data: Data) -> String? {
        guard
            let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return nil }
        if let detail = object["detail"] as? String { return detail }
        if let detail = object["detail"] { return String(describing: detail) }
        return nil
    }
}
