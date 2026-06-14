import SwiftUI

@main
struct StockAgentsApp: App {
    @StateObject private var env = AppEnvironment()

    var body: some Scene {
        WindowGroup {
            AuthGateView(auth: env.auth, env: env)
                .environmentObject(env)
                .preferredColorScheme(.dark)
        }
    }
}
