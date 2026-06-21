-- Voice-conversational layer — multi-agent panel ("group call") schema additions.
--
-- The single-agent voice session shape (one ``agent_id`` per session, one
-- assistant role per ``voice_turns`` row) is unchanged. This migration adds
-- two nullable columns so a single ``voice_sessions`` row can describe a
-- 2-3 persona panel, and so each ``voice_turns`` row records which panelist
-- actually spoke that line.
--
-- The application is fail-open against these columns: ``web/voice/db.py``
-- retries the insert/upsert without the new fields when PostgREST surfaces
-- an unknown-column error, so this migration can roll out independently of
-- the code deploy and a stale schema cache will not crash a session.

-- ──────────────────────────────────────────────────────────────────────────
-- voice_sessions.agent_ids — full panel roster.
--
-- Single-agent sessions leave this NULL; the existing ``agent_id`` column
-- continues to hold the (sole) panelist. Multi-agent sessions populate the
-- array with the full ordered roster (the first entry mirrors ``agent_id``
-- so older queries that only read ``agent_id`` still see one of the
-- panelists rather than NULL).
-- ──────────────────────────────────────────────────────────────────────────
alter table if exists public.voice_sessions
    add column if not exists agent_ids text[];

-- Index reserved for future ``WHERE agent_ids @> ARRAY['Bull Researcher']``
-- queries from the analytics dashboard. ``gin`` is the right operator class
-- for array-containment lookups.
create index if not exists voice_sessions_agent_ids_idx
    on public.voice_sessions using gin (agent_ids)
    where agent_ids is not null;

-- ──────────────────────────────────────────────────────────────────────────
-- voice_turns.speaker_agent_id — which panelist spoke this line.
--
-- For single-agent sessions this is redundant (it's the session's only
-- agent) and stays NULL by default. For panels it labels each assistant
-- turn with the persona whose voice was used so the in-call UI can render a
-- per-line badge ("BULL", "BEAR", …) without consulting the panel roster.
-- ──────────────────────────────────────────────────────────────────────────
alter table if exists public.voice_turns
    add column if not exists speaker_agent_id text;

create index if not exists voice_turns_speaker_idx
    on public.voice_turns (session_id, speaker_agent_id)
    where speaker_agent_id is not null;
