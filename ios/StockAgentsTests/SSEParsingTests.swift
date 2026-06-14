import XCTest
@testable import StockAgents

/// Unit tests for the SSE frame field parser used by `RunStreamClient`.
final class SSEParsingTests: XCTestCase {
    func testParsesIdField() {
        let (field, value) = RunStreamClient.parseField("id: 42")
        XCTAssertEqual(field, "id")
        XCTAssertEqual(value, "42")
    }

    func testParsesDataFieldWithJSON() {
        let (field, value) = RunStreamClient.parseField(#"data: {"type":"done"}"#)
        XCTAssertEqual(field, "data")
        XCTAssertEqual(value, #"{"type":"done"}"#)
    }

    func testStripsOnlyOneLeadingSpace() {
        let (_, value) = RunStreamClient.parseField("data:  two-spaces")
        XCTAssertEqual(value, " two-spaces")
    }

    func testFieldWithoutColon() {
        let (field, value) = RunStreamClient.parseField("data")
        XCTAssertEqual(field, "data")
        XCTAssertEqual(value, "")
    }

    func testColonInValueIsPreserved() {
        let (field, value) = RunStreamClient.parseField(#"data: {"url":"http://x"}"#)
        XCTAssertEqual(field, "data")
        XCTAssertEqual(value, #"{"url":"http://x"}"#)
    }
}
