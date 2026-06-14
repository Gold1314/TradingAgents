import XCTest
@testable import StockAgents

/// Verifies the SSE event union decodes every `type` the backend emits
/// (`web/server.py`), using payloads shaped exactly like the server's JSON.
final class AgentEventDecodingTests: XCTestCase {
    private let decoder = JSONDecoder()

    private func decode(_ json: String) throws -> AgentEvent {
        try decoder.decode(AgentEvent.self, from: Data(json.utf8))
    }

    func testNodes() throws {
        let event = try decode(#"{"type":"nodes","nodes":["Market Analyst","Trader"]}"#)
        guard case .nodes(let payload) = event else { return XCTFail("wrong case") }
        XCTAssertEqual(payload.nodes, ["Market Analyst", "Trader"])
    }

    func testStatus() throws {
        let event = try decode(#"{"type":"status","message":"Running pipeline..."}"#)
        guard case .status(let payload) = event else { return XCTFail("wrong case") }
        XCTAssertEqual(payload.message, "Running pipeline...")
    }

    func testMemory() throws {
        let event = try decode(#"{"type":"memory","phase":"resolve","status":"done","resolved":2}"#)
        guard case .memory(let payload) = event else { return XCTFail("wrong case") }
        XCTAssertEqual(payload.phase, "resolve")
        XCTAssertEqual(payload.resolved, 2)
    }

    func testIdentity() throws {
        let event = try decode(#"{"type":"identity","content":"NVDA — NVIDIA Corp"}"#)
        guard case .identity(let payload) = event else { return XCTFail("wrong case") }
        XCTAssertEqual(payload.content, "NVDA — NVIDIA Corp")
    }

    func testAccount() throws {
        let json = #"{"type":"account","connected":true,"buying_power":1000.5,"portfolio_value":5000.0,"position":{"symbol":"NVDA","qty":3}}"#
        let event = try decode(json)
        guard case .account(let payload) = event else { return XCTFail("wrong case") }
        XCTAssertTrue(payload.connected)
        XCTAssertEqual(payload.buyingPower, 1000.5)
        XCTAssertEqual(payload.position?["symbol"]?.stringValue, "NVDA")
    }

    func testAgentRunningAndDone() throws {
        let running = try decode(#"{"type":"agent","node":"Market Analyst","status":"running","content":null}"#)
        guard case .agent(let r) = running else { return XCTFail("wrong case") }
        XCTAssertEqual(r.status, .running)
        XCTAssertNil(r.content)

        let done = try decode(#"{"type":"agent","node":"Market Analyst","status":"done","content":"Report **strong**"}"#)
        guard case .agent(let d) = done else { return XCTFail("wrong case") }
        XCTAssertEqual(d.status, .done)
        XCTAssertEqual(d.content, "Report **strong**")
    }

    func testGraph() throws {
        let event = try decode(#"{"type":"graph","node":"tools_market","status":"active","tools":["get_ohlcv"]}"#)
        guard case .graph(let payload) = event else { return XCTFail("wrong case") }
        XCTAssertEqual(payload.tools, ["get_ohlcv"])
    }

    func testProgress() throws {
        let event = try decode(#"{"type":"progress","active":"Trader","completed":["Market Analyst"],"node":"Market Analyst","status":"done"}"#)
        guard case .progress(let payload) = event else { return XCTFail("wrong case") }
        XCTAssertEqual(payload.active, "Trader")
        XCTAssertEqual(payload.completed, ["Market Analyst"])
    }

    func testUsage() throws {
        let json = #"{"type":"usage","node":"Trader","input_tokens":100,"output_tokens":50,"total_tokens":150,"calls":2,"model":"gpt-5.5","cost":0.0012}"#
        let event = try decode(json)
        guard case .usage(let payload) = event else { return XCTFail("wrong case") }
        XCTAssertEqual(payload.totalTokens, 150)
        XCTAssertEqual(payload.model, "gpt-5.5")
        XCTAssertEqual(payload.cost, 0.0012)
    }

    func testUsageSummary() throws {
        let json = """
        {"type":"usage_summary","nodes":{"Trader":{"node":"Trader","input_tokens":100,"output_tokens":50,"total_tokens":150,"calls":1,"model":"gpt-5.5","cost":0.001}},"totals":{"input_tokens":100,"output_tokens":50,"total_tokens":150,"cost":0.001}}
        """
        let event = try decode(json)
        guard case .usageSummary(let payload) = event else { return XCTFail("wrong case") }
        XCTAssertEqual(payload.totals.totalTokens, 150)
        XCTAssertEqual(payload.nodes["Trader"]?.calls, 1)
    }

    func testFinal() throws {
        let event = try decode(#"{"type":"final","decision":"Buy","content":"Strong momentum."}"#)
        guard case .final(let payload) = event else { return XCTFail("wrong case") }
        XCTAssertEqual(payload.decision, "Buy")
    }

    func testTrade() throws {
        let json = #"{"type":"trade","trade_mode":"manual","can_place_real_orders":false,"dry_run":true,"status":"proposed","intent":{"ticker":"NVDA","action":"buy","rating":"Buy","notional":100.0,"quantity":null,"order_type":"market","reason":"x"},"message":"proposed"}"#
        let event = try decode(json)
        guard case .trade(let payload) = event else { return XCTFail("wrong case") }
        XCTAssertEqual(payload.status, "proposed")
        XCTAssertEqual(payload.intent?.ticker, "NVDA")
        XCTAssertEqual(payload.intent?.notional, 100.0)
    }

    func testError() throws {
        let event = try decode(#"{"type":"error","message":"boom","trace":"..."}"#)
        guard case .error(let payload) = event else { return XCTFail("wrong case") }
        XCTAssertEqual(payload.message, "boom")
    }

    func testDone() throws {
        let event = try decode(#"{"type":"done"}"#)
        XCTAssertEqual(event, .done)
    }

    func testUnknownTypeIsToleranced() throws {
        let event = try decode(#"{"type":"some_future_event","foo":1}"#)
        guard case .unknown(let raw) = event else { return XCTFail("wrong case") }
        XCTAssertEqual(raw, "some_future_event")
    }
}
