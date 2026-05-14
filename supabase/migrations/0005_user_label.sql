-- 2026-05-13 — 本地版 + 共用 Supabase + user_label 隔離
-- 3 張個人表加 user_label;既有 row 全 backfill 為 'loger';之後 drop default。
-- 共用市場資料表(symbols / indicator_cache / daily_ohlc / cache_runs)不動。

-- ---------------------------------------------------------------------------
-- watchlist: PK 從 (symbol) 改為 (user_label, symbol)
-- ---------------------------------------------------------------------------
alter table watchlist add column if not exists user_label text not null default 'loger';
alter table watchlist drop constraint if exists watchlist_pkey;
alter table watchlist add primary key (user_label, symbol);
alter table watchlist alter column user_label drop default;
create index if not exists idx_watchlist_label on watchlist(user_label);

-- ---------------------------------------------------------------------------
-- active_signals
-- ---------------------------------------------------------------------------
alter table active_signals add column if not exists user_label text not null default 'loger';
alter table active_signals alter column user_label drop default;
create index if not exists idx_active_signals_label_enabled
  on active_signals(user_label, enabled) where enabled;

-- ---------------------------------------------------------------------------
-- signals_log
-- ---------------------------------------------------------------------------
alter table signals_log add column if not exists user_label text not null default 'loger';
alter table signals_log alter column user_label drop default;
create index if not exists idx_signals_log_label_time on signals_log(user_label, triggered_at desc);
