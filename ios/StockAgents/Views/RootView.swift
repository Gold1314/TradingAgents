import SwiftUI

/// App shell: hosts the run-setup screen, routes to the live run session, and
/// restores an in-flight run on launch (web parity: `tryRestoreActiveRun`).
struct RootView: View {
    @EnvironmentObject private var env: AppEnvironment
    @StateObject private var setupVM: RunSetupViewModel

    @State private var activeSession: RunSessionViewModel?
    @State private var showSession = false
    @State private var showSettings = false
    @State private var showConnect = false
    @State private var didAttemptRestore = false

    init(env: AppEnvironment) {
        _setupVM = StateObject(wrappedValue: env.makeSetupViewModel())
    }

    var body: some View {
        NavigationStack {
            RunSetupView(viewModel: setupVM, onStarted: handleStartResponse)
                .navigationTitle("StockAgents")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .navigationBarLeading) {
                        Button {
                            showConnect = true
                        } label: {
                            Image(systemName: "building.columns")
                        }
                        .accessibilityLabel("Connect Robinhood")
                    }
                    ToolbarItem(placement: .navigationBarTrailing) {
                        Button {
                            showSettings = true
                        } label: {
                            Image(systemName: "gearshape")
                        }
                        .accessibilityLabel("Settings")
                    }
                }
                .navigationDestination(isPresented: $showSession) {
                    if let session = activeSession {
                        RunSessionView(viewModel: session) {
                            // "New run" — tear down the session and pop home.
                            session.stop()
                            activeSession = nil
                            showSession = false
                        }
                    }
                }
        }
        .sheet(isPresented: $showSettings) {
            SettingsView()
        }
        .sheet(isPresented: $showConnect) {
            RobinhoodConnectView(viewModel: env.makeBrokerConnectViewModel())
        }
        .task {
            await setupVM.loadConfig()
            await restoreIfNeeded()
        }
    }

    private func handleStartResponse(_ response: StartRunResponse) {
        let session = env.makeSessionViewModel()
        switch response {
        case .started(let runID, let nodes):
            session.startStreaming(runID: runID, nodes: nodes, request: setupVM.makeRequest())
        case .cached(let run):
            session.renderCached(run)
        }
        activeSession = session
        showSession = true
    }

    private func restoreIfNeeded() async {
        guard !didAttemptRestore else { return }
        didAttemptRestore = true
        guard let saved = env.activeRunStore.load() else { return }
        let session = env.makeSessionViewModel()
        await session.restore(runID: saved.runID, request: saved.request, fallbackNodes: saved.nodes)
        if case .failed = session.phase { return } // run expired; stay on setup
        activeSession = session
        showSession = true
    }
}
