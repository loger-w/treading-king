-- symbols 主表 — 其他所有表的 FK 來源。

create extension if not exists pgcrypto;

create table if not exists symbols (
  symbol text primary key,
  name text not null,
  market text not null check (market in ('TWSE', 'OTC')),
  is_etf boolean default false,
  is_active boolean default true,
  updated_at timestamptz default now()
);

create index if not exists idx_symbols_market_active
  on symbols(market) where is_active;
create index if not exists idx_symbols_etf_active
  on symbols(is_etf) where is_active;

-- RLS：所有 table 預設啟用，BFF 用 service_role 寫入（bypass RLS），
-- 前端 publishable key 受限於 policy。symbols 是公開股票代碼/名稱，前端可直接讀。
alter table symbols enable row level security;

create policy "anon can read symbols"
  on symbols for select
  to anon, authenticated
  using (true);
-- 沒寫 INSERT/UPDATE/DELETE policy → anon 不能寫，只有 service_role 能
