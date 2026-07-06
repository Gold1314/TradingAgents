-- Voice-conversational layer — multi-agent *panel* calls.
--
-- A panel call hosts several personas in one LiveKit room (moderated
-- turn-taking) instead of the solo one-agent-per-room model. Three additive,
-- nullable columns capture it; existing solo rows are untouched and the app
-- fails-open when the columns are missing (the Supabase upsert in
-- ``web/voice/db.create_panel_session`` only sets them when present).
--
--   * voice_sessions.mode           — 'solo' (default) or 'panel'.
--   * voice_sessions.agent_ids      — the full panelist roster (panel mode);
--                                     solo rows leave this null and keep using
--                                     the scalar ``agent_id`` column. For a
--                                     panel, ``agent_id`` stores the lead
--                                     (moderator) so existing per-agent queries
--                                     and the cost dashboard still work.
--   * voice_turns.speaker_agent_id  — which persona actually spoke an assistant
--                                     turn, so a panel transcript attributes
--                                     each line to the right agent.
--
-- Safe to apply at any time.

alter table if exists public.voice_sessions
    add column if not exists mode text not null default 'solo';

alter table if exists public.voice_sessions
    add column if not exists agent_ids text[];

alter table if exists public.voice_turns
    add column if not exists speaker_agent_id text;

-- Panel-session lookups (e.g. the run-history UI filtering to panels).
create index if not exists voice_sessions_mode_idx
    on public.voice_sessions (mode)
    where mode <> 'solo';
