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
                        // closed the connection). Reconnect and resume.
                        attempts += 1
                    } catch is CancellationError {
                        continuation.finish()
                        return
                    } catch APIError.unauthorized {
                        // The (re)connect was rejected with `401`: the access
                        // token has almost certainly expired mid-run. Refresh it
                        // (single-flight, shared with `APIClient`) and reconnect
                        // immediately with the new bearer + the preserved
                        // `Last-Event-ID`, so no events are missed.
                        authRefreshAttempts += 1
                        let refreshed = authRefreshAttempts <= self.maxAuthRefreshAttempts
                            && (await self.auth.attemptRefresh())
                        if !refreshed {
                            // Refresh failed (or kept being rejected): drop to a
                            // signed-out/auth-error state like `APIClient`, and
                            // stop reconnecting.
                            await self.auth.signOut()
                            continuation.finish(throwing: APIError.unauthorized)
                            return
                        }
                        continue // reconnect now, no backoff — the token is fresh
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

        var dataBuffer = ""
        var pendingID: Int?
        let decoder = JSONDecoder()

        for try await line in bytes.lines {
            try Task.checkCancellation()

            // Blank line => dispatch the buffered event frame.
            if line.isEmpty {
                guard !dataBuffer.isEmpty else {
                    pendingID = nil
                    continue
                }
                if let payload = dataBuffer.data(using: .utf8) {
                    let event = try decoder.decode(AgentEvent.self, from: payload)
                    onEvent(StreamedEvent(id: pendingID, event: event))
                    if case .done = event { return true }
                }
                dataBuffer = ""
                pendingID = nil
                continue
            }

            // Comment / keepalive line.
            if line.hasPrefix(":") { continue }

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
