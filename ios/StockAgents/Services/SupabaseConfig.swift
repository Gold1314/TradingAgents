import Foundation

/// Supabase project endpoint + publishable anon key used by `AuthService` to
/// talk to GoTrue (`/auth/v1/*`).
///
/// **No secrets are hardcoded in source** (project rule). The values resolve in
/// priority order, mirroring `AppEnvironment.resolveBaseURL()`:
///
/// 1. **Runtime overrides** persisted in `UserDefaults` (handy for dev/staging
///    swaps without a rebuild).
/// 2. **Build-time defaults** from the `SUPABASE_URL` / `SUPABASE_ANON_KEY`
///    Info.plist keys (declared in `project.yml`).
///
/// The anon key is a *publishable* key — it is safe to ship in a client and is
/// designed to be public — but it is still kept out of source and made
/// configurable so each environment can point at its own project. The
/// service-role key (`SUPABASE_KEY` in the backend) is **never** used here.
///
/// Returns `nil` when neither a URL nor a key is configured; `AuthService` then
/// surfaces a clear "auth isn't configured" error instead of making a doomed
/// request.
struct SupabaseConfig: Equatable {
    let url: URL
    let anonKey: String

    static let urlDefaultsKey = "stockagents.supabase.url"
    static let anonKeyDefaultsKey = "stockagents.supabase.anonKey"

    private static let urlInfoKey = "SUPABASE_URL"
    private static let anonKeyInfoKey = "SUPABASE_ANON_KEY"

    /// GoTrue base (`{SUPABASE_URL}/auth/v1`).
    var authBaseURL: URL {
        url.appendingPathComponent("auth").appendingPathComponent("v1")
    }

    static func resolve(defaults: UserDefaults = .standard, bundle: Bundle = .main) -> SupabaseConfig? {
        guard
            let urlString = nonEmpty(defaults.string(forKey: urlDefaultsKey))
                ?? nonEmpty(bundle.object(forInfoDictionaryKey: urlInfoKey) as? String),
            let url = URL(string: urlString),
            let anonKey = nonEmpty(defaults.string(forKey: anonKeyDefaultsKey))
                ?? nonEmpty(bundle.object(forInfoDictionaryKey: anonKeyInfoKey) as? String)
        else {
            return nil
        }
        return SupabaseConfig(url: url, anonKey: anonKey)
    }

    private static func nonEmpty(_ value: String?) -> String? {
        guard let trimmed = value?.trimmingCharacters(in: .whitespacesAndNewlines), !trimmed.isEmpty else {
            return nil
        }
        // The Info.plist placeholder ("__SET_IN_XCCONFIG__") is treated as unset
        // so an unconfigured build fails loudly rather than hitting a bad host.
        if trimmed.hasPrefix("__") && trimmed.hasSuffix("__") { return nil }
        return trimmed
    }
}
