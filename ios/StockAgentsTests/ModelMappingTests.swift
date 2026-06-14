import XCTest
@testable import StockAgents

/// Verdict mapping, start-run response discrimination, config + chart decoding.
final class ModelMappingTests: XCTestCase {
    private let decoder = JSONDecoder()

    func testVerdictMapping() {
        XCTAssertEqual(Verdict(decision: "Buy"), .buy)
        XCTAssertEqual(Verdict(decision: "  sell "), .sell)
        XCTAssertEqual(Verdict(decision: "OVERWEIGHT"), .overweight)
        XCTAssertNil(Verdict(decision: "Unknown"))
        XCTAssertEqual(Verdict.hold.gaugePosition, 50)
        XCTAssertEqual(Verdict.buy.gaugePosition, 100)
    }

    func testStartRunStarted() throws {
        let json = #"{"cached":false,"run_id":"abc123","nodes":["Market Analyst","Trader"]}"#
        let response = try decoder.decode(StartRunResponse.self, from: Data(json.utf8))
        guard case .started(let runID, let nodes) = response else { return XCTFail("wrong case") }
        XCTAssertEqual(runID, "abc123")
        XCTAssertEqual(nodes.count, 2)
    }

    func testStartRunCached() throws {
        let json = """
        {"cached":true,"run":{"ticker":"NVDA","trade_date":"2026-06-12","asset_type":"stock","decision":"Buy","final_content":"x","identity":"NVDA","created_at":"2026-06-12T00:00:00Z","agents":[{"seq":0,"agent":"Market Analyst","content":"report"}]}}
        """
        let response = try decoder.decode(StartRunResponse.self, from: Data(json.utf8))
        guard case .cached(let run) = response else { return XCTFail("wrong case") }
        XCTAssertEqual(run.ticker, "NVDA")
        XCTAssertEqual(run.agents.first?.agent, "Market Analyst")
    }

    func testConfigDecoding() throws {
        let json = """
        {"provider":"openai","deep_model":"gpt-5.5","quick_model":"gpt-5.4-mini","max_debate_rounds":1,"max_risk_rounds":1,"analysts":["market","social","news","fundamentals"],"analyst_display":{"market":"Market Analyst"},"supabase_configured":true,"admin_available":false,"cache_window_minutes":60,"robinhood":{"enabled":false,"trade_mode":"off","dry_run":true,"can_place_real_orders":false,"grounding_enabled":false}}
        """
        let config = try decoder.decode(RunConfig.self, from: Data(json.utf8))
        XCTAssertEqual(config.maxDebateRounds, 1)
        XCTAssertEqual(config.analysts.count, 4)
        XCTAssertEqual(config.analystDisplay["market"], "Market Analyst")
        XCTAssertEqual(config.robinhood?.enabled, false)
    }

    func testChartDecoding() throws {
        let json = """
        {"symbol":"NVDA","from":"2026-01-01","to":"2026-06-12","candles":[{"time":"2026-06-12","open":100.0,"high":110.0,"low":95.0,"close":105.0}],"volume":[{"time":"2026-06-12","value":1000.0,"color":"rgba(52,211,153,.45)"}],"indicators":{"close_50_sma":[{"time":"2026-06-12","value":102.0}]}}
        """
        let payload = try decoder.decode(ChartPayload.self, from: Data(json.utf8))
        XCTAssertEqual(payload.symbol, "NVDA")
        XCTAssertEqual(payload.candles.first?.isUp, true)
        XCTAssertEqual(payload.indicators["close_50_sma"]?.first?.value, 102.0)
        XCTAssertNotNil(payload.candles.first?.date)
    }

    func testAgentNodeMetadataCoversAllNodes() {
        XCTAssertEqual(AgentNode.all.count, 12)
        XCTAssertEqual(AgentNode.metadata(for: "Portfolio Manager").stage, .decision)
        XCTAssertEqual(AgentNode.metadata(for: "Bull Researcher").stage, .researchDebate)
        // Unknown node falls back gracefully.
        XCTAssertEqual(AgentNode.metadata(for: "Mystery").stage, .analysis)
    }
}
