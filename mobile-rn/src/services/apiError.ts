/**
 * Typed networking errors, mapped to user-friendly copy. Mirrors the Swift
 * `APIError` (incl. 401 + 429-with-Retry-After).
 */
export type ApiError =
  | { kind: 'invalidUrl' }
  | { kind: 'transport'; message: string }
  | { kind: 'timeout' }
  | { kind: 'decoding'; message: string }
  | { kind: 'unauthorized' }
  | { kind: 'rateLimited'; retryAfter: number | null }
  | { kind: 'http'; status: number; detail: string | null }
  | { kind: 'cancelled' };

export function apiErrorMessage(error: ApiError): string {
  switch (error.kind) {
    case 'invalidUrl':
      return 'The backend URL is invalid. Check it in Settings.';
    case 'transport':
      return `Network error: ${error.message}`;
    case 'timeout':
      return 'The request timed out. Check your connection and the backend URL.';
    case 'decoding':
      return `Could not read the server response: ${error.message}`;
    case 'unauthorized':
      return 'Your session expired. Please sign in again.';
    case 'rateLimited':
      return error.retryAfter != null
        ? `Daily limit reached. Try again in about ${error.retryAfter}s.`
        : 'Daily limit reached. Please try again later.';
    case 'http':
      return error.detail ?? `Request failed (HTTP ${error.status}).`;
    case 'cancelled':
      return 'The request was cancelled.';
    default: {
      // Exhaustiveness guard: a new ApiError variant forces a compile error here.
      const _never: never = error;
      return String(_never);
    }
  }
}

/** Narrowing helper for `unknown` caught in try/catch. */
export function isApiError(value: unknown): value is ApiError {
  return Boolean(value && typeof value === 'object' && 'kind' in (value as object));
}

/** Extract a friendly message from any thrown value. */
export function describeError(value: unknown): string {
  if (isApiError(value)) return apiErrorMessage(value);
  if (value instanceof Error) return value.message;
  return String(value);
}
