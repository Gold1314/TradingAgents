"""PASS / FAIL / UNKNOWN reporting against the §5.1.5.G live-test checklist.

The checklist items below are copied verbatim (condensed) from
``docs/ios-app-plan.md`` §5.1.5.G so the operator can see exactly which
credentialed fact each command does or does not establish.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

PASS = "PASS"
FAIL = "FAIL"
UNKNOWN = "UNKNOWN"

# §5.1.5.G — "Still requires a live, credentialed test (with a real Agentic
# account)". Keys are stable ids used by the commands to report against.
CHECKLIST: Dict[str, str] = {
    "G1": "Robinhood honors our remote-HTTPS callback post-login "
    "(authorize -> callback returns a code).",
    "G2": "Is HTTPS broadly accepted, or must our exact callback URL be "
    "allowlisted by Robinhood (partner onboarding)?",
    "G3": "End-to-end token exchange persists a NON-EMPTY access_token "
    "(+ refresh_token) via our storage.",
    "G4": "Refresh-token TTL + rotation behaviour (does refresh rotate the "
    "refresh token? re-consent cadence?).",
    "G5": "Desktop-only onboarding for first-time account creation vs. "
    "subsequent re-auth.",
    "G6": "Multi-redirect / dev-vs-prod coexistence (e.g. staging HTTPS vs "
    "prod HTTPS callbacks).",
}


@dataclass
class CheckResult:
    """One checklist verdict produced by a command."""

    item: str
    status: str
    detail: str

    def line(self) -> str:
        label = CHECKLIST.get(self.item, self.item)
        return f"[{self.status:^7}] {self.item}: {label}\n          -> {self.detail}"


def print_header(title: str) -> None:
    bar = "=" * 78
    print(f"\n{bar}\n{title}\n{bar}")


def print_section(title: str) -> None:
    print(f"\n--- {title} ---")


def print_results(results: List[CheckResult]) -> None:
    print_section("§5.1.5.G checklist verdicts from this command")
    if not results:
        print("  (none)")
        return
    for res in results:
        print(res.line())


def print_kv(data: Dict[str, object], indent: int = 2) -> None:
    pad = " " * indent
    width = max((len(str(k)) for k in data), default=0)
    for key, value in data.items():
        print(f"{pad}{str(key):<{width}} : {value}")
