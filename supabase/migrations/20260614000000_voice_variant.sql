-- Voice-conversational layer — Phase 6 persona A/B framework.
--
-- Adds a ``variant`` column so cost / quality analyses can be sliced by the
-- persona variant the user heard. Variants are minted client-side by
-- ``tradingagents.voice.personas.assign_variant`` (a stable hash on
-- (agent_id, user_id)). The column is intentionally nullable so older
-- session rows remain valid.
--
-- Safe to apply at any time; the app fails-open if the column is missing
-- (the Supabase upsert in ``web/voice/db.create_session`` skips ``variant``
-- when it is ``None``).

alter table if exists public.voice_sessions
    add column if not exists variant text;

create index if not exists voice_sessions_variant_idx
    on public.voice_sessions (agent_id, variant)
    where variant is not null;
