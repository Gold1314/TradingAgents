/**
 * Request/response models for the run lifecycle, mirroring the `RunRequest` /
 * config / start-response shapes in `web/server.py` (and the Swift models).
 * Typed in camelCase; the wire is snake_case — conversion happens in apiClient.
 */

export type AssetType = 'stock' | 'crypto' | 'forex' | 'commodity';
export const ASSET_TYPES: AssetType[] = ['stock', 'crypto', 'forex', 'commodity'];

/** Body of `POST /api/runs`. `ticker`/`tradeDate` required; rest fall back to server defaults. */
export interface RunRequest {
  ticker: string;
  tradeDate: string; // YYYY-MM-DD
  analysts: string[];
  assetType: AssetType;
  provider: string | null;
  deepModel: string | null;
  quickModel: string | null;
  maxDebateRounds: number | null;
  maxRiskRounds: number | null;
  force: boolean;
}

/** Serialize a `RunRequest` to the snake_case wire body. */
export function runRequestToWire(req: RunRequest): Record<string, unknown> {
  return {
    ticker: req.ticker,
    trade_date: req.tradeDate,
    analysts: req.analysts,
    asset_type: req.assetType,
    provider: req.provider,
    deep_model: req.deepModel,
    quick_model: req.quickModel,
    max_debate_rounds: req.maxDebateRounds,
    max_risk_rounds: req.maxRiskRounds,
    force: req.force,
  };
}

/** The four selectable analysts, in stable display order (matches the server). */
export const ANALYST_ORDER = ['market', 'social', 'news', 'fundamentals'];

/** Response of `GET /api/config` (`get_config()`), drives the run form defaults. */
export interface RunConfig {
  provider: string | null;
  deepModel: string | null;
  quickModel: string | null;
  maxDebateRounds: number | null;
  maxRiskRounds: number | null;
  analysts: string[];
  analystDisplay: Record<string, string>;
  supabaseConfigured: boolean | null;
  adminAvailable: boolean | null;
  cacheWindowMinutes: number | null;
}

function num(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}
function str(v: unknown): string | null {
  return typeof v === 'string' ? v : null;
}
function bool(v: unknown): boolean | null {
  return typeof v === 'boolean' ? v : null;
}

export function parseRunConfig(input: unknown): RunConfig {
  const raw = (input && typeof input === 'object' ? input : {}) as Record<string, unknown>;
  const analysts = Array.isArray(raw.analysts)
    ? raw.analysts.filter((a): a is string => typeof a === 'string')
    : [];
  const display: Record<string, string> = {};
  const rawDisplay = (raw.analyst_display && typeof raw.analyst_display === 'object'
    ? raw.analyst_display
    : {}) as Record<string, unknown>;
  for (const [k, v] of Object.entries(rawDisplay)) {
    if (typeof v === 'string') display[k] = v;
  }
  return {
    provider: str(raw.provider),
    deepModel: str(raw.deep_model),
    quickModel: str(raw.quick_model),
    maxDebateRounds: num(raw.max_debate_rounds),
    maxRiskRounds: num(raw.max_risk_rounds),
    analysts,
    analystDisplay: display,
    supabaseConfigured: bool(raw.supabase_configured),
    adminAvailable: bool(raw.admin_available),
    cacheWindowMinutes: num(raw.cache_window_minutes),
  };
}

/** One persisted agent report inside a cached run (`{seq, agent, content}`). */
export interface CachedAgentOutput {
  seq: number;
  agent: string;
  content: string;
}

/** A persisted run served from the 60-minute cache (shape from `db.get_recent_run`). */
export interface CachedRun {
  /**
   * The original Supabase `runs.id` row — same value the voice router accepts
   * on `POST /api/voice/sessions` via its `voice_db.get_run_owner` fallback
   * path. Carrying it on cache hits lets the run-session screen keep voice
   * affordances enabled (Talk button, handoff) without needing to re-create
   * an in-memory run entry.
   */
  runId: string | null;
  ticker: string;
  tradeDate: string;
  assetType: string | null;
  decision: string | null;
  finalContent: string | null;
  identity: string | null;
  createdAt: string | null;
  agents: CachedAgentOutput[];
}

/**
 * Response of `POST /api/runs` (`start_run()`): either a fresh run started or a
 * 60-minute cache hit, discriminated by `cached`.
 */
export type StartRunResponse =
  | { cached: false; runId: string; nodes: string[] }
  | { cached: true; run: CachedRun };

export function parseStartRunResponse(input: unknown): StartRunResponse {
  const raw = (input && typeof input === 'object' ? input : {}) as Record<string, unknown>;
  if (raw.cached === true) {
    const r = (raw.run && typeof raw.run === 'object' ? raw.run : {}) as Record<string, unknown>;
    const agents: CachedAgentOutput[] = Array.isArray(r.agents)
      ? r.agents.map((a) => {
          const o = (a ?? {}) as Record<string, unknown>;
          return {
            seq: typeof o.seq === 'number' ? o.seq : 0,
            agent: typeof o.agent === 'string' ? o.agent : '',
            content: typeof o.content === 'string' ? o.content : '',
          };
        })
      : [];
    return {
      cached: true,
      run: {
        // Supabase serializes the row's primary key as `id`. The cache hit
        // payload is the raw row dict — see web/db.py:get_recent_run.
        runId: typeof r.id === 'string' ? r.id : str(r.run_id),
        ticker: typeof r.ticker === 'string' ? r.ticker : '',
        tradeDate: typeof r.trade_date === 'string' ? r.trade_date : '',
        assetType: str(r.asset_type),
        decision: str(r.decision),
        finalContent: str(r.final_content),
        identity: str(r.identity),
        createdAt: str(r.created_at),
        agents,
      },
    };
  }
  return {
    cached: false,
    runId: typeof raw.run_id === 'string' ? raw.run_id : '',
    nodes: Array.isArray(raw.nodes) ? raw.nodes.filter((n): n is string => typeof n === 'string') : [],
  };
}

/** Response of `GET /api/runs/{run_id}` (`run_status()`), used for session restore. */
export interface RunStatus {
  runId: string;
  active: boolean;
  finished: boolean;
  nodes: string[];
  events: number;
}

export function parseRunStatus(input: unknown): RunStatus {
  const raw = (input && typeof input === 'object' ? input : {}) as Record<string, unknown>;
  return {
    runId: typeof raw.run_id === 'string' ? raw.run_id : '',
    active: raw.active === true,
    finished: raw.finished === true,
    nodes: Array.isArray(raw.nodes) ? raw.nodes.filter((n): n is string => typeof n === 'string') : [],
    events: typeof raw.events === 'number' ? raw.events : 0,
  };
}
