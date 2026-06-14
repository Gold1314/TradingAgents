import Foundation

/// Drives the Robinhood connect screen (plan §5.1.3): kick off server-mediated
/// OAuth, present the system auth sheet, then poll `GET /api/v2/robinhood/status`
/// until the server reports `connected: true`. The phone never holds a brokerage
/// token — it only launches the auth URL and observes server state.
@MainActor
final class BrokerConnectViewModel: ObservableObject {
    enum Phase: Equatable {
        case unknown        // before the first status load
        case disconnected
        case starting       // calling /authorize
        case authorizing    // ASWebAuthenticationSession presented
        case polling        // bounced back; polling /status
        case connected
        case failed(String)
    }

    @Published private(set) var phase: Phase = .unknown
    @Published private(set) var status: RobinhoodStatus?

    private let api: APIClient
    private let connectService: BrokerConnectService

    /// Polling cadence after the auth sheet bounces back.
    private let pollInterval: UInt64 = 1_500_000_000 // 1.5s in ns
    private let maxPollAttempts = 20                 // ~30s ceiling

    init(api: APIClient, connectService: BrokerConnectService) {
        self.api = api
        self.connectService = connectService
    }

    var isConnected: Bool {
        if case .connected = phase { return true }
        return status?.connected ?? false
    }

    var isBusy: Bool {
        switch phase {
        case .starting, .authorizing, .polling: return true
        default: return false
        }
    }

    /// Initial / refresh status load (no auth sheet). Safe to call on appear.
    func loadStatus() async {
        do {
            let status = try await api.robinhoodStatus()
            self.status = status
            phase = status.connected ? .connected : .disconnected
        } catch {
            // A 401 here just means the app token isn't present yet; show the
            // disconnected state with the underlying reason rather than a hard
            // failure so the user can still attempt to connect.
            phase = .failed(message(for: error))
        }
    }

    /// Full connect flow: authorize → system auth sheet → poll status.
    func connect() async {
        phase = .starting
        do {
            let authorize = try await api.robinhoodAuthorize()
            guard let url = URL(string: authorize.authorizationURL) else {
                phase = .failed(BrokerConnectError.invalidAuthorizationURL.localizedDescription)
                return
            }

            phase = .authorizing
            let callback = try await connectService.authenticate(authorizationURL: url)
            let (callbackStatus, reason) = BrokerConnectService.callbackStatus(callback)

            // The server is authoritative (plan §5.1.3 step 5): poll regardless,
            // but surface the bounce-back reason early when it explicitly failed.
            phase = .polling
            let connected = await pollUntilConnected()
            if connected {
                phase = .connected
            } else if callbackStatus == "error" {
                phase = .failed(reason ?? status?.error ?? "Robinhood reported an authorization error.")
            } else {
                phase = .failed(status?.error ?? "Timed out waiting for Robinhood to connect.")
            }
        } catch let error as BrokerConnectError where error == .cancelled {
            // User backed out of the sheet — return to the prior state quietly.
            phase = status?.connected == true ? .connected : .disconnected
        } catch {
            phase = .failed(message(for: error))
        }
    }

    func cancel() {
        connectService.cancel()
    }

    // MARK: - Helpers

    private func pollUntilConnected() async -> Bool {
        for _ in 0..<maxPollAttempts {
            if Task.isCancelled { return false }
            do {
                let status = try await api.robinhoodStatus()
                self.status = status
                if status.connected { return true }
            } catch {
                // Transient poll error — keep trying until the attempt ceiling.
            }
            try? await Task.sleep(nanoseconds: pollInterval)
        }
        return false
    }

    private func message(for error: Error) -> String {
        if let apiError = error as? APIError { return apiError.errorDescription ?? "Request failed." }
        if let connectError = error as? BrokerConnectError { return connectError.errorDescription ?? "Connection failed." }
        return error.localizedDescription
    }
}
