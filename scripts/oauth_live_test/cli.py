"""Command-line entry point for the OAuth live-test harness.

Commands
--------
* ``discover``            unauthenticated metadata discovery + DCR probe (SAFE).
* ``build-authorize-url`` build/print the authorize URL + PKCE (SAFE).
* ``exchange --live``     PKCE token exchange for a pasted code (CREDENTIALED).
* ``refresh  --live``     refresh_token grant; measures TTL/rotation (CREDENTIALED).

Only ``exchange`` and ``refresh`` need a real Robinhood Agentic account, and both
require an explicit ``--live`` flag. No command ever places a brokerage order.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import List, Optional

from scripts.oauth_live_test import flow, tunnel
from scripts.oauth_live_test.callback_server import CallbackCapture, serve_one_callback
from scripts.oauth_live_test.config import REDIRECT_SHAPES, HarnessConfig, build_config
from scripts.oauth_live_test.discovery import (
    DiscoveryResult,
    discover,
    probe_dcr,
)
from scripts.oauth_live_test.report import (
    FAIL,
    PASS,
    UNKNOWN,
    CheckResult,
    print_header,
    print_kv,
    print_results,
    print_section,
)


# ── shared argument wiring ────────────────────────────────────────────────────
def _add_common_args(parser: argparse.ArgumentParser) -> None:
    g = parser.add_argument_group("connection / redirect configuration")
    g.add_argument("--mcp-url", help="Robinhood MCP URL (default: the repo's DEFAULT_MCP_URL).")
    g.add_argument("--public-base-url", help="Public HTTPS base for the remote-https redirect, e.g. https://staging.example.com")
    g.add_argument("--redirect-shape", choices=REDIRECT_SHAPES, help="Redirect shape to test (default: remote-https).")
    g.add_argument("--redirect-uri", help="Explicit redirect URI (overrides --redirect-shape).")
    g.add_argument("--custom-scheme-uri", help="Custom-scheme redirect (default: stockagents://oauth/robinhood).")
    g.add_argument("--callback-path", help="Path appended to --public-base-url (default: /api/robinhood/callback).")
    g.add_argument("--callback-port", type=int, help="Loopback port for the 'loopback' shape (default: 8765).")
    g.add_argument("--client-name", help="DCR client_name (default: TradingAgents).")
    g.add_argument("--client-id", help="Use a known client_id instead of performing DCR.")
    g.add_argument("--token-path", help="Where exchanged tokens are persisted (default: an isolated harness file, NOT the real broker token).")
    g.add_argument("--session-file", help="Where PKCE verifier/state are stored between build-authorize-url and exchange.")
    g.add_argument("--results-file", help="Where measurement records (TTL/rotation) are appended.")
    g.add_argument("--timeout", type=float, help="Per-request HTTP timeout in seconds (default: 30).")


def _require_live(args, command: str) -> None:
    if not getattr(args, "live", False):
        print(
            f"\nRefusing to run '{command}': this is a CREDENTIALED step that "
            "contacts Robinhood's token endpoint with a real authorization "
            "grant.\nRe-run with --live once you have a code/state from "
            "build-authorize-url.\n",
            file=sys.stderr,
        )
        raise SystemExit(2)


# ── discovery sharing ─────────────────────────────────────────────────────────
def _run_discovery(cfg: HarnessConfig) -> DiscoveryResult:
    print_section(f"Discovering authorization-server metadata for {cfg.mcp_url}")
    result = discover(cfg.mcp_url, timeout=cfg.timeout)
    if result.errors:
        print("  notes during discovery:")
        for err in result.errors:
            print(f"    - {err}")
    return result


def _validate_assumptions(result: DiscoveryResult) -> List[CheckResult]:
    """PASS/FAIL the §5.1.5.C spike assumptions (these ARE testable unauthenticated)."""
    checks: List[CheckResult] = []
    asm = result.asm or {}

    def has(seq, value) -> bool:
        return value in (seq or [])

    grants = asm.get("grant_types_supported", [])
    checks.append(CheckResult(
        "assume.grants",
        PASS if has(grants, "authorization_code") and has(grants, "refresh_token") else FAIL,
        f"grant_types_supported = {grants}",
    ))
    pkce = asm.get("code_challenge_methods_supported", [])
    checks.append(CheckResult(
        "assume.pkce_s256",
        PASS if has(pkce, "S256") else FAIL,
        f"code_challenge_methods_supported = {pkce}",
    ))
    auth_methods = asm.get("token_endpoint_auth_methods_supported", [])
    checks.append(CheckResult(
        "assume.public_client",
        PASS if has(auth_methods, "none") else FAIL,
        f"token_endpoint_auth_methods_supported = {auth_methods} (public client = 'none')",
    ))
    checks.append(CheckResult(
        "assume.dcr_open",
        PASS if asm.get("registration_endpoint") else FAIL,
        f"registration_endpoint = {asm.get('registration_endpoint')}",
    ))
    checks.append(CheckResult(
        "assume.endpoints",
        PASS if result.authorization_endpoint and result.token_endpoint else FAIL,
        f"authorize={result.authorization_endpoint} token={result.token_endpoint}",
    ))
    cimd = asm.get("client_id_metadata_document_supported")
    checks.append(CheckResult(
        "assume.cimd_off",
        PASS if not cimd else UNKNOWN,
        f"client_id_metadata_document_supported = {cimd} (absent/false => DCR path, as expected)",
    ))
    return checks


def _print_assumption_checks(checks: List[CheckResult]) -> None:
    print_section("Spike assumption validation (§5.1.5.C — unauthenticated, decisive)")
    for c in checks:
        print(f"[{c.status:^7}] {c.item}: {c.detail}")


# ── command: discover ─────────────────────────────────────────────────────────
def cmd_discover(cfg: HarnessConfig, args) -> int:
    print_header("discover — unauthenticated authorization-server metadata")
    result = _run_discovery(cfg)

    if result.www_authenticate:
        print_section("WWW-Authenticate (from unauthenticated MCP probe)")
        print(f"  {result.www_authenticate}")

    if result.prm:
        print_section(f"Protected Resource Metadata  ({result.prm_url})")
        print(json.dumps(result.prm, indent=2))
    if result.asm:
        print_section(f"Authorization Server Metadata  ({result.asm_url})")
        print(json.dumps(result.asm, indent=2))

    if not result.ok:
        print(
            "\nDiscovery did NOT complete (likely no network access to "
            "Robinhood from this environment). The harness handled it "
            "gracefully; re-run from a network that can reach the MCP host."
        )

    if result.asm:
        _print_assumption_checks(_validate_assumptions(result))

    # Optional DCR probe across redirect shapes (corroborates §5.1.5.D).
    if result.registration_endpoint and not args.no_dcr_probe:
        _print_dcr_probe(cfg, result)

    print_results(_discover_checklist(result))
    return 0 if result.ok else 1


def _representative_redirects(cfg: HarnessConfig) -> List[str]:
    uris: List[str] = []
    base = cfg.public_base_url.rstrip("/") if cfg.public_base_url else "https://example.com"
    uris.append(f"{base}{cfg.callback_path}")
    uris.append(cfg.custom_scheme_uri)
    uris.append(f"http://localhost:{cfg.callback_port}/callback")
    return uris


def _print_dcr_probe(cfg: HarnessConfig, result: DiscoveryResult) -> None:
    print_section("Dynamic Client Registration probe (unauthenticated, §5.1.5.D)")
    print(
        "  Sending DCR with several redirect shapes. Per the spike, Robinhood "
        "returns a shared, fixed public client and merely echoes redirect_uris\n"
        "  WITHOUT validating them — so a 200 here proves nothing about whether a "
        "redirect will be honored post-login (that needs the live test).\n"
    )
    client_ids = set()
    for uri in _representative_redirects(cfg):
        probe = probe_dcr(
            result.registration_endpoint, [uri], cfg.client_name, timeout=cfg.timeout
        )
        if probe.client_id:
            client_ids.add(probe.client_id)
        print(f"  redirect_uri = {uri}")
        print_kv(
            {
                "http_status": probe.status_code,
                "client_id": probe.client_id,
                "client_name": probe.client_name,
                "has_client_secret": probe.has_client_secret,
                "echoed_redirect_uris": probe.echoed_redirect_uris,
                "error": probe.error,
            },
            indent=6,
        )
        print()
    if len(client_ids) == 1:
        print(f"  => All shapes returned the SAME client_id: {client_ids.pop()}")
        print("     (confirms the shared fixed public client; redirect gate is post-login.)")
    elif client_ids:
        print(f"  => Distinct client_ids observed: {sorted(client_ids)}")


def _discover_checklist(result: DiscoveryResult) -> List[CheckResult]:
    auth = result.authorization_endpoint or "<undiscovered>"
    token = result.token_endpoint or "<undiscovered>"
    grants = (result.asm or {}).get("grant_types_supported", [])
    return [
        CheckResult("G1", UNKNOWN, f"Precondition only: authorize endpoint = {auth}. Needs login -> run build-authorize-url then exchange --live."),
        CheckResult("G2", UNKNOWN, "DCR echoes any redirect without validating; HTTPS-accept vs allowlist is only observable post-login."),
        CheckResult("G3", UNKNOWN, f"Precondition only: token endpoint = {token}. Run exchange --live to confirm a non-empty access_token."),
        CheckResult("G4", UNKNOWN, f"refresh_token grant advertised = {'refresh_token' in grants}. Run refresh --live to measure TTL/rotation."),
        CheckResult("G5", UNKNOWN, "Not network-testable; record desktop-onboarding observations manually during the live run."),
        CheckResult("G6", UNKNOWN, "Run build-authorize-url with two redirect shapes/URLs and compare post-login acceptance."),
    ]


# ── command: build-authorize-url ──────────────────────────────────────────────
def cmd_build_authorize_url(cfg: HarnessConfig, args) -> int:
    print_header("build-authorize-url — construct the consent URL + PKCE (SAFE)")
    try:
        redirect_uri = cfg.redirect_uri()
    except ValueError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 2
    print(f"Redirect: {cfg.describe_redirect()}")

    result = _run_discovery(cfg)
    if not result.asm:
        print("\nERROR: cannot build an authorize URL without discovered metadata "
              "(no network to Robinhood?). Aborting.", file=sys.stderr)
        return 1

    scope = " ".join(result.scopes_supported) if result.scopes_supported else None

    client_id = cfg.client_id
    if not client_id:
        print_section("Obtaining client_id via Dynamic Client Registration (unauthenticated)")
        try:
            info = asyncio.run(flow.register_client(
                cfg.mcp_url, cfg.client_name, redirect_uri, result.asm, cfg.timeout
            ))
            client_id = info.client_id
            print(f"  client_id = {client_id}")
        except Exception as exc:  # noqa: BLE001
            print(f"  DCR failed: {exc}\n  Provide --client-id to proceed.", file=sys.stderr)
            return 1

    built = flow.build_authorize_url(
        cfg.mcp_url, cfg.client_name, redirect_uri, client_id,
        result.asm, result.prm, scope,
    )

    flow.save_session(cfg.session_file, {
        "state": built.state,
        "code_verifier": built.code_verifier,
        "code_challenge": built.code_challenge,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "scope": scope,
        "mcp_url": cfg.mcp_url,
        "authorization_endpoint": result.authorization_endpoint,
        "token_endpoint": result.token_endpoint,
        "asm": result.asm,
        "prm": result.prm,
    })

    print_section("Authorize URL — open this in a browser while logged in to Robinhood")
    print(f"\n{built.url}\n")
    print_kv({
        "state": built.state,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "code_challenge_method": "S256",
        "session_file": cfg.session_file,
    })

    _print_capture_guidance(cfg, redirect_uri)
    print_results(_build_url_checklist(redirect_uri))

    if args.serve_loopback:
        _serve_loopback(cfg, built.state)
    if getattr(args, "serve_callback", False):
        _serve_tunnel_callback(cfg, built.state)
    return 0


def _print_capture_guidance(cfg: HarnessConfig, redirect_uri: str) -> None:
    print_section("How the redirect (code/state) is captured")
    print(
        "PRIMARY — server-mediated (§5.1.3, the shipped design):\n"
        f"  Robinhood redirects to your SERVER endpoint ({redirect_uri}).\n"
        "  In production your FastAPI GET /api/robinhood/callback receives\n"
        "  ?code=...&state=..., the server completes the PKCE exchange, persists\n"
        "  per-user tokens, then 302s to stockagents://oauth/robinhood?status=ok.\n"
        "  For THIS harness test, read the code+state off that callback request\n"
        "  (server log / browser address bar) and paste them into `exchange`.\n\n"
        "FALLBACK — loopback on the SERVER (§5.1.4, demoted per §5.1.5.F):\n"
        "  Only if remote-HTTPS is rejected. Use --redirect-shape loopback with\n"
        "  --serve-loopback to run a one-shot 127.0.0.1 listener that captures the\n"
        "  code automatically (reuses brokers/oauth._capture_callback). Note the\n"
        "  spike found loopback is the WORST bet (direct negative evidence)."
    )


def _build_url_checklist(redirect_uri: str) -> List[CheckResult]:
    https = redirect_uri.lower().startswith("https://")
    return [
        CheckResult(
            "G1",
            UNKNOWN,
            "Open the URL logged in. If Robinhood redirects to your callback with "
            f"?code=..., the redirect ({redirect_uri}) was honored post-login -> "
            "then confirm with `exchange --live`. If you land on "
            "robinhood.com/oauth/error, the redirect was REJECTED.",
        ),
        CheckResult(
            "G2",
            UNKNOWN if https else FAIL,
            "Using an HTTPS callback" if https else
            "This redirect is NOT https; §5.1.5.F ships remote-https only.",
        ),
        CheckResult(
            "G6",
            UNKNOWN,
            "Re-run this command with a SECOND redirect (e.g. a prod vs staging "
            "HTTPS URL or --redirect-shape custom-scheme) and compare which the "
            "post-login step accepts.",
        ),
    ]


def _serve_loopback(cfg: HarnessConfig, expected_state: str) -> None:
    from tradingagents.brokers.oauth import _capture_callback

    print_section(f"Loopback listener on http://localhost:{cfg.callback_port}/callback")
    print("  Waiting (up to 300s) for Robinhood to redirect back ...")
    try:
        code, state = _capture_callback(cfg.callback_port)
    except Exception as exc:  # noqa: BLE001
        print(f"  capture failed: {exc}", file=sys.stderr)
        return
    matched = bool(state) and state == expected_state
    print_kv({"code_captured": bool(code), "state": state, "state_matches": matched})
    print(
        "\n  Next: run `exchange --live --code <CODE>` "
        "(state was validated here)." if matched else
        "\n  WARNING: state mismatch — do NOT exchange this code."
    )


def _serve_tunnel_callback(cfg: HarnessConfig, expected_state: str) -> None:
    """One-shot listener for the remote-HTTPS (tunnel) redirect shape.

    Binds 127.0.0.1:<callback_port> and serves <callback_path>, so a
    cloudflared/ngrok tunnel pointed at that port can forward Robinhood's
    post-login redirect here. Captures code/state, validates state, and stashes
    the (non-printed) code into the session so `exchange --live` can read it
    without the secret ever appearing on the command line or in logs.
    """
    print_section(
        f"Tunnel callback listener on http://127.0.0.1:{cfg.callback_port}{cfg.callback_path}"
    )
    print(
        "  Point your tunnel at this port, e.g.:\n"
        f"    cloudflared tunnel --url http://localhost:{cfg.callback_port}\n"
        f"    (or:  python -m scripts.oauth_live_test tunnel --port {cfg.callback_port})\n"
        "  Then open the authorize URL above while logged in to Robinhood.\n"
        "  Waiting (up to 300s) for the redirect ..."
    )
    capture: CallbackCapture = serve_one_callback(
        cfg.callback_port,
        cfg.callback_path,
        expected_state=expected_state,
    )

    if capture.timed_out:
        print("\n  Timed out waiting for the callback. Re-run when ready, or capture\n"
              "  the code/state from the browser address bar and pass them to `exchange`.")
        return
    if capture.error:
        print_kv({"error": capture.error, "state_matches": capture.state_matches})
        print("\n  Robinhood returned an error on the redirect — the callback URL was\n"
              "  likely REJECTED post-login (see §5.1.5.D). Do NOT run `exchange`.")
        return

    # Persist the captured code/state into the session (never print the code).
    try:
        session = flow.load_session(cfg.session_file)
    except (OSError, ValueError):
        session = {}
    session["captured_code"] = capture.code
    session["captured_state"] = capture.state
    flow.save_session(cfg.session_file, session)

    print_kv({
        "code_captured": bool(capture.code),
        "callback_path_hit": capture.path,
        "state_matches": capture.state_matches,
        "stored_in_session": cfg.session_file,
    })
    if capture.state_matches:
        print(
            "\n  State validated. The authorization code was saved to the session\n"
            "  (not printed). Next:\n"
            "    .venv/bin/python -m scripts.oauth_live_test exchange --live\n"
        )
    else:
        print(
            "\n  WARNING: state mismatch — possible CSRF/cross-session mixup.\n"
            "  Do NOT run `exchange` with this code."
        )


# ── command: exchange ─────────────────────────────────────────────────────────
def cmd_exchange(cfg: HarnessConfig, args) -> int:
    _require_live(args, "exchange")
    print_header("exchange --live — PKCE token exchange (CREDENTIALED; no orders)")
    try:
        session = flow.load_session(cfg.session_file)
    except (OSError, ValueError) as exc:
        print(f"\nERROR: cannot read session file {cfg.session_file}: {exc}\n"
              "Run build-authorize-url first.", file=sys.stderr)
        return 2

    if args.state and session.get("state") and args.state != session["state"]:
        print(f"\nERROR: --state does not match the saved session state. Aborting "
              "to avoid a cross-session/CSRF mixup.", file=sys.stderr)
        return 2

    # Code can come from --code or, for the tunnel flow, the listener-captured
    # value persisted in the session by `build-authorize-url --serve-callback`.
    code = args.code or session.get("captured_code")
    if not code:
        print("\nERROR: no authorization code. Pass --code <CODE>, or run "
              "`build-authorize-url --serve-callback` first so the tunnel listener "
              "captures it into the session.", file=sys.stderr)
        return 2

    asm = session.get("asm")
    prm = session.get("prm")
    if not asm:
        live = _run_discovery(cfg)
        asm, prm = live.asm, live.prm
    if not asm:
        print("\nERROR: no authorization-server metadata available.", file=sys.stderr)
        return 1

    res = asyncio.run(flow.exchange_code(
        mcp_url=session.get("mcp_url", cfg.mcp_url),
        client_name=cfg.client_name,
        redirect_uri=session["redirect_uri"],
        client_id=session["client_id"],
        asm=asm,
        prm=prm,
        scope=session.get("scope"),
        code=code,
        code_verifier=session["code_verifier"],
        token_path=cfg.token_path,
        timeout=cfg.timeout,
    ))

    print_section("Token exchange result")
    print_kv({
        "ok": res.ok,
        "http_status": res.http_status,
        "access_token_present": res.access_token_present,
        "access_token_len": res.access_token_len,
        "refresh_token_present": res.refresh_token_present,
        "expires_in_seconds": res.expires_in,
        "expires_in_human": _human_seconds(res.expires_in),
        "scope": res.scope,
        "token_type": res.token_type,
        "persisted_to": cfg.token_path,
        "persisted_ok": res.persisted,
        "error": res.error,
    })

    flow.record_result(cfg.results_file, "exchange", {
        "ok": res.ok,
        "http_status": res.http_status,
        "access_token_present": res.access_token_present,
        "refresh_token_present": res.refresh_token_present,
        "expires_in": res.expires_in,
        "obtained_at": res.obtained_at,
        "redirect_uri": session["redirect_uri"],
    })

    print_results(_exchange_checklist(res, session["redirect_uri"]))
    return 0 if res.ok else 1


def _exchange_checklist(res: "flow.ExchangeResult", redirect_uri: str) -> List[CheckResult]:
    return [
        CheckResult(
            "G1",
            PASS if res.ok else FAIL,
            f"A code from redirect {redirect_uri} exchanged successfully -> redirect "
            "was honored post-login." if res.ok else
            f"Exchange failed ({res.error or res.http_status}); the redirect may have "
            "been rejected or the code/verifier was invalid.",
        ),
        CheckResult(
            "G3",
            PASS if (res.access_token_present and res.persisted) else FAIL,
            f"access_token len={res.access_token_len}, refresh_token="
            f"{res.refresh_token_present}, persisted={res.persisted}.",
        ),
        CheckResult(
            "G2",
            PASS if res.ok else UNKNOWN,
            "Our exact HTTPS callback was accepted. Whether HTTPS is *broadly* "
            "accepted vs our URL specifically allowlisted still needs a second-URL "
            "test (see G6).",
        ),
        CheckResult("G4", UNKNOWN, "Run `refresh --live` to measure refresh TTL/rotation."),
    ]


# ── command: refresh ──────────────────────────────────────────────────────────
def cmd_refresh(cfg: HarnessConfig, args) -> int:
    _require_live(args, "refresh")
    print_header("refresh --live — refresh_token grant (CREDENTIALED; no orders)")

    # Prefer session metadata; fall back to live discovery.
    asm = prm = None
    redirect_uri = cfg.redirect_uri() if (cfg.explicit_redirect_uri or cfg.public_base_url or cfg.redirect_shape != "remote-https") else None
    try:
        session = flow.load_session(cfg.session_file)
        asm, prm = session.get("asm"), session.get("prm")
        redirect_uri = redirect_uri or session.get("redirect_uri")
    except (OSError, ValueError):
        session = {}
    if not asm:
        live = _run_discovery(cfg)
        asm, prm = live.asm, live.prm
    if not asm:
        print("\nERROR: no authorization-server metadata available.", file=sys.stderr)
        return 1
    if not redirect_uri:
        redirect_uri = "https://example.com/api/robinhood/callback"  # only used to shape client metadata

    res = asyncio.run(flow.refresh_tokens(
        mcp_url=session.get("mcp_url", cfg.mcp_url),
        client_name=cfg.client_name,
        redirect_uri=redirect_uri,
        asm=asm,
        prm=prm,
        token_path=cfg.token_path,
        timeout=cfg.timeout,
    ))

    print_section("Refresh result")
    print_kv({
        "ok": res.ok,
        "http_status": res.http_status,
        "access_token_changed": res.access_token_changed,
        "refresh_token_rotated": res.refresh_token_rotated,
        "new_expires_in_seconds": res.new_expires_in,
        "new_expires_in_human": _human_seconds(res.new_expires_in),
        "refreshed_at_epoch": res.refreshed_at,
        "error": res.error,
    })

    flow.record_result(cfg.results_file, "refresh", {
        "ok": res.ok,
        "http_status": res.http_status,
        "access_token_changed": res.access_token_changed,
        "refresh_token_rotated": res.refresh_token_rotated,
        "new_expires_in": res.new_expires_in,
        "refreshed_at": res.refreshed_at,
    })

    print(
        "\nTIP: to measure refresh-token TTL/rotation (G4), run `refresh --live` "
        "repeatedly over hours/days. Each run appends to the results file with a "
        "timestamp; compare `refresh_token_rotated` and `new_expires_in` over time."
    )
    print_results(_refresh_checklist(res))
    return 0 if res.ok else 1


def _refresh_checklist(res: "flow.RefreshResult") -> List[CheckResult]:
    if not res.ok:
        return [CheckResult("G4", FAIL, f"Refresh failed: {res.error or res.http_status}.")]
    rot = "rotated" if res.refresh_token_rotated else (
        "NOT rotated" if res.refresh_token_rotated is False else "unknown")
    return [CheckResult(
        "G4",
        PASS,
        f"Refresh succeeded; new access TTL={_human_seconds(res.new_expires_in)} "
        f"({res.new_expires_in}s); refresh_token {rot}. Repeat over time to confirm "
        "the re-consent cadence + family-revocation behaviour.",
    )]


def _human_seconds(seconds: Optional[int]) -> Optional[str]:
    if seconds is None:
        return None
    days = seconds / 86400.0
    if days >= 1:
        return f"~{days:.1f} days"
    hours = seconds / 3600.0
    return f"~{hours:.1f} hours"


# ── command: tunnel ───────────────────────────────────────────────────────────
def cmd_tunnel(cfg: HarnessConfig, args) -> int:
    print_header("tunnel — expose the local callback over public HTTPS (SAFE)")
    port = getattr(args, "port", None) or cfg.callback_port
    print(
        "This shells out to cloudflared/ngrok (whichever is installed) to give\n"
        "you a public https://... URL that forwards to the local callback\n"
        "listener. No credentials, no orders. Copy the URL into\n"
        "`build-authorize-url --public-base-url <url> --serve-callback`.\n"
    )
    return tunnel.run_tunnel(port, prefer=getattr(args, "tool", None))


# ── parser ────────────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.oauth_live_test",
        description=(
            "Live OAuth test harness for the Robinhood Trading MCP. Safe by "
            "default: discover/build-authorize-url need no credentials; "
            "exchange/refresh require --live and never place orders. "
            "Verifies the docs/ios-app-plan.md §5.1.5.G checklist."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_disc = sub.add_parser("discover", help="Unauthenticated metadata discovery + DCR probe.")
    _add_common_args(p_disc)
    p_disc.add_argument("--no-dcr-probe", action="store_true", help="Skip the unauthenticated DCR probe.")

    p_build = sub.add_parser("build-authorize-url", help="Build/print the authorize URL + PKCE.")
    _add_common_args(p_build)
    p_build.add_argument("--serve-loopback", action="store_true", help="Run a one-shot loopback listener to capture the code (loopback shape only).")
    p_build.add_argument("--serve-callback", action="store_true", help="Run a one-shot tunnel-aware listener on --callback-port/--callback-path to capture the redirect (use with --public-base-url set to a cloudflared/ngrok URL).")

    p_exch = sub.add_parser("exchange", help="[--live] PKCE token exchange for a pasted code.")
    _add_common_args(p_exch)
    p_exch.add_argument("--live", action="store_true", help="Required: perform the real credentialed token exchange.")
    p_exch.add_argument("--code", help="The authorization code Robinhood returned. Optional if a tunnel listener already captured it into the session.")
    p_exch.add_argument("--state", help="The state value returned (checked against the saved session).")

    p_ref = sub.add_parser("refresh", help="[--live] Exercise the refresh_token grant.")
    _add_common_args(p_ref)
    p_ref.add_argument("--live", action="store_true", help="Required: perform the real credentialed refresh.")

    p_tun = sub.add_parser("tunnel", help="Start a cloudflared/ngrok HTTPS tunnel to the local callback port (SAFE).")
    p_tun.add_argument("--port", type=int, help="Local port to expose (default: --callback-port / 8765).")
    p_tun.add_argument("--tool", choices=["cloudflared", "ngrok"], help="Force a specific tunnel tool.")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = build_config(args)

    command = args.command
    if command == "discover":
        return cmd_discover(cfg, args)
    if command == "build-authorize-url":
        return cmd_build_authorize_url(cfg, args)
    if command == "exchange":
        return cmd_exchange(cfg, args)
    if command == "refresh":
        return cmd_refresh(cfg, args)
    if command == "tunnel":
        return cmd_tunnel(cfg, args)
    parser.error(f"unknown command: {command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
