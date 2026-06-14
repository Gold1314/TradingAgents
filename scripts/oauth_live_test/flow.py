"""Authorize-URL construction + token exchange/refresh, reusing real machinery.

Wherever a step can be delegated to the installed ``mcp`` SDK or the repo's
``tradingagents.brokers.oauth`` module, it is — so what the harness exercises is
the *same* code the production server-mediated flow (§5.1.3) will run:

* PKCE generation  -> ``mcp.client.auth.oauth2.PKCEParameters``
* DCR              -> ``create_client_registration_request`` / ``handle_registration_response``
* authorize params -> mirror ``OAuthClientProvider._perform_authorization_code_grant``
* token exchange   -> ``OAuthClientProvider._exchange_token_authorization_code`` + ``_handle_token_response``
* token refresh    -> ``OAuthClientProvider._refresh_token`` + ``_handle_refresh_response``
* token storage    -> ``tradingagents.brokers.oauth.FileTokenStorage``

The exchange/refresh steps are gated by the CLI behind ``--live`` and **never**
touch any order/trade tool.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import urlencode, urlparse

import httpx

from tradingagents.brokers.oauth import FileTokenStorage

try:
    from mcp.client.auth.oauth2 import OAuthClientProvider, PKCEParameters
    from mcp.client.auth.utils import (
        create_client_registration_request,
        handle_registration_response,
    )
    from mcp.shared.auth import (
        OAuthClientInformationFull,
        OAuthClientMetadata,
        OAuthMetadata,
        OAuthToken,
        ProtectedResourceMetadata,
    )

    MCP_AVAILABLE = True
except Exception as _exc:  # noqa: BLE001 — record why, surface on use
    MCP_AVAILABLE = False
    _MCP_IMPORT_ERROR = _exc


def _require_mcp() -> None:
    if not MCP_AVAILABLE:
        raise RuntimeError(
            "The 'mcp' SDK is required for build-authorize-url/exchange/refresh. "
            f"Install it (pip install langchain-mcp-adapters mcp). Import error: {_MCP_IMPORT_ERROR}"
        )


async def _noop_redirect(_url: str) -> None:  # pragma: no cover - unused handler
    return None


async def _noop_callback():  # pragma: no cover - unused handler
    return "", None


def _client_metadata(client_name: str, redirect_uri: str) -> "OAuthClientMetadata":
    """Build OAuthClientMetadata identically to brokers/oauth.build_oauth_provider."""
    return OAuthClientMetadata(
        client_name=client_name,
        redirect_uris=[redirect_uri],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",
    )


def _make_provider(
    mcp_url: str,
    client_name: str,
    redirect_uri: str,
    storage: FileTokenStorage,
) -> "OAuthClientProvider":
    return OAuthClientProvider(
        server_url=mcp_url,
        client_metadata=_client_metadata(client_name, redirect_uri),
        storage=storage,
        redirect_handler=_noop_redirect,
        callback_handler=_noop_callback,
    )


def _apply_discovery(provider: "OAuthClientProvider", asm: Dict[str, Any], prm: Optional[Dict[str, Any]]) -> None:
    """Populate the provider context with discovered metadata (no network)."""
    provider.context.oauth_metadata = OAuthMetadata.model_validate(asm)
    if prm:
        try:
            provider.context.protected_resource_metadata = (
                ProtectedResourceMetadata.model_validate(prm)
            )
        except Exception:  # noqa: BLE001 — PRM is optional for our purposes
            provider.context.protected_resource_metadata = None


# ── DCR: obtain the (shared) client_id without credentials ────────────────────
async def register_client(
    mcp_url: str,
    client_name: str,
    redirect_uri: str,
    asm: Dict[str, Any],
    timeout: float,
) -> "OAuthClientInformationFull":
    _require_mcp()
    metadata = _client_metadata(client_name, redirect_uri)
    asm_model = OAuthMetadata.model_validate(asm)
    parsed = urlparse(mcp_url)
    auth_base = f"{parsed.scheme}://{parsed.netloc}"
    request = create_client_registration_request(asm_model, metadata, auth_base)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.send(request)
    return await handle_registration_response(response)


# ── session persistence (PKCE verifier/state) ────────────────────────────────
def save_session(path: str, data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


def load_session(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ── build-authorize-url ───────────────────────────────────────────────────────
@dataclass
class AuthorizeUrl:
    url: str
    state: str
    code_verifier: str
    code_challenge: str
    client_id: str
    redirect_uri: str
    scope: Optional[str]


def build_authorize_url(
    mcp_url: str,
    client_name: str,
    redirect_uri: str,
    client_id: str,
    asm: Dict[str, Any],
    prm: Optional[Dict[str, Any]],
    scope: Optional[str],
) -> AuthorizeUrl:
    """Construct the authorize URL exactly as the SDK would for this client."""
    _require_mcp()
    storage = FileTokenStorage(os.devnull)  # not persisted here; URL build only
    provider = _make_provider(mcp_url, client_name, redirect_uri, storage)
    _apply_discovery(provider, asm, prm)
    provider.context.client_info = OAuthClientInformationFull(
        client_id=client_id,
        token_endpoint_auth_method="none",
        redirect_uris=[redirect_uri],
    )

    pkce = PKCEParameters.generate()
    state = secrets.token_urlsafe(32)
    auth_endpoint = str(provider.context.oauth_metadata.authorization_endpoint)

    params: Dict[str, str] = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": pkce.code_challenge,
        "code_challenge_method": "S256",
    }
    if provider.context.should_include_resource_param(None):
        params["resource"] = provider.context.get_resource_url()
    if scope:
        params["scope"] = scope

    url = f"{auth_endpoint}?{urlencode(params)}"
    return AuthorizeUrl(
        url=url,
        state=state,
        code_verifier=pkce.code_verifier,
        code_challenge=pkce.code_challenge,
        client_id=client_id,
        redirect_uri=redirect_uri,
        scope=scope,
    )


# ── exchange (opt-in --live) ──────────────────────────────────────────────────
@dataclass
class ExchangeResult:
    ok: bool
    http_status: Optional[int]
    access_token_present: bool
    access_token_len: int
    refresh_token_present: bool
    expires_in: Optional[int]
    scope: Optional[str]
    token_type: Optional[str]
    persisted: bool
    obtained_at: float
    error: Optional[str] = None


async def exchange_code(
    mcp_url: str,
    client_name: str,
    redirect_uri: str,
    client_id: str,
    asm: Dict[str, Any],
    prm: Optional[Dict[str, Any]],
    scope: Optional[str],
    code: str,
    code_verifier: str,
    token_path: str,
    timeout: float,
) -> ExchangeResult:
    """Exchange ``code`` for tokens via the SDK and persist with FileTokenStorage."""
    _require_mcp()
    storage = FileTokenStorage(token_path)
    provider = _make_provider(mcp_url, client_name, redirect_uri, storage)
    _apply_discovery(provider, asm, prm)
    if scope:
        provider.context.client_metadata.scope = scope
    provider.context.client_info = OAuthClientInformationFull(
        client_id=client_id,
        token_endpoint_auth_method="none",
        redirect_uris=[redirect_uri],
    )
    # Persist client_info so a later `refresh` can reuse the same client.
    await storage.set_client_info(provider.context.client_info)

    request = await provider._exchange_token_authorization_code(code, code_verifier)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.send(request)

    obtained_at = time.time()
    if response.status_code != 200:
        body = (await response.aread()).decode("utf-8", "replace")
        return ExchangeResult(
            ok=False,
            http_status=response.status_code,
            access_token_present=False,
            access_token_len=0,
            refresh_token_present=False,
            expires_in=None,
            scope=None,
            token_type=None,
            persisted=False,
            obtained_at=obtained_at,
            error=f"token endpoint returned HTTP {response.status_code}: {body[:500]}",
        )

    # Let the SDK parse + persist (this is the real persistence path).
    await provider._handle_token_response(response)
    saved = await storage.get_tokens()
    token: Optional["OAuthToken"] = provider.context.current_tokens
    access = (token.access_token if token else "") or ""
    return ExchangeResult(
        ok=bool(access),
        http_status=200,
        access_token_present=bool(access),
        access_token_len=len(access),
        refresh_token_present=bool(token and token.refresh_token),
        expires_in=token.expires_in if token else None,
        scope=token.scope if token else None,
        token_type=token.token_type if token else None,
        persisted=bool(saved and saved.access_token),
        obtained_at=obtained_at,
    )


# ── refresh (opt-in --live) ───────────────────────────────────────────────────
@dataclass
class RefreshResult:
    ok: bool
    http_status: Optional[int]
    access_token_changed: Optional[bool]
    refresh_token_rotated: Optional[bool]
    new_expires_in: Optional[int]
    refreshed_at: float
    error: Optional[str] = None


async def refresh_tokens(
    mcp_url: str,
    client_name: str,
    redirect_uri: str,
    asm: Dict[str, Any],
    prm: Optional[Dict[str, Any]],
    token_path: str,
    timeout: float,
) -> RefreshResult:
    """Exercise the refresh_token grant via the SDK; report rotation/TTL."""
    _require_mcp()
    storage = FileTokenStorage(token_path)
    provider = _make_provider(mcp_url, client_name, redirect_uri, storage)
    _apply_discovery(provider, asm, prm)

    current = await storage.get_tokens()
    client_info = await storage.get_client_info()
    if not current or not current.refresh_token:
        return RefreshResult(
            ok=False, http_status=None, access_token_changed=None,
            refresh_token_rotated=None, new_expires_in=None, refreshed_at=time.time(),
            error="No saved refresh_token. Run `exchange --live` first.",
        )
    if not client_info:
        return RefreshResult(
            ok=False, http_status=None, access_token_changed=None,
            refresh_token_rotated=None, new_expires_in=None, refreshed_at=time.time(),
            error="No saved client_info. Run `exchange --live` first.",
        )

    provider.context.current_tokens = current
    provider.context.client_info = client_info
    old_access = current.access_token or ""
    old_refresh = current.refresh_token or ""

    request = await provider._refresh_token()
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.send(request)

    refreshed_at = time.time()
    if response.status_code != 200:
        body = (await response.aread()).decode("utf-8", "replace")
        return RefreshResult(
            ok=False, http_status=response.status_code, access_token_changed=None,
            refresh_token_rotated=None, new_expires_in=None, refreshed_at=refreshed_at,
            error=f"refresh returned HTTP {response.status_code}: {body[:500]}",
        )

    ok = await provider._handle_refresh_response(response)
    new = provider.context.current_tokens
    new_access = (new.access_token if new else "") or ""
    new_refresh = (new.refresh_token if new else "") or ""
    return RefreshResult(
        ok=bool(ok and new_access),
        http_status=200,
        access_token_changed=(new_access != old_access),
        refresh_token_rotated=(new_refresh != old_refresh) if new_refresh else None,
        new_expires_in=new.expires_in if new else None,
        refreshed_at=refreshed_at,
    )


def record_result(results_file: str, command: str, payload: Dict[str, Any]) -> None:
    """Append a timestamped record so the operator has a durable measurement log."""
    os.makedirs(os.path.dirname(results_file) or ".", exist_ok=True)
    existing: list = []
    if os.path.exists(results_file):
        try:
            with open(results_file, "r", encoding="utf-8") as fh:
                existing = json.load(fh)
                if not isinstance(existing, list):
                    existing = [existing]
        except (OSError, ValueError):
            existing = []
    existing.append({"command": command, "recorded_at": time.time(), **payload})
    with open(results_file, "w", encoding="utf-8") as fh:
        json.dump(existing, fh, indent=2)
