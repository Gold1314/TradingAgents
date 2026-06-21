# Voice-Conversational Cost Model

Reference numbers and worked examples for the voice layer added in
`tradingagents/voice/` + `web/voice/`. All prices are USD and reflect public
list rates at the time of the roadmap (June 2026); update them in your env
to track your actual contract rates.

## Unit prices

| Layer | Provider | Default rate | Env override |
|---|---|---|---|
| ASR | Deepgram Nova-3 streaming | $0.0043 / min (~$0.000072 / s) | `TRADINGAGENTS_VOICE_PRICE_ASR_PER_SECOND` |
| TTS | ElevenLabs Flash v2.5 | $0.18 / min (~$0.003 / s) | `TRADINGAGENTS_VOICE_PRICE_TTS_PER_SECOND` |
| LLM input | OpenAI gpt-4o-mini | $0.15 / 1M tokens | `TRADINGAGENTS_VOICE_PRICE_LLM_INPUT_PER_1K` |
| LLM output | OpenAI gpt-4o-mini | $0.60 / 1M tokens | `TRADINGAGENTS_VOICE_PRICE_LLM_OUTPUT_PER_1K` |
| Transport | LiveKit Cloud | $0.0010 / participant-minute | — (not modelled per session) |

The worker estimates session cost from the wall-clock duration (40% input
audio / 60% output audio split — a typical "explainer + Q&A" mix) and the
LLM token usage reported by the plugin. Operators with hard SLA cost
targets should swap in their TTS/ASR plugin's per-session audio second
counters where available.

## Worked example — a 5-minute conversation

A representative session: the user holds a 5-minute back-and-forth with the
Bull Researcher, alternating ~2 minutes of user speech and ~3 minutes of
agent speech.

| Component | Rate | Volume | Subtotal |
|---|---|---|---|
| ASR (user speaking) | $0.000072 / s | 120 s | **$0.0086** |
| TTS (agent speaking) | $0.003 / s | 180 s | **$0.5400** |
| LLM input | $0.00015 / 1k | ~6k tokens (system + transcript + context) | **$0.0009** |
| LLM output | $0.00060 / 1k | ~1.5k tokens | **$0.0009** |
| Transport | — | 2 participants × 5 min | **$0.0100** |
| **Total** | | | **≈ $0.56 per session** |

TTS dominates; the user-facing per-minute cost is ~$0.11. ElevenLabs
Conversational AI's bundled price beats this slightly when you commit to
volume — Phase 6's cost dashboard surfaces actual realised rates.

## Worked example — a daily power user

Assume one user holds the maximum 30 minutes of voice per day
(`TRADINGAGENTS_VOICE_MINUTES_PER_USER_PER_DAY=30`):

* TTS: 18 min × $0.18 = **$3.24**
* ASR: 12 min × $0.0043 = **$0.052**
* LLM: ~$0.05 (estimated for 30 min of turn-taking with ~12k token context)
* Transport: 30 min × $0.001 × 2 participants = **$0.06**
* **Total ≈ $3.40 / day / power user**

Recommended initial guardrails:

* `TRADINGAGENTS_VOICE_SESSION_MAX_SECONDS=600` — single calls capped at 10 min.
* `TRADINGAGENTS_VOICE_MINUTES_PER_USER_PER_DAY=30` — covers ~3-6 sessions/day.
* `TRADINGAGENTS_VOICE_SESSIONS_PER_USER_PER_HOUR=12` — bursty but not abusive.

## Why ElevenLabs over OpenAI Realtime in v1

OpenAI Realtime API is cheaper per minute (~$0.06/min input + ~$0.24/min
output, ~$0.30/min combined ≈ $0.15/min for the 40/60 split). However:

* It exposes ~8 generic preset voices, all of which sound like the same
  speaker family. The vision is **12 distinct branded personas**; ElevenLabs
  has the voice library to deliver that.
* Realtime locks transport, ASR, and TTS to one provider — when ElevenLabs
  ships a better voice or Cartesia gets faster, the composable stack swaps
  in without re-architecting.
* The system prompt is identical on either path. The plan keeps Realtime
  available as a per-persona fallback (`openai_realtime_voice` field on
  `VoicePersona`) so we can A/B it without code changes.

## Cost dashboard

`GET /api/voice/admin/cost-summary?hours=24` (admin-gated) returns the
aggregate rollups powering the Phase 6 dashboard:

```json
{
  "sessions": 124,
  "minutes": 386.4,
  "cost_usd": 71.20,
  "by_agent": [
    { "agent_id": "Portfolio Manager", "sessions": 38, "minutes": 142.1, "cost_usd": 26.18 },
    { "agent_id": "Bull Researcher",   "sessions": 24, "minutes": 78.2,  "cost_usd": 14.40 },
    ...
  ]
}
```

The per-agent breakdown surfaces personas that are disproportionately
expensive (e.g. the PM and the Bull/Bear get the longest conversations) so
you can tune session caps or swap TTS models per persona.
