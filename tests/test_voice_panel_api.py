"""Endpoint tests for POST /api/voice/panels (web/voice/router.py).

Builds the real voice router against a fake ``server`` module with the LiveKit
admin, token minting, run-context load, and Supabase persistence mocked, so the
panel validation + provisioning flow is exercised without any live infra.

Guarded with ``importorskip`` because ``web.voice.router`` pulls the voice
stack (which imports the reconcile/LLM path); slim CI without those deps skips.
"""

import types

import pytest

pytest.importorskip("pandas")
pytest.importorskip("langgraph")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from tradingagents.voice.settings import VoiceSettings  # noqa: E402


def _ready_settings(panel_max_agents: int = 4) -> VoiceSettings:
    return VoiceSettings(
        enabled=True,
        livekit_url="wss://example.livekit.cloud",
        livekit_api_key="key",
        livekit_api_secret="secret",
        deepgram_api_key="dg",
        deepgram_model="nova-3",
        tts_provider="elevenlabs",
        fallback_provider=None,
        elevenlabs_api_key="el",
        elevenlabs_model="eleven_flash_v2_5",
        cartesia_api_key=None,
        voice_llm_provider="anthropic",
        voice_llm_model="claude-haiku-4-5-20251001",
        anthropic_api_key="ak",
        session_max_seconds=600,
        daily_minutes_per_user=0,      # disable caps for the test
        hourly_sessions_per_user=0,
        token_ttl_seconds=1800,
        panel_max_agents=panel_max_agents,
    )


@pytest.fixture()
def client(monkeypatch):
    from web.voice import router as vr

    # Fake run context: every agent has a finished report.
    class _Ctx:
        ticker = "NVDA"
        trade_date = "2026-07-01"
        asset_type = "stock"

        def __init__(self, agents):
            self.agents = {a: object() for a in agents}

    # All 12 personas "finished" for this run.
    from tradingagents.voice.personas import PERSONAS
    ctx = _Ctx(list(PERSONAS.keys()))

    monkeypatch.setattr(vr, "load_settings", lambda: _ready_settings())
    monkeypatch.setattr(vr, "load_run_context", lambda run_id, manager: ctx)
    monkeypatch.setattr(vr, "_resolve_user_id", lambda request, authorization: None)

    async def _ensure_room(**kwargs):
        return None

    monkeypatch.setattr(vr.livekit_admin, "ensure_room", _ensure_room)
    monkeypatch.setattr(vr, "mint_room_token", lambda **kwargs: "test-token")

    created = {}

    def _create_panel_session(**kwargs):
        created.update(kwargs)
        return True

    monkeypatch.setattr(vr.voice_db, "create_panel_session", _create_panel_session)

    fake_server = types.SimpleNamespace(manager=types.SimpleNamespace(get=lambda rid: None))
    app = FastAPI()
    app.include_router(vr.build_voice_router(fake_server))
    tc = TestClient(app)
    tc._created = created  # expose for assertions
    return tc


def test_panel_happy_path(client):
    r = client.post(
        "/api/voice/panels",
        json={"run_id": "run1", "agent_ids": ["Bull Researcher", "Bear Researcher"]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "panel"
    assert body["token"] == "test-token"
    assert body["room"].startswith("voice-run1-panel-")
    assert [p["agent_id"] for p in body["personas"]] == ["Bull Researcher", "Bear Researcher"]
    # Lead defaults to first when no PM on the roster.
    assert body["lead_agent_id"] == "Bull Researcher"
    # Persisted with the full roster + lead in agent_id.
    assert client._created["agent_ids"] == ["Bull Researcher", "Bear Researcher"]
    assert client._created["lead_agent_id"] == "Bull Researcher"


def test_panel_lead_prefers_pm(client):
    r = client.post(
        "/api/voice/panels",
        json={"run_id": "r", "agent_ids": ["Bull Researcher", "Portfolio Manager"]},
    )
    assert r.status_code == 200
    assert r.json()["lead_agent_id"] == "Portfolio Manager"


def test_panel_rejects_single_agent(client):
    r = client.post(
        "/api/voice/panels", json={"run_id": "r", "agent_ids": ["Bull Researcher"]}
    )
    assert r.status_code == 400
    assert "at least 2" in r.json()["detail"]


def test_panel_rejects_unknown_agent(client):
    r = client.post(
        "/api/voice/panels",
        json={"run_id": "r", "agent_ids": ["Bull Researcher", "Nobody"]},
    )
    assert r.status_code == 400
    assert "unknown agent_id" in r.json()["detail"]


def test_panel_enforces_cap(client):
    r = client.post(
        "/api/voice/panels",
        json={
            "run_id": "r",
            "agent_ids": [
                "Market Analyst", "Sentiment Analyst", "News Analyst",
                "Fundamentals Analyst", "Bull Researcher",
            ],
        },
    )
    assert r.status_code == 400
    assert "capped at 4" in r.json()["detail"]


def test_panel_409_when_agent_report_missing(client, monkeypatch):
    from web.voice import router as vr

    class _PartialCtx:
        ticker = "NVDA"
        trade_date = "2026-07-01"
        asset_type = "stock"
        agents = {"Bull Researcher": object()}  # Bear not finished

    monkeypatch.setattr(vr, "load_run_context", lambda run_id, manager: _PartialCtx())
    r = client.post(
        "/api/voice/panels",
        json={"run_id": "r", "agent_ids": ["Bull Researcher", "Bear Researcher"]},
    )
    assert r.status_code == 409
