"""Environment-gated settings for the additive voice-conversational layer.

Mirrors the pattern in :mod:`web.mobile.settings`: every value is read at
*call time* (not import) so tests and operators can flip flags without
reimporting, and the master switch (``TRADINGAGENTS_VOICE_ENABLED``) defaults
to **OFF** — when unset the voice router is never mounted, the worker never
starts, and no UI affordances appear.

Three provider concerns live here:

* **Transport (LiveKit)** — API key, secret, and WebRTC URL. The signed room
  JWT minted by :mod:`web.voice.router` uses these credentials; clients
  receive the JWT + the WebRTC URL and never see the API secret.
* **ASR (Deepgram)** — speech-in. A single API key.
* **TTS (ElevenLabs / Cartesia)** — speech-out. ElevenLabs is the v1 default;
  Cartesia is supported as a hot-swappable alternative.

Per-user voice quota (minutes per day) is also defined here so the worker and
the router enforce the same cap regardless of which path mints the session.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Tuple

ENV_ENABLED = "TRADINGAGENTS_VOICE_ENABLED"

ENV_LIVEKIT_URL = "LIVEKIT_URL"
ENV_LIVEKIT_API_KEY = "LIVEKIT_API_KEY"
ENV_LIVEKIT_API_SECRET = "LIVEKIT_API_SECRET"

ENV_DEEPGRAM_API_KEY = "DEEPGRAM_API_KEY"
ENV_DEEPGRAM_MODEL = "DEEPGRAM_MODEL"

ENV_ELEVENLABS_API_KEY = "ELEVENLABS_API_KEY"
ENV_ELEVENLABS_MODEL = "ELEVENLABS_MODEL"
ENV_CARTESIA_API_KEY = "CARTESIA_API_KEY"

ENV_TTS_PROVIDER = "TRADINGAGENTS_VOICE_TTS_PROVIDER"
ENV_FALLBACK_PROVIDER = "TRADINGAGENTS_VOICE_FALLBACK_PROVIDER"

# Voice LLM. The worker reads ``anthropic_api_key`` directly off the settings
# snapshot (not ``os.environ``) because livekit-agents 1.6 forks subprocesses
# for each job and the child's environment is sanitized to whatever was
# explicitly passed through; reading via settings makes the call deterministic.
ENV_ANTHROPIC_API_KEY = "ANTHROPIC_API_KEY"
ENV_VOICE_LLM_PROVIDER = "TRADINGAGENTS_VOICE_LLM_PROVIDER"
ENV_VOICE_LLM_MODEL = "TRADINGAGENTS_VOICE_LLM_MODEL"

ENV_SESSION_MAX_SECONDS = "TRADINGAGENTS_VOICE_SESSION_MAX_SECONDS"
ENV_DAILY_MINUTES_PER_USER = "TRADINGAGENTS_VOICE_MINUTES_PER_USER_PER_DAY"
ENV_HOURLY_SESSIONS_PER_USER = "TRADINGAGENTS_VOICE_SESSIONS_PER_USER_PER_HOUR"

# Max agents in one panel call. More panelists = higher per-turn cost (each
# swap loads a persona) and slower routing, so keep this small. Hard-clamped
# to ``panel.MAX_PANEL_AGENTS_HARD`` in :func:`panel.normalize_roster`.
ENV_PANEL_MAX_AGENTS = "TRADINGAGENTS_VOICE_PANEL_MAX_AGENTS"

# Room JWT TTL. Shorter is safer — clients renew if a call lasts longer.
ENV_TOKEN_TTL_SECONDS = "TRADINGAGENTS_VOICE_TOKEN_TTL_SECONDS"

_TRUTHY = ("1", "true", "yes", "on")


def _env(name: str) -> Optional[str]:
    raw = os.environ.get(name)
    if raw is None:
        return None
    raw = raw.strip()
    return raw or None


def _int_env(name: str, default: int) -> int:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def voice_enabled() -> bool:
    """Master gate. Default OFF — the app is unchanged unless flipped on.

    Also returns False unless the three transport credentials are present, so a
    half-configured deployment surfaces as a clean "feature off" rather than a
    runtime error on first session.
    """
    if os.environ.get(ENV_ENABLED, "").strip().lower() not in _TRUTHY:
        return False
    return bool(
        _env(ENV_LIVEKIT_URL)
        and _env(ENV_LIVEKIT_API_KEY)
        and _env(ENV_LIVEKIT_API_SECRET)
    )


@dataclass(frozen=True)
class VoiceSettings:
    """Resolved snapshot of the voice configuration. Cheap to construct."""

    enabled: bool

    livekit_url: Optional[str]
    livekit_api_key: Optional[str]
    livekit_api_secret: Optional[str]

    deepgram_api_key: Optional[str]
    deepgram_model: str

    tts_provider: str
    fallback_provider: Optional[str]
    elevenlabs_api_key: Optional[str]
    elevenlabs_model: str
    cartesia_api_key: Optional[str]

    voice_llm_provider: Optional[str]
    voice_llm_model: Optional[str]
    anthropic_api_key: Optional[str]

    session_max_seconds: int
    daily_minutes_per_user: int
    hourly_sessions_per_user: int
    token_ttl_seconds: int
    panel_max_agents: int

    @property
    def transport_configured(self) -> bool:
        return bool(self.livekit_url and self.livekit_api_key and self.livekit_api_secret)

    @property
    def asr_configured(self) -> bool:
        return bool(self.deepgram_api_key)

    @property
    def tts_configured(self) -> bool:
        if self.tts_provider == "elevenlabs":
            return bool(self.elevenlabs_api_key)
        if self.tts_provider == "cartesia":
            return bool(self.cartesia_api_key)
        return False

    @property
    def fully_configured(self) -> Tuple[bool, str]:
        """Return ``(ok, reason)`` — surfaces *which* leg is missing on 503s."""
        if not self.enabled:
            return False, "voice is disabled (TRADINGAGENTS_VOICE_ENABLED=false)"
        if not self.transport_configured:
            return False, "LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET are required"
        if not self.asr_configured:
            return False, "DEEPGRAM_API_KEY is required"
        if not self.tts_configured:
            return False, f"{self.tts_provider.upper()}_API_KEY is required"
        return True, "ok"


def load_settings() -> VoiceSettings:
    """Read the current voice settings from the environment."""
    return VoiceSettings(
        enabled=voice_enabled(),
        livekit_url=_env(ENV_LIVEKIT_URL),
        livekit_api_key=_env(ENV_LIVEKIT_API_KEY),
        livekit_api_secret=_env(ENV_LIVEKIT_API_SECRET),
        deepgram_api_key=_env(ENV_DEEPGRAM_API_KEY),
        deepgram_model=_env(ENV_DEEPGRAM_MODEL) or "nova-3",
        tts_provider=(_env(ENV_TTS_PROVIDER) or "elevenlabs").lower(),
        fallback_provider=(_env(ENV_FALLBACK_PROVIDER) or "").lower() or None,
        elevenlabs_api_key=_env(ENV_ELEVENLABS_API_KEY),
        elevenlabs_model=_env(ENV_ELEVENLABS_MODEL) or "eleven_flash_v2_5",
        cartesia_api_key=_env(ENV_CARTESIA_API_KEY),
        voice_llm_provider=_env(ENV_VOICE_LLM_PROVIDER),
        voice_llm_model=_env(ENV_VOICE_LLM_MODEL),
        anthropic_api_key=_env(ENV_ANTHROPIC_API_KEY),
        session_max_seconds=_int_env(ENV_SESSION_MAX_SECONDS, 600),
        daily_minutes_per_user=_int_env(ENV_DAILY_MINUTES_PER_USER, 30),
        hourly_sessions_per_user=_int_env(ENV_HOURLY_SESSIONS_PER_USER, 12),
        token_ttl_seconds=_int_env(ENV_TOKEN_TTL_SECONDS, 1800),
        panel_max_agents=_int_env(ENV_PANEL_MAX_AGENTS, 4),
    )
