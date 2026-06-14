import Foundation

/// Drives the order ticket + the mandatory two-factor LIVE submission gate
/// (plan §5.3, decision 4). Surfaces the agent's proposed `OrderIntent`, lets the
/// user adjust sizing, and — for a real-money order — requires BOTH an explicit
/// on-screen confirmation AND a successful biometric evaluation IMMEDIATELY
/// before the network call. There is exactly one path to `api.submitOrder`, and
/// for a LIVE order it runs only after `biometrics.evaluate` succeeds, so it is
/// impossible to place a live order without passing the Face ID / Touch ID gate.
@MainActor
final class OrderTicketViewModel: ObservableObject {
    enum Phase: Equatable {
        case ready
        case authenticating   // biometric gate in progress (LIVE only)
        case submitting       // POST in flight
        case placed(TradeEvent)
        case failed(SubmissionFailure)
    }

    /// Typed, actionable classification of an order-submission failure, mapped
    /// from the server's HTTP status (plan §5.3 / `web/mobile/orders.py`). Each
    /// case drives a distinct, recoverable UX in `OrderTicketView` rather than a
    /// generic error banner. These all occur *after* the biometric gate (the
    /// network call only runs once `confirmAndSubmit` has passed it), so they can
    /// never bypass Face ID / Touch ID.
    enum SubmissionFailure: Equatable {
        /// `409` "Robinhood not connected." — offer to open the connect flow.
        case brokerNotConnected(String)
        /// `404` — the run is unknown, expired, or not owned by this user.
        case runUnavailable(String)
        /// `429` — per-user order quota; `retryAfter` is the server's wait hint.
        case rateLimited(message: String, retryAfter: Int?)
        /// `400` — no actionable order / missing size; guide the user to fix it.
        case validation(String)
        /// Any other failure (other `409`s, transport, decoding, expired session).
        case generic(String)

        /// The human-readable message shown in the ticket's result section.
        var displayMessage: String {
            switch self {
            case .brokerNotConnected(let message),
                 .runUnavailable(let message),
                 .validation(let message),
                 .generic(let message):
                return message
            case .rateLimited(let message, _):
                return message
            }
        }
    }

    enum Side: String, CaseIterable, Identifiable {
        case buy
        case sell
        var id: String { rawValue }
        var label: String { rawValue.capitalized }
    }

    @Published private(set) var phase: Phase = .ready

    // Editable ticket fields, seeded from the proposed intent.
    @Published var side: Side
    @Published var orderType: String
    @Published var notionalText: String
    @Published var quantityText: String

    let runID: String
    let proposed: OrderIntent
    /// Whether this submission moves real money (drives styling + the gate).
    let placesRealMoney: Bool
    /// Whether the broker is in dry-run/simulated mode.
    let isDryRun: Bool

    private let api: APIClient
    private let biometrics: BiometricAuthenticator

    init(runID: String, trade: TradeEvent, api: APIClient, biometrics: BiometricAuthenticator) {
        self.runID = runID
        self.api = api
        self.biometrics = biometrics

        let intent = trade.intent ?? OrderIntent(
            ticker: "",
            action: "none",
            rating: "",
            notional: nil,
            quantity: nil,
            orderType: "market",
            reason: ""
        )
        self.proposed = intent
        self.side = (intent.action == "sell") ? .sell : .buy
        self.orderType = intent.orderType
        self.notionalText = intent.notional.map { String(format: "%.2f", $0) } ?? ""
        self.quantityText = intent.quantity.map { Self.trimQuantity($0) } ?? ""

        let dryRun = trade.dryRun ?? true
        self.isDryRun = dryRun
        self.placesRealMoney = (trade.canPlaceRealOrders ?? false) && !dryRun
    }

    /// What biometry the device offers, for labeling the confirm action.
    var biometryKind: BiometricAuthenticator.Kind { biometrics.availableKind() }

    /// True when a LIVE order cannot be gated because no device auth exists.
    /// The confirm action is disabled in this state — we never skip the gate.
    var liveBlockedByMissingBiometrics: Bool {
        placesRealMoney && biometryKind == .none
    }

    var isSubmitting: Bool {
        switch phase {
        case .authenticating, .submitting: return true
        default: return false
        }
    }

    /// THE submission gate. Call this only from the deliberate confirm action.
    ///
    /// Order of operations for a LIVE order (plan §5.3):
    ///   1. explicit confirmation — already provided by reaching this method via
    ///      the "Place LIVE order" action in the confirmation sheet; then
    ///   2. biometric evaluation — must SUCCEED here before any network call.
    /// Only then is `POST /api/robinhood/orders/{run_id}` sent. A simulated /
    /// dry-run placement skips step 2 (still confirmed), per §5.3.
    func confirmAndSubmit() async {
        do {
            if placesRealMoney {
                phase = .authenticating
                try await biometrics.evaluate(
                    reason: "Authorize your LIVE \(side.label) order for \(proposed.ticker)."
                )
            }
            phase = .submitting
            let result = try await api.submitOrder(runID: runID, edits: makeEdits())
            phase = .placed(result)
        } catch {
            phase = failurePhase(for: error)
        }
    }

    func reset() {
        phase = .ready
    }

    // MARK: - Helpers

    /// Build the edit payload. The ticker is never sent (server forces the run's
    /// own); we send only the fields the user can change. One sizing field is
    /// kept per side — buys size by notional, sells by quantity — matching the
    /// server's normalization in `_place_pending_order`.
    private func makeEdits() -> PlaceOrderRequest {
        var edits = PlaceOrderRequest()
        edits.action = side.rawValue
        edits.orderType = orderType.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        switch side {
        case .buy:
            edits.notional = Double(notionalText.trimmingCharacters(in: .whitespacesAndNewlines))
            edits.quantity = nil
        case .sell:
            edits.quantity = Double(quantityText.trimmingCharacters(in: .whitespacesAndNewlines))
            edits.notional = nil
        }
        return edits
    }

    /// Classify a thrown error into a `.failed` phase with an actionable
    /// `SubmissionFailure`. The v2 order endpoint (`web/mobile/orders.py`) maps
    /// distinct failures onto HTTP statuses, so we branch on the status carried
    /// by `APIError` rather than on free-text — except the two `409` sub-cases,
    /// which share a status and are disambiguated by the server's detail string
    /// ("Robinhood not connected." vs. execution-off / no-ticker).
    private func failurePhase(for error: Error) -> Phase {
        if let gateError = error as? BiometricAuthenticator.GateError {
            return .failed(.generic(gateError.errorDescription ?? "Authentication failed."))
        }
        guard let apiError = error as? APIError else {
            return .failed(.generic(error.localizedDescription))
        }
        switch apiError {
        case .rateLimited(let retryAfter):
            let message: String
            if let retryAfter {
                message = "Order limit reached. Try again in about \(retryAfter)s."
            } else {
                message = "Order limit reached. Please try again later."
            }
            return .failed(.rateLimited(message: message, retryAfter: retryAfter))

        case .http(let status, let detail):
            switch status {
            case 404:
                return .failed(.runUnavailable(
                    "This run is no longer available. Start a new run to place an order."
                ))
            case 409:
                if let detail, detail.localizedCaseInsensitiveContains("not connected") {
                    return .failed(.brokerNotConnected(
                        "Robinhood isn't connected. Connect your account to place this order."
                    ))
                }
                // Other 409s (execution off, run has no ticker): surface the
                // server's reason verbatim — there's no in-ticket recovery.
                return .failed(.generic(detail ?? "This order can't be placed right now."))
            case 400:
                let guidance = "Set a side (buy or sell) and a positive size, then try again."
                if let detail, !detail.isEmpty {
                    return .failed(.validation("\(detail). \(guidance)"))
                }
                return .failed(.validation(guidance))
            default:
                return .failed(.generic(apiError.errorDescription ?? "Order submission failed."))
            }

        default:
            // .unauthorized (refresh+retry already exhausted in APIClient → the
            // app drops to signed-out), .transport, .decoding, .invalidURL, etc.
            return .failed(.generic(apiError.errorDescription ?? "Order submission failed."))
        }
    }

    private static func trimQuantity(_ value: Double) -> String {
        if value == value.rounded() {
            return String(format: "%.0f", value)
        }
        return String(value)
    }
}
