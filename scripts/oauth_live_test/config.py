"""Configuration for the OAuth live-test harness.

Everything an operator might need to vary — the MCP URL, the public base URL,
the redirect URI/shape, the loopback port, and where scratch session/token/result
files live — is configurable via flags or ``OAUTH_LIVE_TEST_*`` environment
variables. Defaults are chosen so the non-live commands run with **zero** setup.

Per the §5.1.5.F ship decision, the *primary* redirect shape is a remote HTTPS
callback; the harness also lets the operator try a custom-scheme or loopback
redirect so they can empirically discover what Robinhood's post-login allowlist
accepts (checklist items 1, 2, 6).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

from tradingagents.brokers.config import DEFAULT_MCP_URL

# Redirect shapes the operator can test. "remote-https" is the shipped choice.
REDIRECT_SHAPES = ("remote-https", "custom-scheme", "loopback")

DEFAULT_CALLBACK_PATH = "/api/robinhood/callback"
DEFAULT_CUSTOM_SCHEME_URI = "stockagents://oauth/robinhood"
DEFAULT_CLIENT_NAME = "TradingAgents"

# Scratch files default OUTSIDE the repo (under ~/.tradingagents) so nothing the
# harness writes can be accidentally committed. All are overridable.
_HARNESS_HOME = os.path.join(os.path.expanduser("~"), ".tradingagents", "oauth_live_test")
DEFAULT_SESSION_FILE = os.path.join(_HARNESS_HOME, "session.json")
DEFAULT_RESULTS_FILE = os.path.join(_HARNESS_HOME, "results.json")
# Deliberately NOT the real ~/.tradingagents/robinhood_token.json — the harness
# must never clobber the user's live broker token cache.
DEFAULT_TOKEN_PATH = os.path.join(_HARNESS_HOME, "harness_token.json")


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip()


@dataclass
class HarnessConfig:
    """Resolved settings for one harness invocation."""

    mcp_url: str
    public_base_url: Optional[str]
    redirect_shape: str
    explicit_redirect_uri: Optional[str]
    custom_scheme_uri: str
    callback_path: str
    callback_port: int
    client_name: str
    client_id: Optional[str]
    token_path: str
    session_file: str
    results_file: str
    timeout: float

    def redirect_uri(self) -> str:
        """Resolve the redirect URI from the explicit override or the shape."""
        if self.explicit_redirect_uri:
            return self.explicit_redirect_uri
        if self.redirect_shape == "remote-https":
            if not self.public_base_url:
                raise ValueError(
                    "redirect-shape 'remote-https' needs --public-base-url "
                    "(e.g. https://staging.example.com) or an explicit "
                    "--redirect-uri."
                )
            base = self.public_base_url.rstrip("/")
            return f"{base}{self.callback_path}"
        if self.redirect_shape == "custom-scheme":
            return self.custom_scheme_uri
        if self.redirect_shape == "loopback":
            return f"http://localhost:{self.callback_port}/callback"
        raise ValueError(f"Unknown redirect shape: {self.redirect_shape}")

    def describe_redirect(self) -> str:
        try:
            uri = self.redirect_uri()
        except ValueError as exc:
            return f"<unresolved: {exc}>"
        scheme = urlparse(uri).scheme or "?"
        return f"{uri}  (shape={self.redirect_shape}, scheme={scheme})"


def build_config(args) -> HarnessConfig:
    """Merge argparse ``args`` over ``OAUTH_LIVE_TEST_*`` env vars over defaults."""
    return HarnessConfig(
        mcp_url=(
            getattr(args, "mcp_url", None)
            or _env("OAUTH_LIVE_TEST_MCP_URL")
            or DEFAULT_MCP_URL
        ),
        public_base_url=(
            getattr(args, "public_base_url", None)
            or _env("OAUTH_LIVE_TEST_PUBLIC_BASE_URL")
        ),
        redirect_shape=(
            getattr(args, "redirect_shape", None)
            or _env("OAUTH_LIVE_TEST_REDIRECT_SHAPE")
            or "remote-https"
        ),
        explicit_redirect_uri=(
            getattr(args, "redirect_uri", None)
            or _env("OAUTH_LIVE_TEST_REDIRECT_URI")
        ),
        custom_scheme_uri=(
            getattr(args, "custom_scheme_uri", None)
            or _env("OAUTH_LIVE_TEST_CUSTOM_SCHEME_URI")
            or DEFAULT_CUSTOM_SCHEME_URI
        ),
        callback_path=(
            getattr(args, "callback_path", None)
            or _env("OAUTH_LIVE_TEST_CALLBACK_PATH")
            or DEFAULT_CALLBACK_PATH
        ),
        callback_port=int(
            getattr(args, "callback_port", None)
            or _env("OAUTH_LIVE_TEST_CALLBACK_PORT")
            or 8765
        ),
        client_name=(
            getattr(args, "client_name", None)
            or _env("OAUTH_LIVE_TEST_CLIENT_NAME")
            or DEFAULT_CLIENT_NAME
        ),
        client_id=(
            getattr(args, "client_id", None)
            or _env("OAUTH_LIVE_TEST_CLIENT_ID")
        ),
        token_path=os.path.expanduser(
            getattr(args, "token_path", None)
            or _env("OAUTH_LIVE_TEST_TOKEN_PATH")
            or DEFAULT_TOKEN_PATH
        ),
        session_file=os.path.expanduser(
            getattr(args, "session_file", None)
            or _env("OAUTH_LIVE_TEST_SESSION_FILE")
            or DEFAULT_SESSION_FILE
        ),
        results_file=os.path.expanduser(
            getattr(args, "results_file", None)
            or _env("OAUTH_LIVE_TEST_RESULTS_FILE")
            or DEFAULT_RESULTS_FILE
        ),
        timeout=float(
            getattr(args, "timeout", None)
            or _env("OAUTH_LIVE_TEST_TIMEOUT")
            or 30.0
        ),
    )
