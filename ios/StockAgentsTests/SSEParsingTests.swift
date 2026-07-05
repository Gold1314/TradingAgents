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

    // MARK: - SSEFrameParser (incremental frame assembly)

    /// Feed a string's UTF-8 bytes through a fresh parser and collect frames.
    private func frames(_ text: String) -> [SSEFrame] {
        var parser = SSEFrameParser()
        return parser.feed(Array(text.utf8))
    }

    /// The core regression: a blank line (which `AsyncLineSequence` drops) must
    /// dispatch the buffered frame.
    func testBlankLineDispatchesFrame() {
        let result = frames("data: {\"type\":\"done\"}\n\n")
        XCTAssertEqual(result.count, 1)
        XCTAssertEqual(result.first, SSEFrame(id: nil, data: #"{"type":"done"}"#))
    }

    /// No blank line yet => nothing dispatched (the delimiter is required).
    func testNoBlankLineNoFrame() {
        let result = frames("data: {\"type\":\"status\"}\n")
        XCTAssertTrue(result.isEmpty)
    }

    /// Multiple `data:` lines within one frame concatenate with newlines.
    func testMultiLineDataConcatenation() {
        let result = frames("data: line one\ndata: line two\n\n")
        XCTAssertEqual(result.count, 1)
        XCTAssertEqual(result.first?.data, "line one\nline two")
    }

    /// `\r\n` (CRLF) endings behave like `\n`: the trailing `\r` is stripped.
    func testCRLFLineEndings() {
        let result = frames("id: 5\r\ndata: {\"type\":\"status\"}\r\n\r\n")
        XCTAssertEqual(result.count, 1)
        XCTAssertEqual(result.first, SSEFrame(id: 5, data: #"{"type":"status"}"#))
    }

    /// `id:` is captured and carried on the emitted frame.
    func testIdIsTracked() {
        let result = frames("id: 42\ndata: {\"type\":\"done\"}\n\n")
        XCTAssertEqual(result.first?.id, 42)
    }

    /// A leading `:` comment / keepalive line is ignored and never yields a frame.
    func testKeepaliveCommentIgnored() {
        // A keepalive alone produces nothing…
        XCTAssertTrue(frames(": keepalive\n\n").isEmpty)
        // …and doesn't corrupt a following real frame.
        let result = frames(": keepalive\ndata: {\"type\":\"done\"}\n\n")
        XCTAssertEqual(result.count, 1)
        XCTAssertEqual(result.first?.data, #"{"type":"done"}"#)
    }

    /// A frame split across two network chunks mid-line still assembles once the
    /// blank-line delimiter arrives.
    func testPartialChunkSplitMidLine() {
        var parser = SSEFrameParser()
        var collected: [SSEFrame] = []
        collected += parser.feed(Array("data: {\"typ".utf8))
        XCTAssertTrue(collected.isEmpty)
        collected += parser.feed(Array("e\":\"done\"}\n\n".utf8))
        XCTAssertEqual(collected.count, 1)
        XCTAssertEqual(collected.first?.data, #"{"type":"done"}"#)
    }

    /// The `done` payload is emitted like any other frame (the caller decodes it
    /// and treats it as terminal).
    func testDoneEventFrame() {
        let result = frames("id: 9\ndata: {\"type\":\"done\"}\n\n")
        XCTAssertEqual(result, [SSEFrame(id: 9, data: #"{"type":"done"}"#)])
    }

    /// A blank line with no buffered `data:` dispatches nothing AND resets a
    /// dangling `id:`, so it isn't wrongly attached to the next frame.
    func testEmptyDataResetsPendingID() {
        let result = frames("id: 3\n\ndata: {\"type\":\"status\"}\n\n")
        XCTAssertEqual(result.count, 1)
        XCTAssertEqual(result.first?.id, nil)
        XCTAssertEqual(result.first?.data, #"{"type":"status"}"#)
    }

    /// Two complete frames in one chunk are both emitted, in order.
    func testTwoFramesInOneChunk() {
        let result = frames(
            "id: 1\ndata: {\"type\":\"status\"}\n\nid: 2\ndata: {\"type\":\"done\"}\n\n"
        )
        XCTAssertEqual(result.count, 2)
        XCTAssertEqual(result[0], SSEFrame(id: 1, data: #"{"type":"status"}"#))
        XCTAssertEqual(result[1], SSEFrame(id: 2, data: #"{"type":"done"}"#))
    }
}
