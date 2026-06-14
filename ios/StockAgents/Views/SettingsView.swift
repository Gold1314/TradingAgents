import SwiftUI

/// Minimal settings: configure the backend base URL at runtime (overrides the
/// build-time `STOCKAGENTS_BASE_URL`). No secrets are stored here.
struct SettingsView: View {
    @EnvironmentObject private var env: AppEnvironment
    @Environment(\.dismiss) private var dismiss

    @State private var urlText: String = ""
    @State private var error: String?

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("https://your-backend", text: $urlText)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                    if let error {
                        Text(error).font(.caption).foregroundStyle(Theme.danger)
                    }
                } header: {
                    Text("Backend base URL")
                } footer: {
                    Text("The StockAgents FastAPI server. Use http://localhost:8000 for local dev, or your deployed HTTPS domain. Production must be HTTPS.")
                }

                Section {
                    LabeledContent("Current", value: env.baseURL.absoluteString)
                }

                Section {
                    if case .signedIn(let user) = env.auth.state {
                        LabeledContent("Signed in", value: user.email ?? user.id)
                    }
                    Button(role: .destructive) {
                        env.auth.signOut()
                        dismiss()
                    } label: {
                        Label("Sign Out", systemImage: "rectangle.portrait.and.arrow.right")
                    }
                } header: {
                    Text("Account")
                } footer: {
                    Text("Signs out of Supabase and clears the stored session from this device.")
                }
            }
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Close") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") { save() }
                }
            }
            .onAppear { urlText = env.baseURL.absoluteString }
        }
    }

    private func save() {
        let trimmed = urlText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let url = URL(string: trimmed), url.scheme != nil, url.host != nil else {
            error = "Enter a valid URL including the scheme (http:// or https://)."
            return
        }
        env.updateBaseURL(url)
        dismiss()
    }
}
