import Foundation

/// Observable sign-in state the UI drives off of (plan §3.4.1). The backend
/// `current_user` dependency resolves a stable `user_id` from the bearer token,
/// so the client only needs to know *whether* it has a valid session and who it
/// belongs to.
enum AuthState: Equatable {
    case signedOut
    case signedIn(AuthUser)
}

/// The authenticated principal, mirroring the Supabase `auth.users` row the
/// backend keys runs/broker tokens on (`sub` = `id`, plus `email` for display).
struct AuthUser: Equatable {
    let id: String
    let email: String?
}

/// A Supabase Auth (GoTrue) token response from
/// `POST {SUPABASE_URL}/auth/v1/token` (both `grant_type=password` and
/// `grant_type=refresh_token` return this shape).
struct SupabaseSession: Decodable, Equatable {
    let accessToken: String
    let refreshToken: String
    /// Seconds until `accessToken` expires (always present).
    let expiresIn: Int?
    /// Absolute expiry as Unix epoch seconds (present on newer GoTrue).
    let expiresAt: Int?
    let user: SupabaseUser?

    enum CodingKeys: String, CodingKey {
        case accessToken = "access_token"
        case refreshToken = "refresh_token"
        case expiresIn = "expires_in"
        case expiresAt = "expires_at"
        case user
    }

    /// Absolute expiry as Unix epoch seconds. Prefers the server-provided
    /// `expires_at`; otherwise derives it from `expires_in` relative to now.
    var expiryEpoch: Int {
        if let expiresAt { return expiresAt }
        return Int(Date().timeIntervalSince1970) + (expiresIn ?? 3600)
    }

    var authUser: AuthUser {
        AuthUser(id: user?.id ?? "", email: user?.email)
    }
}

/// The `user` object embedded in a GoTrue session. Only the fields the app uses
/// are decoded; the rest of the (large) Supabase user record is ignored.
struct SupabaseUser: Decodable, Equatable {
    let id: String
    let email: String?
}

/// A GoTrue error body. GoTrue has used a few shapes over time
/// (`error`/`error_description`, `msg`, `message`, `error_code`); decode them
/// all leniently so the user sees a meaningful message (e.g. "Invalid login
/// credentials") instead of a bare status code.
struct SupabaseAuthErrorBody: Decodable {
    let error: String?
    let errorDescription: String?
    let msg: String?
    let message: String?
    let errorCode: String?

    enum CodingKeys: String, CodingKey {
        case error
        case errorDescription = "error_description"
        case msg
        case message
        case errorCode = "error_code"
    }

    var displayMessage: String? {
        errorDescription ?? msg ?? message ?? error
    }
}
