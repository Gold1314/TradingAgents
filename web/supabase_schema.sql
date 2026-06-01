-- StockAgents — Supabase schema
-- Run this once in the Supabase SQL Editor (Dashboard ▸ SQL Editor ▸ New query).
--
-- These tables are written to ONLY by the FastAPI backend using the
-- service_role key, which BYPASSES Row Level Security. We enable RLS with no
-- policies so the public anon/authenticated keys are denied access (the browser
-- never touches these tables directly), while the backend keeps full access.

-- One row per completed analysis run.
create table if not exists public.runs (
    id            uuid primary key default gen_random_uuid(),
    ticker        text not null,
    trade_date    text not null,
    asset_type    text,
    provider      text,
    deep_model    text,
    quick_model   text,
    decision      text,             -- Buy / Overweight / Hold / Underweight / Sell
    final_content text,             -- full Portfolio Manager rationale
    identity      text,             -- resolved instrument context banner
    created_at    timestamptz not null default now()
);

-- Cache lookups query the latest run for a (ticker, trade_date).
create index if not exists runs_ticker_date_created_idx
    on public.runs (ticker, trade_date, created_at desc);

-- One row per agent output shown in the UI, ordered by seq.
create table if not exists public.agent_outputs (
    id         uuid primary key default gen_random_uuid(),
    run_id     uuid not null references public.runs (id) on delete cascade,
    seq        int not null,
    agent      text not null,
    content    text,
    created_at timestamptz not null default now()
);

create index if not exists agent_outputs_run_idx
    on public.agent_outputs (run_id, seq);

-- Global app settings (key/value). Holds the admin-controlled cache toggle.
create table if not exists public.app_settings (
    key        text primary key,
    value      jsonb not null,
    updated_at timestamptz not null default now()
);

-- Default: the 60-minute cache is ON until an admin turns it off.
insert into public.app_settings (key, value)
values ('cache_enabled', 'true'::jsonb)
on conflict (key) do nothing;

-- Enable RLS with no policies: denies anon/authenticated keys, while the
-- backend's service_role key bypasses RLS and retains full access.
alter table public.runs           enable row level security;
alter table public.agent_outputs  enable row level security;
alter table public.app_settings   enable row level security;
