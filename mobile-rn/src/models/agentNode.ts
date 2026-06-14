import type { AgentStatus } from './events';

/**
 * The five pipeline stages, ported verbatim from `STAGES` in
 * `web/static/index.html`. Order is the on-screen left→right stepper order.
 */
export type Stage =
  | 'Analysis'
  | 'Research Debate'
  | 'Trading Plan'
  | 'Risk Debate'
  | 'Decision';

export const STAGE_ORDER: Stage[] = [
  'Analysis',
  'Research Debate',
  'Trading Plan',
  'Risk Debate',
  'Decision',
];

export const STAGE_META: Record<Stage, { icon: string; color: string }> = {
  Analysis: { icon: '🔎', color: '#38bdf8' },
  'Research Debate': { icon: '⚔️', color: '#a78bfa' },
  'Trading Plan': { icon: '💼', color: '#fbbf24' },
  'Risk Debate': { icon: '🛡️', color: '#fb7185' },
  Decision: { icon: '🎯', color: '#34d399' },
};

/** Per-agent display metadata, ported from the `NODE` map in `index.html`. */
export interface AgentNode {
  name: string;
  stage: Stage;
  icon: string;
  color: string;
  blurb: string;
}

/**
 * The full `NODE` map keyed by the display name the backend emits. The four
 * analysts are selectable; the eight fixed agents always run after them
 * (matches `FIXED_NODES` in `server.py`).
 */
export const AGENT_NODES: Record<string, AgentNode> = {
  'Market Analyst': { name: 'Market Analyst', stage: 'Analysis', icon: '📈', color: '#38bdf8', blurb: 'Price action & technicals' },
  'Sentiment Analyst': { name: 'Sentiment Analyst', stage: 'Analysis', icon: '💬', color: '#22d3ee', blurb: 'Social & retail sentiment' },
  'News Analyst': { name: 'News Analyst', stage: 'Analysis', icon: '📰', color: '#818cf8', blurb: 'Macro & headlines' },
  'Fundamentals Analyst': { name: 'Fundamentals Analyst', stage: 'Analysis', icon: '📊', color: '#0ea5e9', blurb: 'Financials & valuation' },
  'Bull Researcher': { name: 'Bull Researcher', stage: 'Research Debate', icon: '🐂', color: '#34d399', blurb: 'The case to buy' },
  'Bear Researcher': { name: 'Bear Researcher', stage: 'Research Debate', icon: '🐻', color: '#fb7185', blurb: 'The case to sell' },
  'Research Manager': { name: 'Research Manager', stage: 'Research Debate', icon: '🧭', color: '#a78bfa', blurb: 'Judges the debate' },
  Trader: { name: 'Trader', stage: 'Trading Plan', icon: '💼', color: '#fbbf24', blurb: 'Concrete trade plan' },
  'Aggressive Analyst': { name: 'Aggressive Analyst', stage: 'Risk Debate', icon: '🚀', color: '#fb923c', blurb: 'Risk-on view' },
  'Conservative Analyst': { name: 'Conservative Analyst', stage: 'Risk Debate', icon: '🛡️', color: '#38bdf8', blurb: 'Risk-off view' },
  'Neutral Analyst': { name: 'Neutral Analyst', stage: 'Risk Debate', icon: '⚖️', color: '#94a3b8', blurb: 'Balanced view' },
  'Portfolio Manager': { name: 'Portfolio Manager', stage: 'Decision', icon: '🎯', color: '#34d399', blurb: 'Final call' },
};

/** Metadata for a node, with a neutral fallback for unknown names. */
export function agentMetadata(name: string): AgentNode {
  return (
    AGENT_NODES[name] ?? { name, stage: 'Analysis', icon: '🤖', color: '#94a3b8', blurb: '' }
  );
}

/** Map a wire analyst key ("market") to its display name ("Market Analyst"). */
export const ANALYST_DISPLAY_NAME: Record<string, string> = {
  market: 'Market Analyst',
  social: 'Sentiment Analyst',
  news: 'News Analyst',
  fundamentals: 'Fundamentals Analyst',
};

/** The Market Analyst is where the price chart is hosted (web parity). */
export const CHART_HOST_NODE = 'Market Analyst';

/** Live per-agent UI state projected from the SSE stream. */
export interface AgentCardState {
  node: string;
  status: AgentStatus;
  content: string | null;
}
