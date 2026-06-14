import Foundation

/// The five pipeline stages, ported verbatim from `STAGES` in
/// `web/static/index.html`. Order is the on-screen left→right stepper order.
enum Stage: String, CaseIterable, Identifiable {
    case analysis = "Analysis"
    case researchDebate = "Research Debate"
    case tradingPlan = "Trading Plan"
    case riskDebate = "Risk Debate"
    case decision = "Decision"

    var id: String { rawValue }
    var label: String { rawValue }

    /// Stage glyph + accent, mirroring `STAGES[].icon/color`.
    var icon: String {
        switch self {
        case .analysis: return "🔎"
        case .researchDebate: return "⚔️"
        case .tradingPlan: return "💼"
        case .riskDebate: return "🛡️"
        case .decision: return "🎯"
        }
    }

    var colorHex: String {
        switch self {
        case .analysis: return "#38bdf8"
        case .researchDebate: return "#a78bfa"
        case .tradingPlan: return "#fbbf24"
        case .riskDebate: return "#fb7185"
        case .decision: return "#34d399"
        }
    }
}

/// Per-agent display metadata, ported from the `NODE` map in `index.html`.
/// Keyed by the node display name the backend emits (e.g. "Market Analyst").
struct AgentNode: Equatable {
    let name: String
    let stage: Stage
    let icon: String
    let colorHex: String
    let blurb: String

    /// The full `NODE` map. The four analysts are selectable; the eight fixed
    /// agents always run after them (matches `FIXED_NODES` in `server.py`).
    static let all: [String: AgentNode] = [
        "Market Analyst":       AgentNode(name: "Market Analyst", stage: .analysis, icon: "📈", colorHex: "#38bdf8", blurb: "Price action & technicals"),
        "Sentiment Analyst":    AgentNode(name: "Sentiment Analyst", stage: .analysis, icon: "💬", colorHex: "#22d3ee", blurb: "Social & retail sentiment"),
        "News Analyst":         AgentNode(name: "News Analyst", stage: .analysis, icon: "📰", colorHex: "#818cf8", blurb: "Macro & headlines"),
        "Fundamentals Analyst": AgentNode(name: "Fundamentals Analyst", stage: .analysis, icon: "📊", colorHex: "#0ea5e9", blurb: "Financials & valuation"),
        "Bull Researcher":      AgentNode(name: "Bull Researcher", stage: .researchDebate, icon: "🐂", colorHex: "#34d399", blurb: "The case to buy"),
        "Bear Researcher":      AgentNode(name: "Bear Researcher", stage: .researchDebate, icon: "🐻", colorHex: "#fb7185", blurb: "The case to sell"),
        "Research Manager":     AgentNode(name: "Research Manager", stage: .researchDebate, icon: "🧭", colorHex: "#a78bfa", blurb: "Judges the debate"),
        "Trader":               AgentNode(name: "Trader", stage: .tradingPlan, icon: "💼", colorHex: "#fbbf24", blurb: "Concrete trade plan"),
        "Aggressive Analyst":   AgentNode(name: "Aggressive Analyst", stage: .riskDebate, icon: "🚀", colorHex: "#fb923c", blurb: "Risk-on view"),
        "Conservative Analyst": AgentNode(name: "Conservative Analyst", stage: .riskDebate, icon: "🛡️", colorHex: "#38bdf8", blurb: "Risk-off view"),
        "Neutral Analyst":      AgentNode(name: "Neutral Analyst", stage: .riskDebate, icon: "⚖️", colorHex: "#94a3b8", blurb: "Balanced view"),
        "Portfolio Manager":    AgentNode(name: "Portfolio Manager", stage: .decision, icon: "🎯", colorHex: "#34d399", blurb: "Final call"),
    ]

    /// Metadata for a node, with a neutral fallback for unknown names.
    static func metadata(for name: String) -> AgentNode {
        all[name] ?? AgentNode(name: name, stage: .analysis, icon: "🤖", colorHex: "#94a3b8", blurb: "")
    }

    /// Map a wire analyst key ("market") to its display name ("Market Analyst").
    static let analystDisplayName: [String: String] = [
        "market": "Market Analyst",
        "social": "Sentiment Analyst",
        "news": "News Analyst",
        "fundamentals": "Fundamentals Analyst",
    ]

    /// The Market Analyst is where the price chart is hosted (web parity).
    static let chartHostNode = "Market Analyst"
}

/// Live per-agent UI state projected from the SSE stream.
struct AgentCardState: Identifiable, Equatable {
    let node: String
    var status: AgentReportEvent.AgentStatus
    var content: String?

    var id: String { node }
    var metadata: AgentNode { AgentNode.metadata(for: node) }
}
