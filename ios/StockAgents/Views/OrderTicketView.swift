import SwiftUI

/// LIVE order ticket + biometric-gated confirmation (plan §5.3, decision 4).
///
/// The user reviews/edits the agent's proposed order, taps "Review order" to
/// open a deliberate confirmation sheet, and only then can place it. For a
/// real-money order the confirm action triggers a Face ID / Touch ID evaluation
/// in the view model BEFORE the network call — there is no way to submit a LIVE
/// order without passing both gates.
struct OrderTicketView: View {
    @StateObject private var viewModel: OrderTicketViewModel
    @Environment(\.dismiss) private var dismiss

    @State private var showConfirm = false
    @State private var showConnect = false

    /// Factory for the Robinhood connect screen, used when a submission fails
    /// with `409 "not connected"`. Passed in (rather than read from the
    /// environment) so the hand-off works reliably from a presented sheet and
    /// stays consistent with the app's `AppEnvironment` view-model factories.
    private let makeBrokerConnectViewModel: () -> BrokerConnectViewModel

    init(
        viewModel: @autoclosure @escaping () -> OrderTicketViewModel,
        makeBrokerConnectViewModel: @escaping () -> BrokerConnectViewModel
    ) {
        _viewModel = StateObject(wrappedValue: viewModel())
        self.makeBrokerConnectViewModel = makeBrokerConnectViewModel
    }

    var body: some View {
        NavigationStack {
            Form {
                modeBanner

                Section("Proposed by the agents") {
                    LabeledContent("Ticker", value: viewModel.proposed.ticker)
                    LabeledContent("Rating", value: viewModel.proposed.rating)
                    if !viewModel.proposed.reason.isEmpty {
                        Text(viewModel.proposed.reason)
                            .font(.footnote)
                            .foregroundStyle(Theme.textMuted)
                    }
                }

                Section("Order") {
                    Picker("Side", selection: $viewModel.side) {
                        ForEach(OrderTicketViewModel.Side.allCases) { side in
                            Text(side.label).tag(side)
                        }
                    }
                    .pickerStyle(.segmented)

                    if viewModel.side == .buy {
                        HStack {
                            Text("Notional (USD)")
                            Spacer()
                            TextField("0.00", text: $viewModel.notionalText)
                                .keyboardType(.decimalPad)
                                .multilineTextAlignment(.trailing)
                        }
                    } else {
                        HStack {
                            Text("Quantity (shares)")
                            Spacer()
                            TextField("0", text: $viewModel.quantityText)
                                .keyboardType(.decimalPad)
                                .multilineTextAlignment(.trailing)
                        }
                    }

                    LabeledContent("Type", value: viewModel.orderType)
                }

                if viewModel.liveBlockedByMissingBiometrics {
                    Section {
                        Label(
                            "Face ID / Touch ID or a device passcode is required to place a LIVE order. Enable one in Settings to continue.",
                            systemImage: "lock.slash"
                        )
                        .font(.footnote)
                        .foregroundStyle(Theme.danger)
                    }
                }

                resultSection

                Section {
                    Button(action: { showConfirm = true }) {
                        HStack {
                            Spacer()
                            Text(viewModel.placesRealMoney ? "Review LIVE order" : "Review order").bold()
                            Spacer()
                        }
                    }
                    .disabled(
                        viewModel.isSubmitting
                        || viewModel.liveBlockedByMissingBiometrics
                        || isPlaced
                        || !viewModel.statusChecked
                    )
                    .listRowBackground(primaryColor)
                    .foregroundStyle(Theme.background)
                }
            }
            .task {
                // Re-fetch the broker's CURRENT trade-safety flags so LIVE-vs-
                // simulated (and the Face ID gate) is decided from fresh server
                // state, not the run-completion snapshot. Fails safe to LIVE.
                await viewModel.refreshTradeMode()
            }
            .navigationTitle("Order ticket")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(isPlaced ? "Done" : "Cancel") { dismiss() }
                }
            }
            .confirmationDialog(confirmTitle, isPresented: $showConfirm, titleVisibility: .visible) {
                Button(confirmActionLabel, role: viewModel.placesRealMoney ? .destructive : nil) {
                    Task { await viewModel.confirmAndSubmit() }
                }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text(confirmMessage)
            }
            .sheet(isPresented: $showConnect) {
                RobinhoodConnectView(viewModel: makeBrokerConnectViewModel())
            }
        }
    }

    // MARK: - Sections

    private var modeBanner: some View {
        let live = viewModel.placesRealMoney
        return Section {
            Label {
                VStack(alignment: .leading, spacing: 2) {
                    Text(live ? "LIVE — real money" : (viewModel.isDryRun ? "Simulated (dry run)" : "Simulated"))
                        .font(.subheadline.weight(.bold))
                    Text(live
                         ? "Placing this order will move real funds in your Robinhood account."
                         : "This placement is simulated — no real order is sent.")
                        .font(.caption)
                }
            } icon: {
                Image(systemName: live ? "exclamationmark.octagon.fill" : "testtube.2")
            }
            .foregroundStyle(live ? Theme.danger : Theme.warning)
        }
    }

    @ViewBuilder
    private var resultSection: some View {
        switch viewModel.phase {
        case .authenticating:
            Section {
                Label("Waiting for \(viewModel.biometryKind.label)…", systemImage: "faceid")
                    .foregroundStyle(Theme.textMuted)
            }
        case .submitting:
            Section {
                HStack {
                    ProgressView()
                    Text("Submitting…").foregroundStyle(Theme.textMuted)
                }
            }
        case .placed(let trade):
            Section {
                Label(placedSummary(trade), systemImage: "checkmark.circle.fill")
                    .foregroundStyle(Theme.accent)
            }
        case .failed(let failure):
            failureSection(failure)
        case .ready:
            EmptyView()
        }
    }

    /// Render a failed submission with copy + affordances tuned to the cause.
    /// The Review button stays enabled in `.failed`, so once the user resolves
    /// the issue (e.g. connects Robinhood, fixes the size) they re-run the full
    /// confirm + biometric gate — these states never bypass Face ID.
    @ViewBuilder
    private func failureSection(_ failure: OrderTicketViewModel.SubmissionFailure) -> some View {
        switch failure {
        case .brokerNotConnected(let message):
            Section {
                Label(message, systemImage: "link.badge.plus")
                    .font(.footnote)
                    .foregroundStyle(Theme.warning)
                Button {
                    showConnect = true
                } label: {
                    HStack {
                        Spacer()
                        Label("Connect Robinhood", systemImage: "link").bold()
                        Spacer()
                    }
                }
            }
        case .runUnavailable(let message):
            Section {
                Label(message, systemImage: "clock.badge.xmark")
                    .font(.footnote)
                    .foregroundStyle(Theme.textMuted)
            }
        case .rateLimited(let message, _):
            Section {
                Label(message, systemImage: "hourglass")
                    .font(.footnote)
                    .foregroundStyle(Theme.warning)
            }
        case .validation(let message):
            Section {
                Label(message, systemImage: "exclamationmark.circle")
                    .font(.footnote)
                    .foregroundStyle(Theme.warning)
            }
        case .generic(let message):
            Section {
                Label(message, systemImage: "exclamationmark.triangle.fill")
                    .font(.footnote)
                    .foregroundStyle(Theme.danger)
            }
        }
    }

    // MARK: - Derived copy

    private var isPlaced: Bool {
        if case .placed = viewModel.phase { return true }
        return false
    }

    private var primaryColor: Color {
        if viewModel.isSubmitting || viewModel.liveBlockedByMissingBiometrics || isPlaced || !viewModel.statusChecked {
            return Theme.border
        }
        return viewModel.placesRealMoney ? Theme.danger : Theme.accent
    }

    private var confirmTitle: String {
        viewModel.placesRealMoney ? "Place LIVE order?" : "Place simulated order?"
    }

    private var confirmActionLabel: String {
        if viewModel.placesRealMoney {
            return "Place LIVE order · \(viewModel.biometryKind.label)"
        }
        return "Simulate order"
    }

    private var confirmMessage: String {
        let size: String
        if viewModel.side == .buy {
            size = Formatters.usd(viewModel.notionalValue)
        } else {
            size = "\(viewModel.quantityText) shares"
        }
        let header = "\(viewModel.side.label.uppercased()) \(viewModel.proposed.ticker) · \(size) · \(viewModel.orderType)"
        if viewModel.placesRealMoney {
            return "\(header)\n\nThis is a LIVE order using real money. You'll confirm with \(viewModel.biometryKind.label) before it is sent."
        }
        return "\(header)\n\nThis is a simulated (dry-run) order — no real funds will move."
    }

    private func placedSummary(_ trade: TradeEvent) -> String {
        switch trade.status {
        case "placed":
            return "Order placed" + (trade.orderID.map { " (#\($0))" } ?? "") + "."
        case "dry_run":
            return "Simulated order recorded (dry run)."
        case "skipped":
            return trade.message ?? "Order skipped."
        case "error":
            return trade.message ?? "Order failed."
        default:
            return trade.message ?? "Order status: \(trade.status)."
        }
    }
}
