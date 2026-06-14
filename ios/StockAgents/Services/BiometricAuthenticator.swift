import Foundation
import LocalAuthentication

/// The mandatory biometric submission gate for LIVE order execution (plan §5.3,
/// decision 4). Every real-money order must pass a `LocalAuthentication`
/// evaluation — Face ID / Touch ID, falling back to the device passcode when
/// biometrics are unavailable/unenrolled. The gate is **never skipped** for a
/// LIVE order: if no device authentication is available at all, evaluation
/// throws and submission is blocked.
struct BiometricAuthenticator {
    /// What the device can offer right now, used to label the confirm action.
    enum Kind: Equatable {
        case faceID
        case touchID
        case passcodeOnly   // no enrolled biometrics; device-passcode fallback
        case none           // no authentication available — gate cannot pass

        /// Human label for the confirm button / copy.
        var label: String {
            switch self {
            case .faceID: return "Face ID"
            case .touchID: return "Touch ID"
            case .passcodeOnly: return "device passcode"
            case .none: return "device authentication"
            }
        }
    }

    enum GateError: LocalizedError, Equatable {
        /// No biometrics AND no passcode set — cannot gate a LIVE order.
        case unavailable
        /// The user cancelled the prompt (or tapped fallback that we don't allow).
        case cancelled
        /// Evaluation ran but did not succeed.
        case failed(String)

        var errorDescription: String? {
            switch self {
            case .unavailable:
                return "Face ID / Touch ID or a device passcode is required to place a LIVE order. Enable one in Settings, then try again."
            case .cancelled:
                return "Authentication was cancelled. The order was not placed."
            case .failed(let message):
                return "Authentication failed: \(message). The order was not placed."
            }
        }
    }

    /// Determine the strongest authentication currently available. Must call
    /// `canEvaluatePolicy` before reading `biometryType` (only populated after).
    func availableKind() -> Kind {
        let context = LAContext()
        var error: NSError?
        if context.canEvaluatePolicy(.deviceOwnerAuthenticationWithBiometrics, error: &error) {
            switch context.biometryType {
            case .faceID: return .faceID
            case .touchID: return .touchID
            default: return .passcodeOnly
            }
        }
        if context.canEvaluatePolicy(.deviceOwnerAuthentication, error: &error) {
            return .passcodeOnly
        }
        return .none
    }

    /// Evaluate the gate. Returns normally **only on success**; throws on
    /// cancel / failure / unavailable so the caller can block submission.
    ///
    /// A fresh `LAContext` is used per call so a prior successful evaluation can
    /// never be silently reused to wave an order through.
    func evaluate(reason: String) async throws {
        let context = LAContext()
        var error: NSError?
        let policy: LAPolicy
        if context.canEvaluatePolicy(.deviceOwnerAuthenticationWithBiometrics, error: &error) {
            policy = .deviceOwnerAuthenticationWithBiometrics
        } else if context.canEvaluatePolicy(.deviceOwnerAuthentication, error: &error) {
            // No usable biometrics — fall back to passcode, but never skip.
            policy = .deviceOwnerAuthentication
        } else {
            throw GateError.unavailable
        }

        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            context.evaluatePolicy(policy, localizedReason: reason) { success, evalError in
                if success {
                    continuation.resume()
                } else {
                    continuation.resume(throwing: BiometricAuthenticator.map(evalError))
                }
            }
        }
    }

    private static func map(_ error: Error?) -> GateError {
        guard let laError = error as? LAError else {
            return .failed(error?.localizedDescription ?? "unknown error")
        }
        switch laError.code {
        case .userCancel, .systemCancel, .appCancel:
            return .cancelled
        case .biometryNotAvailable, .biometryNotEnrolled, .passcodeNotSet:
            return .unavailable
        default:
            return .failed(laError.localizedDescription)
        }
    }
}
