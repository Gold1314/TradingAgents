"""Voice-conversational agents — additive feature, default OFF.

This subpackage adds a real-time, full-duplex voice dialogue capability on top
of the existing 12-agent LangGraph pipeline. Each agent gets a distinct branded
voice (ElevenLabs) and a persona prompt; users tap an agent card in the web /
iOS / RN client and enter a WebRTC voice call (LiveKit) with that one agent.

Design tenets (see ``docs/voice-conversational-roadmap.md`` for the full plan):

1. **Purely additive.** The LangGraph pipeline, SSE event surface, and every
   existing client flow are untouched. Voice is gated behind
   ``TRADINGAGENTS_VOICE_ENABLED`` (default false) so the app behaves byte-for-
   byte the same when the feature is off.

2. **Reuses your LLM factory.** The voice agent's "brain" plugs into the same
   :mod:`tradingagents.llm_clients` factory — no second provider stack.

3. **Provider-agnostic transport.** :mod:`tradingagents.voice.agent_worker`
   wraps the LiveKit Agents runtime; ASR (Deepgram), TTS (ElevenLabs), and LLM
   are pluggable so an OpenAI Realtime adapter can drop in later without
   touching callers.

4. **Hard-constrained context.** Each session loads the agent's own report and
   read-only summaries of sibling reports + the debate transcripts via
   :mod:`tradingagents.voice.context_loader`. The agent has no live-data tools
   in v1 — only its own analysis from the just-completed run.

5. **Optional dependencies.** ``livekit-agents``, ``deepgram-sdk``,
   ``elevenlabs`` are NOT in the core install. The worker module imports them
   lazily so this package stays importable in environments that don't need
   voice (CI, tests, the CLI).
"""

from __future__ import annotations
