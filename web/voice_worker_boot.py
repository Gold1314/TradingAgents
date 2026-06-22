"""Railway boot helper for the LiveKit voice worker service.

Railway health checks probe ``/api/config`` on ``$PORT``, but the LiveKit
worker process has no HTTP server. This module serves a minimal JSON stub on
that path in a background thread, then execs the real worker (``start``).
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path.startswith("/api/config"):
            body = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, *_args) -> None:
        return


def _serve_health() -> None:
    port = int(os.environ.get("PORT", "8000"))
    HTTPServer(("0.0.0.0", port), _HealthHandler).serve_forever()


def main() -> None:
    threading.Thread(target=_serve_health, daemon=True).start()
    subprocess.run(
        [sys.executable, "-m", "tradingagents.voice.agent_worker", "start"],
        check=True,
    )


if __name__ == "__main__":
    main()
