import SwiftUI

/// Robinhood connect screen (plan §5.1.3). Shows the current connection state,
/// drives the server-mediated OAuth flow via `ASWebAuthenticationSession`, and
/// reflects the connecting / connected / failed states. Emphasizes that the
/// phone never holds a brokerage token.
struct RobinhoodConnectView: View {
    @StateObject private var viewModel: BrokerConnectViewModel
    @Environment(\.dismiss) private var dismiss

    init(viewModel: @autoclosure @escaping () -> BrokerConnectViewModel) {
        _viewModel = StateObject(wrappedValue: viewModel())
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 20) {
                    statusCard
                    if viewModel.isConnected {
                        safetyCard
                    }
                    actionButton
                    if case .failed(let message) = viewModel.phase {
                        errorBanner(message)
                    }
                    privacyFootnote
                }
                .padding()
            }
            .background(Theme.background.ignoresSafeArea())
            .navigationTitle("Robinhood")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Done") {
                        viewModel.cancel()
                        dismiss()
                    }
                }
            }
            .task { await viewModel.loadStatus() }
        }
    }

    // MARK: - Sections

    private var statusCard: some View {
        VStack(spacing: 12) {
            Image(systemName: iconName)
                .font(.system(size: 44))
                .foregroundStyle(statusColor)
            Text(statusTitle)
                .font(.title3.weight(.semibold))
                .foregroundStyle(Theme.textPrimary)
            Text(statusSubtitle)
                .font(.subheadline)
                .multilineTextAlignment(.center)
                .foregroundStyle(Theme.textMuted)
        }
        .frame(maxWidth: .infinity)
        .padding(20)
        .background(RoundedRectangle(cornerRadius: 16).fill(Theme.surface.opacity(0.5)))
    }

    private var safetyCard: some View {
        let status = viewModel.status
        return VStack(alignment: .leading, spacing: 8) {
            Label("Trade-safety", systemImage: "shield.lefthalf.filled")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(Theme.textPrimary)
            row("Mode", status?.tradeMode ?? "—")
            row("Real orders", (status?.canPlaceRealOrders ?? false) ? "Enabled" : "Off")
            row("Dry run", (status?.dryRun ?? true) ? "On (simulated)" : "Off (LIVE)")
            if let cap = status?.maxOrderNotional {
                row("Max order", Formatters.usd(cap))
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .background(RoundedRectangle(cornerRadius: 14).fill(Theme.surface.opacity(0.35)))
    }

    private var actionButton: some View {
        Button(action: { Task { await viewModel.connect() } }) {
            HStack {
                Spacer()
                if viewModel.isBusy {
                    ProgressView().tint(Theme.background)
                    Text(busyLabel).bold()
                } else {
                    Image(systemName: "link")
                    Text(viewModel.isConnected ? "Reconnect" : "Connect Robinhood").bold()
                }
                Spacer()
            }
            .padding(.vertical, 14)
        }
        .background(RoundedRectangle(cornerRadius: 12).fill(viewModel.isBusy ? Theme.border : Theme.accent))
        .foregroundStyle(Theme.background)
        .disabled(viewModel.isBusy)
    }

    private func errorBanner(_ message: String) -> some View {
        Label(message, systemImage: "exclamationmark.triangle.fill")
            .font(.footnote)
            .foregroundStyle(Theme.danger)
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 10).fill(Theme.danger.opacity(0.12)))
    }

    private var privacyFootnote: some View {
        Label(
            "Your Robinhood login and tokens stay on the server. This device only opens the secure sign-in page and checks connection status — it never sees your brokerage credentials.",
            systemImage: "lock.shield"
        )
        .font(.caption)
        .foregroundStyle(Theme.textMuted)
        .padding(.top, 4)
    }

    private func row(_ label: String, _ value: String) -> some View {
        HStack {
            Text(label).foregroundStyle(Theme.textMuted)
            Spacer()
            Text(value).foregroundStyle(Theme.textPrimary)
        }
        .font(.footnote)
    }

    // MARK: - Derived copy

    private var iconName: String {
        switch viewModel.phase {
        case .connected: return "checkmark.seal.fill"
        case .failed: return "xmark.seal.fill"
        case .starting, .authorizing, .polling: return "arrow.triangle.2.circlepath"
        default: return "building.columns"
        }
    }

    private var statusColor: Color {
        switch viewModel.phase {
        case .connected: return Theme.accent
        case .failed: return Theme.danger
        case .starting, .authorizing, .polling: return Theme.warning
        default: return Theme.textMuted
        }
    }

    private var statusTitle: String {
        switch viewModel.phase {
        case .unknown: return "Checking…"
        case .disconnected: return "Not connected"
        case .starting: return "Starting…"
        case .authorizing: return "Authorize in browser"
        case .polling: return "Finishing connection…"
        case .connected: return "Connected"
        case .failed: return "Connection failed"
        }
    }

    private var statusSubtitle: String {
        switch viewModel.phase {
        case .connected:
            return "Robinhood is linked to your account on the server."
        case .authorizing:
            return "Complete the Robinhood sign-in in the secure browser sheet."
        case .polling:
            return "Confirming the connection with the server…"
        default:
            return "Link your Robinhood account to ground runs in your real positions and place orders."
        }
    }

    private var busyLabel: String {
        switch viewModel.phase {
        case .starting: return "Starting…"
        case .authorizing: return "Waiting…"
        case .polling: return "Connecting…"
        default: return "Working…"
        }
    }
}
