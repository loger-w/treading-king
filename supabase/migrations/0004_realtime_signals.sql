-- supabase/migrations/0004_realtime_signals.sql
-- Phase 3 — active_signals + signals_log + daily_ohlc

create table if not exists active_signals (
  id                uuid primary key default gen_random_uuid(),
  name              text not null,
  filter_json       jsonb not null,
  scope             jsonb not null,
  cooldown_seconds  int  default 1800 check (cooldown_seconds between 60 and 86400),
  ignore_auctions   boolean default true,
  enabled           boolean default true,
  created_at        timestamptz default now()
);

create index if not exists idx_active_signals_enabled
  on active_signals(enabled) where enabled;

create table if not exists signals_log (
  id                bigserial primary key,
  active_signal_id  uuid references active_signals(id),
  symbol            text references symbols(symbol),
  triggered_at      timestamptz default now(),
  trigger_price     numeric,
  trigger_volume    bigint,
  context_json      jsonb
);

create index if not exists idx_signals_log_triggered_desc
  on signals_log(triggered_at desc);
create index if not exists idx_signals_log_symbol_time
  on signals_log(symbol, triggered_at desc);
create index if not exists idx_signals_log_active_signal_time
  on signals_log(active_signal_id, triggered_at desc);

create table if not exists daily_ohlc (
  symbol  text not null references symbols(symbol),
  date    date not null,
  open    numeric,
  high    numeric,
  low     numeric,
  close   numeric,
  primary key (symbol, date)
);

create index if not exists idx_daily_ohlc_date on daily_ohlc(date);

alter table active_signals enable row level security;
alter table signals_log    enable row level security;
alter table daily_ohlc     enable row level security;

create policy "anon can read active_signals" on active_signals for select to anon, authenticated using (true);
create policy "anon can read signals_log"    on signals_log    for select to anon, authenticated using (true);
create policy "anon can read daily_ohlc"     on daily_ohlc     for select to anon, authenticated using (true);
