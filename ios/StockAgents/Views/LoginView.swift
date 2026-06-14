import SwiftUI

/// Email/password sign-in against Supabase Auth (plan §3.4.1). Shown by the auth
/// gate whenever `AuthService.state` is `.signedOut`. On success the gate swaps
/// in the main app automatically (it observes `AuthService`).
struct LoginView: View {
    @StateObject private var viewModel: LoginViewModel
    @FocusState private var focusedField: Field?

    private enum Field { case email, password }

    init(viewModel: LoginViewModel) {
        _viewModel = StateObject(wrappedValue: viewModel)
    }

    var body: some View {
        ZStack {
            Theme.background.ignoresSafeArea()

            VStack(spacing: 24) {
                Spacer(minLength: 0)

                VStack(spacing: 8) {
                    Image(systemName: "chart.line.uptrend.xyaxis")
                        .font(.system(size: 44, weight: .semibold))
                        .foregroundStyle(Theme.accent)
                    Text("StockAgents")
                        .font(.largeTitle.bold())
                        .foregroundStyle(Theme.textPrimary)
                    Text("Sign in to run the trading desk.")
                        .font(.subheadline)
                        .foregroundStyle(Theme.textMuted)
                }

                VStack(spacing: 12) {
                    TextField("Email", text: $viewModel.email)
                        .textContentType(.emailAddress)
                        .keyboardType(.emailAddress)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .submitLabel(.next)
                        .focused($focusedField, equals: .email)
                        .onSubmit { focusedField = .password }
                        .fieldStyle()

                    SecureField("Password", text: $viewModel.password)
                        .textContentType(.password)
                        .submitLabel(.go)
                        .focused($focusedField, equals: .password)
                        .onSubmit { submit() }
                        .fieldStyle()

                    if let error = viewModel.errorMessage {
                        Text(error)
                            .font(.footnote)
                            .foregroundStyle(Theme.danger)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }

                    if !viewModel.isConfigured {
                        Text("Supabase isn't configured. Set SUPABASE_URL and SUPABASE_ANON_KEY (see the ios README).")
                            .font(.footnote)
                            .foregroundStyle(Theme.warning)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }

                    Button(action: submit) {
                        HStack {
                            if viewModel.isLoading {
                                ProgressView().tint(Theme.background)
                            }
                            Text(viewModel.isLoading ? "Signing in…" : "Sign In")
                                .fontWeight(.semibold)
                        }
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 14)
                        .background(viewModel.canSubmit ? Theme.accent : Theme.border)
                        .foregroundStyle(Theme.background)
                        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                    }
                    .disabled(!viewModel.canSubmit)
                }

                Spacer(minLength: 0)
            }
            .padding(.horizontal, 24)
            .frame(maxWidth: 480)
        }
    }

    private func submit() {
        focusedField = nil
        Task { await viewModel.signIn() }
    }
}

private extension View {
    /// Shared dark text-field chrome matching the app's surfaces.
    func fieldStyle() -> some View {
        self
            .padding(.horizontal, 14)
            .padding(.vertical, 12)
            .background(Theme.surface)
            .foregroundStyle(Theme.textPrimary)
            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .stroke(Theme.border, lineWidth: 1)
            )
    }
}
