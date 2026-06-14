import SwiftUI

/// The run configuration form. Mirrors the web form in `index.html`: ticker,
/// trade date, asset type, analyst multi-select, and an "Advanced" disclosure
/// (provider, deep/quick model, debate & risk rounds).
struct RunSetupView: View {
    @ObservedObject var viewModel: RunSetupViewModel
    let onStarted: (StartRunResponse) -> Void

    @State private var showAdvanced = false

    var body: some View {
        Form {
            Section {
                TextField("Ticker (e.g. NVDA)", text: $viewModel.ticker)
                    .textInputAutocapitalization(.characters)
                    .autocorrectionDisabled()
                    .font(.body.weight(.semibold))

                DatePicker("Trade date", selection: $viewModel.tradeDate, displayedComponents: .date)

                Picker("Asset type", selection: $viewModel.assetType) {
                    ForEach(AssetType.allCases) { type in
                        Text(type.label).tag(type)
                    }
                }
            } header: {
                Text("What to analyze")
            }

            Section {
                analystGrid
            } header: {
                Text("Analyst team")
            } footer: {
                Text("Plus 8 fixed agents (researchers, manager, trader, risk team, PM).")
            }

            Section {
                DisclosureGroup("Advanced — models & debate rounds", isExpanded: $showAdvanced) {
                    TextField("Provider (optional)", text: $viewModel.provider)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    TextField("Deep model (optional)", text: $viewModel.deepModel)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    TextField("Quick model (optional)", text: $viewModel.quickModel)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    HStack {
                        TextField("Debate rounds", text: $viewModel.maxDebateRounds)
                            .keyboardType(.numberPad)
                        Divider()
                        TextField("Risk rounds", text: $viewModel.maxRiskRounds)
                            .keyboardType(.numberPad)
                    }
                }
            }

            if let error = viewModel.startError {
                Section {
                    Text(error).foregroundStyle(Theme.danger).font(.callout)
                }
            }

            Section {
                Button(action: start) {
                    HStack {
                        Spacer()
                        if viewModel.isStarting {
                            ProgressView().tint(.white)
                        } else {
                            Text("Run analysis").bold()
                        }
                        Spacer()
                    }
                }
                .disabled(!viewModel.canStart)
                .listRowBackground(viewModel.canStart ? Theme.accent : Theme.border)
                .foregroundStyle(Theme.background)
            }

            if case .failed(let message) = viewModel.configState {
                Section {
                    Label("Using built-in defaults — \(message)", systemImage: "exclamationmark.triangle")
                        .font(.footnote)
                        .foregroundStyle(Theme.warning)
                }
            }
        }
    }

    private var analystGrid: some View {
        let columns = [GridItem(.adaptive(minimum: 150), spacing: 8)]
        return LazyVGrid(columns: columns, alignment: .leading, spacing: 8) {
            ForEach(viewModel.availableAnalysts, id: \.key) { analyst in
                analystChip(key: analyst.key, label: analyst.display)
            }
        }
        .padding(.vertical, 4)
    }

    private func analystChip(key: String, label: String) -> some View {
        let isOn = viewModel.selectedAnalysts.contains(key)
        let meta = AgentNode.metadata(for: AgentNode.analystDisplayName[key] ?? key)
        return Button {
            viewModel.toggleAnalyst(key)
        } label: {
            HStack(spacing: 6) {
                Text(meta.icon)
                Text(label)
                    .font(.subheadline)
                    .lineLimit(1)
                Spacer(minLength: 0)
                if isOn {
                    Image(systemName: "checkmark.circle.fill")
                        .foregroundStyle(Color(hex: meta.colorHex))
                }
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 8)
            .frame(maxWidth: .infinity)
            .background(
                RoundedRectangle(cornerRadius: 10)
                    .fill(isOn ? Color(hex: meta.colorHex).opacity(0.18) : Theme.surface.opacity(0.4))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 10)
                    .stroke(isOn ? Color(hex: meta.colorHex) : Theme.border, lineWidth: 1)
            )
        }
        .buttonStyle(.plain)
        .foregroundStyle(Theme.textPrimary)
    }

    private func start() {
        Task {
            if let response = await viewModel.start() {
                onStarted(response)
            }
        }
    }
}
