-- Phase 1 — symbols 主表
-- Plan §Phase 1 對應。symbols 是其他所有表的 FK 來源。

create extension if not exists pgcrypto;

create table if not exists symbols (
  symbol text primary key,
  name text not null,
  market text not null check (market in ('TWSE', 'OTC')),
  industry text,
  is_etf boolean default false,
  is_active boolean default true,
  updated_at timestamptz default now()
);

create index if not exists idx_symbols_market_active
  on symbols(market) where is_active;
create index if not exists idx_symbols_etf_active
  on symbols(is_etf) where is_active;
