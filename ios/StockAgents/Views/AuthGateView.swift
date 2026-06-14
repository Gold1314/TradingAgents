import SwiftUI

/// Top-level gate: shows the Supabase login screen when signed out and the main
/// app when signed in (plan §3.4.1). Observes `AuthService` so a successful
/// sign-in — or a refresh failure that forces sign-out — swaps the UI
/// automatically.
struct AuthGateView: View {
    @ObservedObject var auth: AuthService
    let env: AppEnvironment

    var body: some View {
        switch auth.state {
        case .signedOut:
            LoginView(viewModel: env.makeLoginViewModel())
        case .signedIn:
            RootView(env: env)
        }
    }
}
