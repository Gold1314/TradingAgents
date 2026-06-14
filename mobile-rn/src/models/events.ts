import type { JsonValue } from './json';
import type { OrderIntent } from './order';
import { parseOrderIntent } from './order';

/**
 * The full SSE event union streamed by `GET /api/runs/{run_id}/events`.
 *
 * Each SSE `data:` frame is a JSON object whose `type` field discriminates the
 * payload. This union mirrors every event `manager.emit(...)` produces in
 * `web/server.py` and that `handleEvent()` consumes in `index.html`, and is the
 * TypeScript port of the Swift `AgentEvent` enum.
 *
 * Decoding (`parseAgentEvent`) reads `type` first, then builds the matching
 * variant. The switch is **exhaustive** over the known wire types with a
 * `never`-checked default, so a newly added server event type forces a
 * compile-time decision here (per the repo's exhaustive-switch rule). An
 * unrecognized `type` decodes to `{ type: 'unknown' }` so the stream never dies
 * on a benign new event.
 */
export type AgentEvent =
  | NodesEvent
  | StatusEvent
  | MemoryEvent
  | IdentityEvent
  | AccountEvent
  | AgentReportEvent
  | GraphEvent
  | ProgressEvent
  | UsageEvent
  | UsageSummaryEvent
  | FinalEvent
  | TradeEvent
  | ErrorEvent
  | DoneEvent
  | UnknownEvent;

/** `nodes` — planned agent order; build the stepper/pipeline. */
export interface NodesEvent {
  type: 'nodes';
  nodes: string[];
}

/** `status` — status pill text. */
export interface StatusEvent {
  type: 'status';
  message: string;
}

/** `memory` — cross-run memory lifecycle (mostly silent in the UI). */
export interface MemoryEvent {
  type: 'memory';
  phase: string;
  status: string | null;
  resolved: number | null;
  hasContext: boolean | null;
}

/** `identity` — resolved instrument identity block. */
export interface IdentityEvent {
  type: 'identity';
  content: string;
}

/** `account` — Robinhood account grounding (trading UI deferred). */
export interface AccountEvent {
  type: 'account';
  connected: boolean;
  buyingPower: number | null;
  portfolioValue: number | null;
  position: JsonValue | null;
  message: string | null;
}

export type AgentStatus = 'running' | 'done';

/** `agent` — one agent card; `content` carries the markdown report when done. */
export interface AgentReportEvent {
  type: 'agent';
  node: string;
  status: AgentStatus;
  content: string | null;
}

/** `graph` — tool-node activity (UI ignores; modeled for completeness). */
export interface GraphEvent {
  type: 'graph';
  node: string;
  status: string;
  tools: string[];
}

/** `progress` — stepper progress sync. */
export interface ProgressEvent {
  type: 'progress';
  active: string | null;
  completed: string[];
  node: string;
  status: string | null;
}

/**
 * `usage` — per-agent token/cost (live economics). The same shape is reused as
 * the per-node entry inside `usage_summary.nodes`, hence the exported `Usage`.
 */
export interface Usage {
  node: string;
  inputTokens: number;
  outputTokens: number;
  totalTokens: number;
  calls: number;
  model: string | null;
  cost: number | null;
}

export interface UsageEvent extends Usage {
  type: 'usage';
}

export interface UsageTotals {
  inputTokens: number;
  outputTokens: number;
  totalTokens: number;
  cost: number | null;
}

/** `usage_summary` — authoritative end-of-run economics. */
export interface UsageSummaryEvent {
  type: 'usage_summary';
  nodes: Record<string, Usage>;
  totals: UsageTotals;
}

/** `final` — the 5-tier verdict + PM rationale. */
export interface FinalEvent {
  type: 'final';
  decision: string;
  content: string;
}

/** `trade` — Robinhood order lifecycle (proposed/placed/dry_run/skipped/error). */
export interface TradeEvent {
  type: 'trade';
  status: string;
  tradeMode: string | null;
  canPlaceRealOrders: boolean | null;
  dryRun: boolean | null;
  orderId: string | null;
  message: string | null;
  intent: OrderIntent | null;
}

/** `error` — run failed. */
export interface ErrorEvent {
  type: 'error';
  message: string;
  trace: string | null;
}

/** `done` — terminal; close the stream. */
export interface DoneEvent {
  type: 'done';
}

/** Forward-compatibility escape hatch for an unrecognized server `type`. */
export interface UnknownEvent {
  type: 'unknown';
  rawType: string;
}

// MARK: - Defensive coercion helpers

type Raw = Record<string, unknown>;

function asObject(value: unknown): Raw {
  return value && typeof value === 'object' ? (value as Raw) : {};
}
function asString(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback;
}
function asStringOrNull(value: unknown): string | null {
  return typeof value === 'string' ? value : null;
}
function asNumberOrNull(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}
function asInt(value: unknown, fallback = 0): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}
function asBoolOrNull(value: unknown): boolean | null {
  return typeof value === 'boolean' ? value : null;
}
function asStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === 'string') : [];
}
function asAgentStatus(value: unknown): AgentStatus {
  return value === 'done' ? 'done' : 'running';
}

function parseUsage(raw: Raw, fallbackNode = ''): Usage {
  return {
    node: asString(raw.node, fallbackNode),
    inputTokens: asInt(raw.input_tokens),
    outputTokens: asInt(raw.output_tokens),
    totalTokens: asInt(raw.total_tokens),
    calls: asInt(raw.calls),
    model: asStringOrNull(raw.model),
    cost: asNumberOrNull(raw.cost),
  };
}

/**
 * Parse one decoded SSE data frame (already `JSON.parse`d) into a typed
 * `AgentEvent`. The switch is exhaustive over the known wire `type` values; the
 * `default` branch handles an unrecognized type as `unknown` (a `never` check is
 * unnecessary here because the discriminant is a runtime string, but every known
 * case is enumerated so adding a variant is a deliberate edit).
 */
export function parseAgentEvent(input: unknown): AgentEvent {
  const raw = asObject(input);
  const type = asString(raw.type);
  switch (type) {
    case 'nodes':
      return { type: 'nodes', nodes: asStringArray(raw.nodes) };
    case 'status':
      return { type: 'status', message: asString(raw.message) };
    case 'memory':
      return {
        type: 'memory',
        phase: asString(raw.phase),
        status: asStringOrNull(raw.status),
        resolved: asNumberOrNull(raw.resolved),
        hasContext: asBoolOrNull(raw.has_context),
      };
    case 'identity':
      return { type: 'identity', content: asString(raw.content) };
    case 'account':
      return {
        type: 'account',
        connected: raw.connected === true,
        buyingPower: asNumberOrNull(raw.buying_power),
        portfolioValue: asNumberOrNull(raw.portfolio_value),
        position: (raw.position ?? null) as JsonValue | null,
        message: asStringOrNull(raw.message),
      };
    case 'agent':
      return {
        type: 'agent',
        node: asString(raw.node),
        status: asAgentStatus(raw.status),
        content: asStringOrNull(raw.content),
      };
    case 'graph':
      return {
        type: 'graph',
        node: asString(raw.node),
        status: asString(raw.status),
        tools: asStringArray(raw.tools),
      };
    case 'progress':
      return {
        type: 'progress',
        active: asStringOrNull(raw.active),
        completed: asStringArray(raw.completed),
        node: asString(raw.node),
        status: asStringOrNull(raw.status),
      };
    case 'usage':
      return { type: 'usage', ...parseUsage(raw) };
    case 'usage_summary': {
      const rawNodes = asObject(raw.nodes);
      const nodes: Record<string, Usage> = {};
      for (const [key, value] of Object.entries(rawNodes)) {
        nodes[key] = parseUsage(asObject(value), key);
      }
      const totals = asObject(raw.totals);
      return {
        type: 'usage_summary',
        nodes,
        totals: {
          inputTokens: asInt(totals.input_tokens),
          outputTokens: asInt(totals.output_tokens),
          totalTokens: asInt(totals.total_tokens),
          cost: asNumberOrNull(totals.cost),
        },
      };
    }
    case 'final':
      return { type: 'final', decision: asString(raw.decision), content: asString(raw.content) };
    case 'trade':
      return {
        type: 'trade',
        status: asString(raw.status),
        tradeMode: asStringOrNull(raw.trade_mode),
        canPlaceRealOrders: asBoolOrNull(raw.can_place_real_orders),
        dryRun: asBoolOrNull(raw.dry_run),
        orderId: asStringOrNull(raw.order_id),
        message: asStringOrNull(raw.message),
        intent: raw.intent ? parseOrderIntent(raw.intent) : null,
      };
    case 'error':
      return { type: 'error', message: asString(raw.message), trace: asStringOrNull(raw.trace) };
    case 'done':
      return { type: 'done' };
    default:
      return { type: 'unknown', rawType: type };
  }
}
