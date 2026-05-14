-- indicator_cache + indicator_cache_runs
--
-- 每日盤後 cache job 灌 16 個 technical indicator(rsi/macd/kdj/sma/bbands)+ 收盤價量。
-- 即時訊號 evaluator 讀這張表算 indicator 條件(`rsi_14 < 30` 等)。
--
-- 「同表 + date filter staging」路線：reader 用 SELECT MAX(run_date)
-- WHERE status='done' 取最後一次成功的 date,避開 rename swap 的 race。

-- ---------------------------------------------------------------------------
-- indicator_cache：每檔每日的價量 + 5 個 technical 快取
-- ---------------------------------------------------------------------------

create table if not exists indicator_cache (
  symbol         text not null references symbols(symbol),
  date           date not null,
  -- 價量
  close          numeric,
  change_pct     numeric,
  volume         bigint,
  amount         numeric,
  -- RSI
  rsi_14         numeric,
  -- MACD
  macd           numeric,
  macd_signal    numeric,
  -- KDJ
  kdj_k          numeric,
  kdj_d          numeric,
  kdj_j          numeric,
  -- SMA
  sma_5          numeric,
  sma_20         numeric,
  sma_60         numeric,
  -- Bollinger
  bbands_upper   numeric,
  bbands_middle  numeric,
  bbands_lower   numeric,
  primary key (symbol, date)
);

create index if not exists idx_indicator_cache_date
  on indicator_cache(date);

-- ---------------------------------------------------------------------------
-- indicator_cache_runs：每次 cache job 的進度與終態
-- ---------------------------------------------------------------------------

create table if not exists indicator_cache_runs (
  run_date        date primary key,
  is_trading_day  boolean not null,
  started_at      timestamptz default now(),
  finished_at     timestamptz,
  symbols_total   int,
  symbols_done    int default 0,
  status          text not null check (status in ('running','done','failed','skipped')),
  error_text      text
);

create index if not exists idx_cache_runs_status_date
  on indicator_cache_runs(status, run_date desc);

-- ---------------------------------------------------------------------------
-- RLS：兩張表都啟用，anon 可讀（公開股市衍生資料），只有 service_role 可寫
-- ---------------------------------------------------------------------------

alter table indicator_cache enable row level security;
alter table indicator_cache_runs enable row level security;

create policy "anon can read indicator_cache"
  on indicator_cache for select
  to anon, authenticated
  using (true);

create policy "anon can read indicator_cache_runs"
  on indicator_cache_runs for select
  to anon, authenticated
  using (true);
-- 沒寫 INSERT/UPDATE/DELETE policy → 只有 service_role bypass RLS 能寫
