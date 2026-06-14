import Foundation

/// Drives `LoginView`: email/password input, loading + error states, and the
/// Supabase sign-in call. On success `AuthService.state` flips to `.signedIn`
/// and the auth gate swaps the login screen for the main app.
@MainActor
final class LoginViewModel: ObservableObject {
    @Published var email = ""
    @Published var password = ""
    @Published private(set) var isLoading = false
    @Published var errorMessage: String?

    private let auth: AuthService

    init(auth: AuthService) {
        self.auth = auth
    }

    var isConfigured: Bool { auth.isConfigured }

    var canSubmit: Bool {
        !isLoading
            && isConfigured
            && !email.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && !password.isEmpty
    }

    func signIn() async {
        guard canSubmit else { return }
        isLoading = true
        errorMessage = nil
        do {
            try await auth.signIn(
                email: email.trimmingCharacters(in: .whitespacesAndNewlines),
                password: password
            )
        } catch {
            errorMessage = message(for: error)
        }
        isLoading = false
    }

    private func message(for error: Error) -> String {
        if let authError = error as? AuthService.AuthError {
            return authError.errorDescription ?? "Sign-in failed."
        }
        if let apiError = error as? APIError {
            return apiError.errorDescription ?? "Sign-in failed."
        }
        return error.localizedDescription
    }
}
