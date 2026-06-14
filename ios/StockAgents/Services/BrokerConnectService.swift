import AuthenticationServices
import Foundation
import UIKit

/// Errors from the `ASWebAuthenticationSession` half of the connect flow.
enum BrokerConnectError: LocalizedError, Equatable {
    case invalidAuthorizationURL
    /// The user dismissed the system auth sheet. Treated as a benign back-out.
    case cancelled
    case couldNotStart
    case noCallback
    case server(String)

    var errorDescription: String? {
        switch self {
        case .invalidAuthorizationURL:
            return "The server returned an invalid authorization URL."
        case .cancelled:
            return "Connection cancelled."
        case .couldNotStart:
            return "Could not start the authentication session."
        case .noCallback:
            return "The authentication session ended without a result."
        case .server(let message):
            return message
        }
    }

    static func from(_ error: Error) -> BrokerConnectError {
        if let asError = error as? ASWebAuthenticationSessionError,
           asError.code == .canceledLogin {
            return .cancelled
        }
        return .server(error.localizedDescription)
    }
}

/// Drives the `ASWebAuthenticationSession` half of the server-mediated Robinhood
/// OAuth flow (plan §5.1.3). The phone only **launches** the authorization URL
/// and **observes** the `stockagents://oauth/robinhood?status=...` bounce-back;
/// it never sees `code`/`state` or any brokerage token (those stay server-side).
/// Connection is then confirmed by polling `GET /api/v2/robinhood/status`.
@MainActor
final class BrokerConnectService: NSObject {
    /// Must match the `CFBundleURLSchemes` entry in Info.plist and the scheme of
    /// `MOBILE_OAUTH_APP_REDIRECT` (`stockagents://oauth/robinhood`).
    static let callbackScheme = "stockagents"

    private var session: ASWebAuthenticationSession?

    /// Present the system auth sheet for `authorizationURL` and resolve with the
    /// app-scheme callback URL the server 302s to. Throws
    /// `BrokerConnectError.cancelled` when the user dismisses the sheet.
    ///
    /// `prefersEphemeralWebBrowserSession` is left **off** so an existing
    /// Robinhood web session can be reused (fewer re-logins); the brokerage
    /// session cookie never reaches app code regardless.
    func authenticate(authorizationURL: URL) async throws -> URL {
        cancel()
        return try await withCheckedThrowingContinuation { continuation in
            let session = ASWebAuthenticationSession(
                url: authorizationURL,
                callbackURLScheme: Self.callbackScheme
            ) { callbackURL, error in
                if let callbackURL {
                    continuation.resume(returning: callbackURL)
                } else if let error {
                    continuation.resume(throwing: BrokerConnectError.from(error))
                } else {
                    continuation.resume(throwing: BrokerConnectError.noCallback)
                }
            }
            session.presentationContextProvider = self
            self.session = session
            if !session.start() {
                self.session = nil
                continuation.resume(throwing: BrokerConnectError.couldNotStart)
            }
        }
    }

    func cancel() {
        session?.cancel()
        session = nil
    }

    /// Parse the `status` query item from the bounce-back URL
    /// (`stockagents://oauth/robinhood?status=ok|error&reason=...`). The server
    /// status poll remains authoritative; this only short-circuits the UI copy.
    static func callbackStatus(_ url: URL) -> (status: String?, reason: String?) {
        let components = URLComponents(url: url, resolvingAgainstBaseURL: false)
        let items = components?.queryItems ?? []
        let status = items.first { $0.name == "status" }?.value
        let reason = items.first { $0.name == "reason" }?.value
        return (status, reason)
    }
}

extension BrokerConnectService: ASWebAuthenticationPresentationContextProviding {
    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        let scene = UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .first { $0.activationState == .foregroundActive }
        return scene?.keyWindow ?? ASPresentationAnchor()
    }
}
