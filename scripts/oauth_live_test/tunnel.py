"""Start a public HTTPS tunnel to the local callback listener.

Gives a developer laptop a real ``https://...`` redirect URL with **zero
infrastructure** by shelling out to whichever tunnel tool is installed:

* ``cloudflared tunnel --url http://localhost:<port>``  → ``https://<rand>.trycloudflare.com``
* ``ngrok http <port>``                                  → ``https://<rand>.ngrok-free.app``

This module **does not** bundle or download any binary. If neither tool is on
``PATH`` it prints install instructions and returns without raising, so the rest
of the harness stays runnable. Once a tunnel is up it prints the public URL
prominently and stays in the foreground (Ctrl-C to stop); pass that URL to the
harness as ``--public-base-url`` (or ``OAUTH_LIVE_TEST_PUBLIC_BASE_URL``).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import List, Optional

# Matches the public hostnames the two supported tools emit.
_URL_RE = re.compile(r"https://[A-Za-z0-9._-]+\.(?:trycloudflare\.com|ngrok[A-Za-z0-9.-]*\.app|ngrok\.io)")

_INSTALL_HINT = (
    "No tunnel tool found on PATH (looked for 'cloudflared' and 'ngrok').\n"
    "Install ONE of them (no binaries are bundled by this harness):\n\n"
    "  cloudflared (recommended — no signup for quick tunnels):\n"
    "    macOS:  brew install cloudflared\n"
    "    linux:  https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/\n"
    "    then:   cloudflared tunnel --url http://localhost:{port}\n\n"
    "  ngrok (free account / authtoken required):\n"
    "    macOS:  brew install ngrok\n"
    "    setup:  ngrok config add-authtoken <YOUR_TOKEN>\n"
    "    then:   ngrok http {port}\n\n"
    "Whichever you use, copy the printed https://... URL and pass it to the\n"
    "harness as --public-base-url (build-authorize-url)."
)


@dataclass
class TunnelTool:
    name: str
    argv: List[str]


def detect_tool(port: int, prefer: Optional[str] = None) -> Optional[TunnelTool]:
    """Return the tunnel tool to use, or ``None`` if none is installed."""
    candidates = []
    if shutil.which("cloudflared"):
        candidates.append(TunnelTool(
            "cloudflared",
            ["cloudflared", "tunnel", "--url", f"http://localhost:{port}"],
        ))
    if shutil.which("ngrok"):
        candidates.append(TunnelTool(
            "ngrok",
            ["ngrok", "http", str(port), "--log", "stdout"],
        ))
    if not candidates:
        return None
    if prefer:
        for tool in candidates:
            if tool.name == prefer:
                return tool
    return candidates[0]


def run_tunnel(port: int, prefer: Optional[str] = None, url_timeout: float = 30.0) -> int:
    """Start the tunnel, print the public URL, and stay in the foreground."""
    tool = detect_tool(port, prefer=prefer)
    if tool is None:
        print(_INSTALL_HINT.format(port=port))
        return 0  # not an error — just nothing to start

    print(f"Starting {tool.name} tunnel to http://localhost:{port} ...")
    print(f"  $ {' '.join(tool.argv)}\n")
    try:
        proc = subprocess.Popen(
            tool.argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        print(f"Failed to start {tool.name}: {exc}", file=sys.stderr)
        return 1

    public_url: Optional[str] = None
    deadline = time.time() + url_timeout
    assert proc.stdout is not None
    try:
        while True:
            line = proc.stdout.readline()
            if line == "" and proc.poll() is not None:
                break
            if line:
                sys.stdout.write(line)
                sys.stdout.flush()
                if public_url is None:
                    match = _URL_RE.search(line)
                    if match:
                        public_url = match.group(0)
                        _announce(public_url, port)
            if public_url is None and time.time() > deadline:
                print(
                    "\n(Tunnel started but no public URL detected yet — watch the "
                    "output above; the https://... line should appear shortly.)\n",
                    file=sys.stderr,
                )
                deadline = float("inf")  # only warn once
        return proc.returncode or 0
    except KeyboardInterrupt:
        print("\nStopping tunnel ...")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        return 0


def _announce(url: str, port: int) -> None:
    bar = "=" * 72
    print(
        f"\n{bar}\n"
        f"  PUBLIC HTTPS CALLBACK BASE: {url}\n"
        f"  -> forwards to http://localhost:{port}\n\n"
        f"  Next (in another terminal):\n"
        f"    .venv/bin/python -m scripts.oauth_live_test build-authorize-url \\\n"
        f"        --public-base-url {url} --serve-callback\n"
        f"{bar}\n"
    )


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m scripts.oauth_live_test.tunnel",
        description="Expose the local OAuth callback listener over public HTTPS.",
    )
    parser.add_argument("--port", type=int, default=8765, help="Local port the listener uses (default 8765).")
    parser.add_argument("--tool", choices=["cloudflared", "ngrok"], help="Force a specific tunnel tool.")
    args = parser.parse_args(argv)
    return run_tunnel(args.port, prefer=args.tool)


if __name__ == "__main__":
    raise SystemExit(main())
