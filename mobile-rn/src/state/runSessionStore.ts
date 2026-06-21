import { create } from 'zustand';
import type { AgentEvent, Usage, UsageTotals, AccountEvent, TradeEvent } from '../models/events';
import type { AgentCardState } from '../models/agentNode';
import { STAGE_ORDER, agentMetadata, type Stage } from '../models/agentNode';
import type { ChartPayload } from '../models/chart';
import { parseVerdict, type Verdict } from '../models/verdict';
import type { RunRequest, CachedRun } from '../models/run';

export type RunPhase = 'idle' | 'connecting' | 'streaming' | 'finished' | 'failed';

interface RunSessionState {
  // Run identity.
  runId: string | null;
  request: RunRequest | null;

  // Stream / status.
  phase: RunPhase;
  errorMessage: string | null;
  statusMessage: string;
  isCached: boolean;
  cacheCreatedAt: string | null;

  // Pipeline + feed.
  plannedNodes: string[];
  agents: Record<string, AgentCardState>;
  activeNode: string | null;
  completedNodes: string[];
  identity: string | null;

  // Verdict.
  decision: string | null;
  verdict: Verdict | null;
  rationale: string | null;

  // Economics.
  usageByNode: Record<string, Usage>;
  usageTotals: UsageTotals | null;

  // Robinhood (decoded for completeness; trading UI deferred).
  account: AccountEvent | null;
  lastTrade: TradeEvent | null;

  // Price chart.
  chart: ChartPayload | null;
  chartError: string | null;

  // Actions.
  reset: () => void;
  initStarted: (runId: string, nodes: string[], request: RunRequest) => void;
  initRestore: (runId: string, request: RunRequest, nodes: string[]) => void;
  initCached: (run: CachedRun) => void;
  setPhase: (phase: RunPhase) => void;
  markFinished: () => void;
  setFailed: (message: string) => void;
  applyEvent: (event: AgentEvent) => void;
  setChart: (chart: ChartPayload) => void;
  setChartError: (message: string) => void;
}

const initialState = {
  runId: null,
  request: null,
  phase: 'idle' as RunPhase,
  errorMessage: null,
  statusMessage: '',
  isCached: false,
  cacheCreatedAt: null,
  plannedNodes: [] as string[],
  agents: {} as Record<string, AgentCardState>,
  activeNode: null,
  completedNodes: [] as string[],
  identity: null,
  decision: null,
  verdict: null as Verdict | null,
  rationale: null,
  usageByNode: {} as Record<string, Usage>,
  usageTotals: null as UsageTotals | null,
  account: null as AccountEvent | null,
  lastTrade: null as TradeEvent | null,
  chart: null as ChartPayload | null,
  chartError: null,
};

function seedPlannedAgents(
  nodes: string[],
  existing: Record<string, AgentCardState>,
): Record<string, AgentCardState> {
  const next = { ...existing };
  for (const node of nodes) {
    if (!next[node]) next[node] = { node, status: 'running', content: null };
  }
  return next;
}

export const useRunSession = create<RunSessionState>((set, get) => ({
  ...initialState,

  reset: () => set({ ...initialState, agents: {}, usageByNode: {}, completedNodes: [] }),

  initStarted: (runId, nodes, request) =>
    set({
      ...initialState,
      agents: seedPlannedAgents(nodes, {}),
      usageByNode: {},
      completedNodes: [],
      runId,
      request,
      plannedNodes: nodes,
      isCached: false,
      phase: 'connecting',
    }),

  initRestore: (runId, request, nodes) =>
    set({
      ...initialState,
      agents: seedPlannedAgents(nodes, {}),
      usageByNode: {},
      completedNodes: [],
      runId,
      request,
      plannedNodes: nodes,
      phase: 'connecting',
    }),

  initCached: (run) => {
    const sorted = [...run.agents].sort((a, b) => a.seq - b.seq);
    const nodes: string[] = [];
    for (const o of sorted) if (!nodes.includes(o.agent)) nodes.push(o.agent);
    const agents: Record<string, AgentCardState> = {};
    const completed: string[] = [];
    for (const o of sorted) {
      agents[o.agent] = { node: o.agent, status: 'done', content: o.content };
      if (!completed.includes(o.agent)) completed.push(o.agent);
    }
    const decision = run.decision ?? 'Hold';
    set({
      ...initialState,
      agents,
      usageByNode: {},
      completedNodes: completed,
      // Carry the original Supabase run_id forward so the voice layer
      // (Talk button, handoff) keeps working on cached hits. The voice
      // router accepts persisted runs via its DB-backed owner check —
      // no in-memory manager entry required.
      runId: run.runId,
      isCached: true,
      cacheCreatedAt: run.createdAt,
      request: {
        ticker: run.ticker,
        tradeDate: run.tradeDate,
        analysts: [],
        assetType: (run.assetType as RunRequest['assetType']) ?? 'stock',
        provider: null,
        deepModel: null,
        quickModel: null,
        maxDebateRounds: null,
        maxRiskRounds: null,
        force: false,
      },
      plannedNodes: nodes,
      identity: run.identity,
      decision,
      verdict: parseVerdict(decision),
      rationale: run.finalContent ?? '',
      statusMessage: 'from cache',
      phase: 'finished',
    });
  },

  setPhase: (phase) => set({ phase }),
  markFinished: () => set({ phase: 'finished', statusMessage: 'Complete' }),
  setFailed: (message) => set({ phase: 'failed', errorMessage: message }),

  setChart: (chart) => set({ chart }),
  setChartError: (message) => set({ chartError: message }),

  // Port of `handleEvent()` in index.html / the Swift `apply(_:)`.
  applyEvent: (event) => {
    const state = get();
    if (state.phase === 'connecting') set({ phase: 'streaming' });

    switch (event.type) {
      case 'nodes':
        set((s) => ({
          plannedNodes: event.nodes,
          agents: seedPlannedAgents(event.nodes, s.agents),
        }));
        break;

      case 'status':
        set({ statusMessage: event.message });
        break;

      case 'memory':
        break; // lifecycle only; silent in the UI (web parity)

      case 'identity':
        set({ identity: event.content });
        break;

      case 'account':
        set({ account: event });
        break;

      case 'agent':
        set((s) => {
          const prev = s.agents[event.node] ?? {
            node: event.node,
            status: event.status,
            content: null,
          };
          const content = event.content && event.content !== '' ? event.content : prev.content;
          const completed =
            event.status === 'done' && !s.completedNodes.includes(event.node)
              ? [...s.completedNodes, event.node]
              : s.completedNodes;
          return {
            agents: { ...s.agents, [event.node]: { node: event.node, status: event.status, content } },
            completedNodes: completed,
          };
        });
        break;

      case 'graph':
        break; // tool-node activity; intentionally ignored (web parity)

      case 'progress':
        set((s) => {
          const completed = [...s.completedNodes];
          for (const n of event.completed) if (!completed.includes(n)) completed.push(n);
          return { activeNode: event.active, completedNodes: completed };
        });
        break;

      case 'usage':
        set((s) => ({ usageByNode: { ...s.usageByNode, [event.node]: { ...event } } }));
        break;

      case 'usage_summary':
        set((s) => ({
          usageByNode: { ...s.usageByNode, ...event.nodes },
          usageTotals: event.totals,
        }));
        break;

      case 'final':
        set({
          decision: event.decision,
          verdict: parseVerdict(event.decision),
          rationale: event.content,
        });
        break;

      case 'trade':
        set({ lastTrade: event });
        break;

      case 'error':
        set({ phase: 'failed', errorMessage: event.message });
        break;

      case 'done':
        get().markFinished();
        break;

      case 'unknown':
        break; // forward-compatible: ignore unrecognized event types

      default: {
        // Exhaustiveness guard: a new AgentEvent variant forces a compile error.
        const _never: never = event;
        return _never;
      }
    }
  },
}));

/** Agent cards in planned pipeline order (only those seen or planned). */
export function selectOrderedAgents(state: RunSessionState): AgentCardState[] {
  return state.plannedNodes
    .map((node) => state.agents[node])
    .filter((card): card is AgentCardState => Boolean(card));
}

/** Per-stage rollup for the stepper: total / completed / active. */
export function stageProgress(
  state: RunSessionState,
  stage: Stage,
): { total: number; completed: number; isActive: boolean } {
  const inStage = state.plannedNodes.filter((n) => agentMetadata(n).stage === stage);
  const completed = inStage.filter((n) => state.completedNodes.includes(n)).length;
  const isActive = state.activeNode ? agentMetadata(state.activeNode).stage === stage : false;
  return { total: inStage.length, completed, isActive };
}

/** Stages that have at least one planned node (drives which stepper cards show). */
export function visibleStages(state: RunSessionState): Stage[] {
  return STAGE_ORDER.filter((stage) =>
    state.plannedNodes.some((n) => agentMetadata(n).stage === stage),
  );
}
