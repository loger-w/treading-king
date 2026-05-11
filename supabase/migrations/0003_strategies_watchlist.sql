-- Phase 2b — strategies + watchlist
-- Plan §Phase 2b 對應。

-- ---------------------------------------------------------------------------
-- strategies：儲存自定篩股條件（filter_json 是 Filter pydantic 的 JSON 序列）
-- ---------------------------------------------------------------------------

create table if not exists strategies (
  id            uuid primary key default gen_random_uuid(),
  name          text not null,
  description   text,
  filter_json   jsonb not null,
  created_at    timestamptz default now()
);

create index if not exists idx_strategies_created on strategies(created_at desc);

-- ---------------------------------------------------------------------------
-- watchlist：自選清單 = 即時監控池（Phase 3 active_signals.scope 會用）
-- ---------------------------------------------------------------------------

create table if not exists watchlist (
  symbol      text primary key references symbols(symbol),
  added_at    timestamptz default now(),
  note        text
);

-- ---------------------------------------------------------------------------
-- RLS：anon 可讀（個人自用，不嚴格區隔讀寫），service_role 才能寫
-- ---------------------------------------------------------------------------

alter table strategies enable row level security;
alter table watchlist enable row level security;

create policy "anon can read strategies"
  on strategies for select
  to anon, authenticated
  using (true);

create policy "anon can read watchlist"
  on watchlist for select
  to anon, authenticated
  using (true);
