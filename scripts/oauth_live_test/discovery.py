"""Unauthenticated authorization-server metadata discovery + DCR probe.

This mirrors exactly what the ``mcp`` SDK does on a 401 (see
``mcp/client/auth/oauth2.py`` ``async_auth_flow`` and
``mcp/client/auth/utils.py``): probe the MCP URL, read the
``WWW-Authenticate`` header, fetch Protected Resource Metadata (PRM) and
Authorization Server Metadata (ASM) from the well-known URLs, and (optionally)
send an unauthenticated Dynamic Client Registration request.

None of this needs credentials. It establishes the *preconditions* for the
credentialed checklist (it confirms the endpoints + token-exchange URL the live
steps will use), and corroborates the §5.1.5.C/D spike findings.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx


@dataclass
class DiscoveryResult:
    """Everything the unauthenticated probe learned."""

    ok: bool
    mcp_url: str
    www_authenticate: Optional[str] = None
    prm_url: Optional[str] = None
    prm: Optional[Dict[str, Any]] = None
    asm_url: Optional[str] = None
    asm: Optional[Dict[str, Any]] = None
    errors: List[str] = field(default_factory=list)

    @property
    def authorization_endpoint(self) -> Optional[str]:
        return (self.asm or {}).get("authorization_endpoint")

    @property
    def token_endpoint(self) -> Optional[str]:
        return (self.asm or {}).get("token_endpoint")

    @property
    def registration_endpoint(self) -> Optional[str]:
        return (self.asm or {}).get("registration_endpoint")

    @property
    def scopes_supported(self) -> List[str]:
        scopes = (self.prm or {}).get("scopes_supported") or (
            self.asm or {}
        ).get("scopes_supported")
        return list(scopes or [])


def _base(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _prm_well_known_urls(mcp_url: str) -> List[str]:
    """Mirror build_protected_resource_metadata_discovery_urls (well-known part)."""
    parsed = urlparse(mcp_url)
    base = _base(mcp_url)
    urls: List[str] = []
    if parsed.path and parsed.path != "/":
        urls.append(f"{base}/.well-known/oauth-protected-resource{parsed.path}")
    urls.append(f"{base}/.well-known/oauth-protected-resource")
    return urls


def _asm_well_known_urls(auth_server_url: str) -> List[str]:
    """Mirror build_oauth_authorization_server_metadata_discovery_urls."""
    parsed = urlparse(auth_server_url)
    base = _base(auth_server_url)
    urls: List[str] = []
    if parsed.path and parsed.path != "/":
        stem = parsed.path.rstrip("/")
        urls.append(f"{base}/.well-known/oauth-authorization-server{stem}")
        urls.append(f"{base}/.well-known/openid-configuration{stem}")
        urls.append(f"{base}{stem}/.well-known/openid-configuration")
    else:
        urls.append(f"{base}/.well-known/oauth-authorization-server")
        urls.append(f"{base}/.well-known/openid-configuration")
    return urls


def _extract_resource_metadata(www_auth: str) -> Optional[str]:
    match = re.search(r'resource_metadata=(?:"([^"]+)"|([^\s,]+))', www_auth)
    if match:
        return match.group(1) or match.group(2)
    return None


def _get_json(client: httpx.Client, url: str, errors: List[str]) -> Optional[Dict[str, Any]]:
    try:
        resp = client.get(url, headers={"MCP-Protocol-Version": "2025-06-18"})
    except httpx.HTTPError as exc:
        errors.append(f"GET {url} failed: {exc}")
        return None
    if resp.status_code != 200:
        errors.append(f"GET {url} -> HTTP {resp.status_code}")
        return None
    try:
        return resp.json()
    except (ValueError, json.JSONDecodeError) as exc:
        errors.append(f"GET {url} returned non-JSON: {exc}")
        return None


def discover(mcp_url: str, timeout: float = 30.0) -> DiscoveryResult:
    """Run the full unauthenticated discovery against ``mcp_url``."""
    result = DiscoveryResult(ok=False, mcp_url=mcp_url)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            # Step 0: provoke the 401 + WWW-Authenticate, like a real MCP client.
            try:
                probe = client.post(mcp_url, json={"jsonrpc": "2.0", "method": "ping", "id": 1})
                result.www_authenticate = probe.headers.get("WWW-Authenticate")
                if result.www_authenticate:
                    result.prm_url = _extract_resource_metadata(result.www_authenticate)
            except httpx.HTTPError as exc:
                result.errors.append(f"MCP probe POST failed: {exc}")

            # Step 1: Protected Resource Metadata.
            prm_candidates: List[str] = []
            if result.prm_url:
                prm_candidates.append(result.prm_url)
            prm_candidates.extend(_prm_well_known_urls(mcp_url))
            for url in prm_candidates:
                prm = _get_json(client, url, result.errors)
                if prm:
                    result.prm = prm
                    result.prm_url = url
                    break

            # Step 2: Authorization Server Metadata.
            auth_servers = (result.prm or {}).get("authorization_servers") or []
            auth_server_url = auth_servers[0] if auth_servers else mcp_url
            for url in _asm_well_known_urls(auth_server_url):
                asm = _get_json(client, url, result.errors)
                if asm and asm.get("authorization_endpoint"):
                    result.asm = asm
                    result.asm_url = url
                    break

        result.ok = bool(result.asm and result.asm.get("authorization_endpoint"))
    except httpx.HTTPError as exc:  # network blocked / DNS / TLS
        result.errors.append(f"network error: {exc}")
    return result


@dataclass
class DcrProbeResult:
    """Outcome of one unauthenticated Dynamic Client Registration POST."""

    ok: bool
    status_code: Optional[int]
    sent_redirect_uris: List[str]
    client_id: Optional[str] = None
    client_name: Optional[str] = None
    has_client_secret: Optional[bool] = None
    echoed_redirect_uris: List[str] = field(default_factory=list)
    error: Optional[str] = None


def probe_dcr(
    registration_endpoint: str,
    redirect_uris: List[str],
    client_name: str,
    timeout: float = 30.0,
) -> DcrProbeResult:
    """Send one unauthenticated DCR request and summarise the response.

    Safe + credential-free: per §5.1.5.D Robinhood returns a shared, fixed
    public client for any caller and merely echoes the redirect_uris, so this
    neither creates nor persists anything per-client.
    """
    payload = {
        "client_name": client_name,
        "redirect_uris": redirect_uris,
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.post(
                registration_endpoint,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
    except httpx.HTTPError as exc:
        return DcrProbeResult(
            ok=False, status_code=None, sent_redirect_uris=redirect_uris, error=str(exc)
        )
    try:
        body = resp.json()
    except (ValueError, json.JSONDecodeError):
        body = {}
    return DcrProbeResult(
        ok=resp.status_code in (200, 201),
        status_code=resp.status_code,
        sent_redirect_uris=redirect_uris,
        client_id=body.get("client_id"),
        client_name=body.get("client_name"),
        has_client_secret=bool(body.get("client_secret")),
        echoed_redirect_uris=list(body.get("redirect_uris") or []),
        error=None if resp.status_code in (200, 201) else f"HTTP {resp.status_code}",
    )
