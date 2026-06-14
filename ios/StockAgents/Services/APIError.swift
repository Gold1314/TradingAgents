import Foundation

/// Errors surfaced by the networking layer, mapped to user-friendly copy.
enum APIError: LocalizedError, Equatable {
    case invalidURL
    case transport(String)
    case decoding(String)
    /// Non-2xx response. `detail` is FastAPI's `{"detail": ...}` body when present.
    case http(status: Int, detail: String?)
    case unauthorized
    /// 429 from the per-user rate limiter (plan §3.4.1a). `retryAfter` seconds.
    case rateLimited(retryAfter: Int?)
    case cancelled

    var errorDescription: String? {
        switch self {
        case .invalidURL:
            return "The backend URL is invalid. Check it in Settings."
        case .transport(let message):
            return "Network error: \(message)"
        case .decoding(let message):
            return "Could not read the server response: \(message)"
        case .http(let status, let detail):
            return detail ?? "Request failed (HTTP \(status))."
        case .unauthorized:
            return "Your session expired. Please sign in again."
        case .rateLimited(let retryAfter):
            if let retryAfter {
                return "Daily limit reached. Try again in about \(retryAfter)s."
            }
            return "Daily limit reached. Please try again later."
        case .cancelled:
            return "The request was cancelled."
        }
    }
}
