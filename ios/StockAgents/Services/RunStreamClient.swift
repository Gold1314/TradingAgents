import Foundation

/// Server-Sent Events consumer for `GET /api/runs/{run_id}/events`.
///
/// There is no first-party SwiftUI SSE API, so this is hand-rolled on
/// `URLSession.bytes(for:)` (plan §3.3). It reads the async byte stream
/// line-by-line, buffers `id:` / `data:` frames, ignores `: keepalive` comment
/// lines, decodes each frame into an `AgentEvent`, and yields it through an
/// `AsyncThrowingStream`.
///
/// Resume: the last seen `id:` is tracked and sent as the `Last-Event-ID`
/// header on reconnect, exactly like the browser's `EventSource`. The server
/// (`run_events()` in `web/server.py`) resumes from `last-event-id + 1`, so a
/// dropped connection (backgrounding, network blip) picks up where it left off.
final class RunStreamClient {
    private let auth: AuthService
    private let session: URLSession
    private let maxReconnectAttempts = 5
    /// A long run can outlive the access token: a dropped connection's reconnect
    /// then carries a stale bearer and the server answers `401`. We refresh the
    /// token (single-flight via `AuthService`) and reconnect with the resumed
    /// `Last-Event-ID`. This caps *consecutive* refresh+reconnect cycles (reset
    /// on any successful connection) so a persistently rejected token can't spin
    /// forever — after the cap we sign out and stop, like `APIClient` does.
    private let maxAuthRefreshAttempts = 2

    init(auth: AuthService, session: URLSession? = nil) {
        self.auth = auth
        if let session {
            self.session = session
        } else {
            // Long-lived stream: disable the per-request timeout (the server
            // sends a `: keepalive` comment every 15s to hold the connection).
            let config = URLSessionConfiguration.default
            config.timeoutIntervalForRequest = 0
            config.timeoutIntervalForResource = 0
            self.session = URLSession(configuration: config)
        }
    }

    /// One decoded event plus its SSE id (used by callers for resume bookkeeping).
    struct StreamedEvent: Equatable {
        let id: Int?
        let event: AgentEvent
    }

    /// Open (and transparently re-open) the event stream for a run.
    ///
    /// - Parameters:
    ///   - baseURL: backend base URL (read at call time so Settings changes apply).
    ///   - runID: the run to stream.
    ///   - lastEventID: resume point; nil for a fresh connection (full replay).
    func events(baseURL: URL, runID: String, lastEventID: Int? = nil) -> AsyncThrowingStream<StreamedEvent, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                var resumeID = lastEventID
                var attempts = 0
                var authRefreshAttempts = 0
                while !Task.isCancelled {
                    do {
                        let sawDone = try await self.consume(
                            baseURL: baseURL,
                            runID: runID,
                            lastEventID: resumeID,
                            onEvent: { streamed in
                                if let id = streamed.id { resumeID = id }
                                continuation.yield(streamed)
                            }
                        )
                        // We connected (got past auth), so any prior token
                        // expiry is resolved — reset the refresh budget.
                        authRefreshAttempts = 0
                        if sawDone {
                            continuation.finish()
                            return
                        }
                        // Stream ended without a terminal `done` (e.g. server
                        // closed the connection). Reconnect and resume — but the
                        // reconnect budget is capped on this path too, otherwise a
                        // server that keeps closing cleanly loops forever.
                        attempts += 1
                        if attempts >= self.maxReconnectAttempts {
                            continuation.finish(throwing: APIError.transport(
                                "The event stream closed repeatedly without completing."
                            ))
                            return
                        }
                    } catch is CancellationError {
                        continuation.finish()
                        return
                    } catch APIError.unauthorized {
                        // The (re)connect was rejected with `401`: the access
                        // token has almost certainly expired mid-run. Refresh it
                        // (single-flight, shared with `APIClient`) and reconnect
                        // immediately with the new bearer + the preserved
                        // `Last-Event-ID`, so no events are missed. We only sign
                        // out on a *definitive* rejection of the refresh token — a
                        // transport blip during refresh keeps the tokens and just
                        // backs off.
                        switch await self.auth.attemptRefresh() {
                        case .refreshed:
                            authRefreshAttempts += 1
                            if authRefreshAttempts > self.maxAuthRefreshAttempts {
                                // Refresh keeps succeeding yet the server keeps
                                // rejecting the token — treat as a hard auth
                                // failure and stop, like `APIClient` does.
                                await self.auth.signOut()
                                continuation.finish(throwing: APIError.unauthorized)
                                return
                            }
                            continue // reconnect now, no backoff — the token is fresh
                        case .rejected:
                            // The refresh token itself was rejected: drop to a
                            // signed-out/auth-error state like `APIClient`.
                            await self.auth.signOut()
                            continuation.finish(throwing: APIError.unauthorized)
                            return
                        case .transient:
                            // Network error during refresh — do NOT sign out.
                            // Keep the tokens and back off, bounded by the generic
                            // reconnect budget.
                            attempts += 1
                            if attempts >= self.maxReconnectAttempts {
                                continuation.finish(throwing: APIError.transport(
                                    "Couldn't refresh your session — network error."
                                ))
                                return
                            }
                        }
                    } catch {
                        attempts += 1
                        if attempts >= self.maxReconnectAttempts {
                            continuation.finish(throwing: error)
                            return
                        }
                    }
                    // Linear backoff before resuming.
                    try? await Task.sleep(nanoseconds: UInt64(min(attempts, 3)) * 1_000_000_000)
                }
                continuation.finish()
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    /// Consume one connection's worth of events. Returns true if a `done` event
    /// was seen (terminal), false if the stream ended otherwise.
    private func consume(
        baseURL: URL,
        runID: String,
        lastEventID: Int?,
        onEvent: (StreamedEvent) -> Void
    ) async throws -> Bool {
        guard let url = URL(string: "/api/runs/\(runID)/events", relativeTo: baseURL) else {
            throw APIError.invalidURL
        }
        var request = URLRequest(url: url)
        request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        if let lastEventID {
            request.setValue(String(lastEventID), forHTTPHeaderField: "Last-Event-ID")
        }
        if let header = await auth.authorizationHeader {
            request.setValue(header, forHTTPHeaderField: "Authorization")
        }

        let (bytes, response) = try await session.bytes(for: request)
        if let http = response as? HTTPURLResponse, !(200...299).contains(http.statusCode) {
            try APIClient.validate(http, data: Data())
        }

        // Drive a pure, incremental frame parser off the RAW byte stream.
        //
        // We deliberately do NOT use `bytes.lines` (`AsyncLineSequence`): it
        // SKIPS empty lines, so the blank-line SSE frame delimiter never
        // arrives and no event is ever dispatched. Reading raw `UInt8`s and
        // splitting on `\n` ourselves preserves those blank lines.
        var parser = SSEFrameParser()
        let decoder = JSONDecoder()

        for try await byte in bytes {
            try Task.checkCancellation()
            guard let frame = parser.consume(byte) else { continue }

            guard let payload = frame.data.data(using: .utf8) else { continue }
            // A single malformed frame must NOT tear down the whole stream: a
            // decode failure here would throw, skip yielding the frame, and —
            // because `resumeID` never advanced past it — replay the same poison
            // event on every reconnect. Instead we surface it as an `.unknown`
            // event (which the UI ignores) so `resumeID` still advances and the
            // bad frame is never replayed.
            let event: AgentEvent
            do {
                event = try decoder.decode(AgentEvent.self, from: payload)
            } catch {
                onEvent(StreamedEvent(id: frame.id, event: .unknown("decode_error")))
                continue
            }
            onEvent(StreamedEvent(id: frame.id, event: event))
            if case .done = event { return true }
        }
        return false
    }

    /// Split an SSE field line into (name, value), trimming one optional leading
    /// space from the value per the SSE spec.
    static func parseField(_ line: String) -> (field: String, value: String) {
        guard let colon = line.firstIndex(of: ":") else {
            return (line, "")
        }
        let field = String(line[line.startIndex..<colon])
        var value = String(line[line.index(after: colon)...])
        if value.hasPrefix(" ") { value.removeFirst() }
        return (field, value)
    }
}

/// One fully-assembled SSE event frame (a blank-line-delimited block). The `id`
/// is the parsed `id:` line (used for resume); `data` is the concatenated
/// `data:` payload, ready to be JSON-decoded by the caller.
struct SSEFrame: Equatable {
    let id: Int?
    let data: String
}

/// Pure, synchronous, incremental SSE frame assembler.
///
/// Feed it raw bytes as they arrive off the wire (`consume(_:)` per byte, or
/// `feed(_:)` for a chunk); it emits an ``SSEFrame`` each time a blank line
/// closes a buffered event. Unlike `URLSession.AsyncBytes.lines`
/// (`AsyncLineSequence`), which silently drops empty lines, this parser
/// PRESERVES blank lines — the SSE frame delimiter — so events are actually
/// dispatched. It also:
///   * handles both `\n` and `\r\n` line endings (a trailing `\r` is stripped),
///   * ignores `:` comment / keepalive lines,
///   * concatenates multiple `data:` lines with newlines (per the SSE spec),
///   * tracks the last `id:` for resume bookkeeping.
///
/// It performs NO JSON decoding — the caller decodes ``SSEFrame/data`` into its
/// own event type, keeping this logic transport-free and unit-testable.
struct SSEFrameParser {
    private var lineBytes: [UInt8] = []
    private var dataBuffer = ""
    private var pendingID: Int?

    /// Feed one byte. Returns a frame iff a blank line just closed a buffered
    /// event; otherwise `nil`.
    mutating func consume(_ byte: UInt8) -> SSEFrame? {
        guard byte == 0x0A else { // 0x0A == "\n"
            lineBytes.append(byte)
            return nil
        }
        // Strip a single trailing "\r" so CRLF endings behave like LF.
        if lineBytes.last == 0x0D { lineBytes.removeLast() } // 0x0D == "\r"
        let line = String(decoding: lineBytes, as: UTF8.self)
        lineBytes.removeAll(keepingCapacity: true)
        return handleLine(line)
    }

    /// Feed a chunk of bytes, returning every frame completed within it.
    mutating func feed<S: Sequence>(_ bytes: S) -> [SSEFrame] where S.Element == UInt8 {
        var frames: [SSEFrame] = []
        for byte in bytes {
            if let frame = consume(byte) { frames.append(frame) }
        }
        return frames
    }

    private mutating func handleLine(_ line: String) -> SSEFrame? {
        // Blank line => dispatch the buffered event frame.
        if line.isEmpty {
            guard !dataBuffer.isEmpty else {
                pendingID = nil
                return nil
            }
            let frame = SSEFrame(id: pendingID, data: dataBuffer)
            dataBuffer = ""
            pendingID = nil
            return frame
        }

        // Comment / keepalive line (": keepalive").
        if line.hasPrefix(":") { return nil }

        let (field, value) = RunStreamClient.parseField(line)
        switch field {
        case "id":
            pendingID = Int(value)
        case "data":
            // SSE concatenates multiple data: lines with newlines.
            dataBuffer += dataBuffer.isEmpty ? value : "\n" + value
        default:
            break // ignore event:/retry: — the server doesn't use them
        }
        return nil
    }
}
