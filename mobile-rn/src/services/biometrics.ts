import * as LocalAuthentication from 'expo-local-authentication';

/**
 * The mandatory biometric submission gate for LIVE order execution (iOS plan
 * §5.3, decision 4), ported from the Swift `BiometricAuthenticator`. Every
 * real-money order must pass an `expo-local-authentication` evaluation — Face ID
 * / Touch ID, falling back to the device passcode when biometrics are
 * unavailable / unenrolled. The gate is **never skipped** for a LIVE order:
 * `evaluate()` returns normally ONLY on a successful authentication and throws
 * otherwise, so the caller's network call can be placed strictly after it.
 *
 * RN/Expo caveat vs. Swift: there is no public pre-flight equivalent of
 * `LAContext.canEvaluatePolicy(.deviceOwnerAuthentication)` to detect whether a
 * device *passcode* is set. So `availableKind()` is best-effort — it reports
 * `passcode` for a device with no enrolled biometrics (a passcode may still be
 * set) and only reports `none` when there is no biometric hardware at all. The
 * AUTHORITATIVE check is `evaluate()`: if no device authentication exists,
 * `authenticateAsync` fails and we throw `unavailable`, blocking the order. The
 * security guarantee therefore does not depend on `availableKind()` accuracy.
 */

/** What the device can offer right now, used to label the confirm action. */
export type BiometryKind = 'faceID' | 'touchID' | 'passcode' | 'none';

/** Human label for the confirm button / copy. */
export function biometryLabel(kind: BiometryKind): string {
  switch (kind) {
    case 'faceID':
      return 'Face ID';
    case 'touchID':
      return 'Touch ID';
    case 'passcode':
      return 'device passcode';
    case 'none':
      return 'device authentication';
    default: {
      const _never: never = kind;
      return _never;
    }
  }
}

/** Why the gate failed; mirrors the Swift `BiometricAuthenticator.GateError`. */
export type BiometricGateErrorKind = 'unavailable' | 'cancelled' | 'failed';

export class BiometricGateError extends Error {
  readonly kind: BiometricGateErrorKind;

  constructor(kind: BiometricGateErrorKind, message: string) {
    super(message);
    this.name = 'BiometricGateError';
    this.kind = kind;
  }

  static unavailable(): BiometricGateError {
    return new BiometricGateError(
      'unavailable',
      'Face ID / Touch ID or a device passcode is required to place a LIVE order. Enable one in Settings, then try again.',
    );
  }

  static cancelled(): BiometricGateError {
    return new BiometricGateError(
      'cancelled',
      'Authentication was cancelled. The order was not placed.',
    );
  }

  static failed(detail: string): BiometricGateError {
    return new BiometricGateError(
      'failed',
      `Authentication failed: ${detail}. The order was not placed.`,
    );
  }
}

export function isBiometricGateError(value: unknown): value is BiometricGateError {
  return value instanceof BiometricGateError;
}

class Biometrics {
  /**
   * Determine the strongest authentication currently available, for labeling the
   * confirm action and (best-effort) disabling the LIVE button when nothing is
   * available. See the class note on the RN passcode-detection caveat.
   */
  async availableKind(): Promise<BiometryKind> {
    const hasHardware = await LocalAuthentication.hasHardwareAsync();
    const enrolled = await LocalAuthentication.isEnrolledAsync();
    if (hasHardware && enrolled) {
      const types = await LocalAuthentication.supportedAuthenticationTypesAsync();
      if (types.includes(LocalAuthentication.AuthenticationType.FACIAL_RECOGNITION)) {
        return 'faceID';
      }
      if (types.includes(LocalAuthentication.AuthenticationType.FINGERPRINT)) {
        return 'touchID';
      }
      // Some other biometric (e.g. iris) — still a biometric gate; fall through
      // to the passcode label rather than over-claiming Face/Touch ID.
      return 'passcode';
    }
    if (!hasHardware) return 'none';
    // Hardware present but no enrolled biometrics — a device passcode may still
    // be set; let the user try and let `evaluate()` be authoritative.
    return 'passcode';
  }

  /**
   * Evaluate the gate. Resolves normally **only on success**; throws a
   * `BiometricGateError` on cancel / failure / unavailable so the caller can
   * block submission. `disableDeviceFallback` is left false so the device
   * passcode is an accepted fallback — but authentication is never skipped.
   */
  async evaluate(reason: string): Promise<void> {
    let result: LocalAuthentication.LocalAuthenticationResult;
    try {
      result = await LocalAuthentication.authenticateAsync({
        promptMessage: reason,
        cancelLabel: 'Cancel',
        disableDeviceFallback: false,
      });
    } catch (err) {
      throw BiometricGateError.failed(err instanceof Error ? err.message : String(err));
    }
    if (result.success) return;
    throw mapAuthError(result.error);
  }
}

/** Map an `authenticateAsync` failure code to a typed gate error. */
function mapAuthError(error: string | undefined): BiometricGateError {
  switch (error) {
    case 'user_cancel':
    case 'system_cancel':
    case 'app_cancel':
    case 'user_fallback':
      return BiometricGateError.cancelled();
    case 'not_available':
    case 'not_enrolled':
    case 'passcode_not_set':
      return BiometricGateError.unavailable();
    default:
      return BiometricGateError.failed(error ?? 'unknown error');
  }
}

export const biometrics = new Biometrics();
