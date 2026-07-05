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
    ///
    /// Seeded conservatively from the run-completion `trade` snapshot, then
    /// REPLACED by a fresh `GET /api/v2/robinhood/status` when the ticket opens
    /// (see ``refreshTradeMode()``). The biometric gate reads this at confirm
    /// time, so an operator flipping dry_run→live after the run finished can't
    /// slip a real order past a stale "Simulated" snapshot.
    @Published private(set) var placesRealMoney: Bool
    /// Whether the broker is in dry-run/simulated mode.
    @Published private(set) var isDryRun: Bool
    /// True once the fresh broker status has been resolved (fetched or, on
    /// failure, fail-safe applied). The confirm action is blocked until then so
    /// the gate is never decided from the stale snapshot alone.
    @Published private(set) var statusChecked = false

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
        // Seed with locale-aware formatting so the displayed separator matches
        // what `.decimalPad` produces AND what `parseAmount` expects — a
        // decimal-comma user sees "100,50", not a period they can't reproduce.
        self.notionalText = intent.notional.map { Self.formatAmount($0, fractionDigits: 2) } ?? ""
        self.quantityText = intent.quantity.map { Self.formatAmount($0, fractionDigits: 8) } ?? ""

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
        // Reentrancy guard: ignore a second confirm while a gate/submit is
        // already in flight (e.g. a rapid double-tap), so we never fire two
        // orders. Retrying after a `.failed` result is still allowed.
        guard !isSubmitting else { return }

        // Validate sizing BEFORE any biometric prompt or network call. A blank
        // or unparseable amount must NOT be sent as null — for a LIVE order the
        // server would fall back to its own default, authorizing an amount the
        // user never typed. Block with a clear message instead.
        guard let edits = validatedEdits() else {
            phase = .failed(.validation(
                side == .buy
                    ? "Enter a valid dollar amount before placing this order."
                    : "Enter a valid share quantity before placing this order."
            ))
            return
        }

        do {
            if placesRealMoney {
                phase = .authenticating
                try await biometrics.evaluate(
                    reason: "Authorize your LIVE \(side.label) order for \(proposed.ticker)."
                )
            }
            phase = .submitting
            let result = try await api.submitOrder(runID: runID, edits: edits)
            phase = .placed(result)
        } catch {
            phase = failurePhase(for: error)
        }
    }

    /// Re-fetch the broker's CURRENT trade-safety flags when the ticket opens and
    /// gate LIVE-vs-simulated (and thus the Face ID requirement) on them, rather
    /// than on the flags snapshotted at run completion (plan §5.3). If the fresh
    /// status can't be fetched we FAIL SAFE: treat the order as LIVE so the
    /// biometric gate is required rather than silently skipped.
    func refreshTradeMode() async {
        defer { statusChecked = true }
        do {
            let status = try await api.robinhoodStatus()
            placesRealMoney = status.placesRealMoney
            isDryRun = status.dryRun ?? true
        } catch {
            // Fail safe: can't confirm it's simulated → treat as real money.
            placesRealMoney = true
            isDryRun = false
        }
    }

    func reset() {
        phase = .ready
    }

    // MARK: - Helpers

    /// Parsed, locale-aware sizing values (nil when the field is empty or not a
    /// valid number). Exposed so the view's confirmation copy formats the SAME
    /// value that will be submitted.
    var notionalValue: Double? { Self.parseAmount(notionalText) }
    var quantityValue: Double? { Self.parseAmount(quantityText) }

    /// Build the edit payload IFF the active sizing field parses to a positive
    /// number; returns nil otherwise so the caller can block submission. The
    /// ticker is never sent (server forces the run's own); we send only the
    /// fields the user can change. One sizing field is kept per side — buys size
    /// by notional, sells by quantity — matching the server's normalization in
    /// `_place_pending_order`.
    private func validatedEdits() -> PlaceOrderRequest? {
        var edits = PlaceOrderRequest()
        edits.action = side.rawValue
        edits.orderType = orderType.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        switch side {
        case .buy:
            guard let notional = notionalValue, notional > 0 else { return nil }
            edits.notional = notional
            edits.quantity = nil
        case .sell:
            guard let quantity = quantityValue, quantity > 0 else { return nil }
            edits.quantity = quantity
            edits.notional = nil
        }
        return edits
    }

    /// Parse a user-entered decimal that may use either '.' or ',' as the decimal
    /// separator: `.decimalPad` yields the *locale's* separator, but `Double(_:)`
    /// only accepts '.', so a decimal-comma user's input would otherwise parse to
    /// nil and get silently dropped. Returns nil for empty/invalid input.
    static func parseAmount(_ text: String) -> Double? {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        // Prefer the current locale (handles the user's own decimalPad output,
        // including a comma decimal + any grouping separators).
        let formatter = NumberFormatter()
        formatter.numberStyle = .decimal
        formatter.locale = .current
        if let number = formatter.number(from: trimmed) {
            return number.doubleValue
        }
        // Fallback: normalize a lone comma decimal to a period (e.g. a
        // comma-decimal string on a period-locale device) and parse plainly.
        return Double(trimmed.replacingOccurrences(of: ",", with: "."))
    }

    /// Format a value for an editable sizing field: locale-aware decimal
    /// separator, NO grouping separators (so it round-trips cleanly through
    /// `parseAmount`), and trailing zeros trimmed.
    private static func formatAmount(_ value: Double, fractionDigits: Int) -> String {
        let formatter = NumberFormatter()
        formatter.numberStyle = .decimal
        formatter.locale = .current
        formatter.usesGroupingSeparator = false
        formatter.minimumFractionDigits = 0
        formatter.maximumFractionDigits = fractionDigits
        return formatter.string(from: NSNumber(value: value)) ?? "\(value)"
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
}
