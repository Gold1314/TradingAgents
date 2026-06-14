/**
 * Minimal type for the handful of backend fields that are intentionally loosely
 * typed (e.g. the `position` blob in the `account` SSE event, whose shape
 * depends on the broker). Mirrors the Swift `JSONValue`.
 */
export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue };
