-- 2026-05-26 — 監聽清單 + 訊號 Discord 通知
-- 設計見 docs/superpowers/specs/2026-05-26-monitor-list-and-discord-notify-design.md

-- ---------------------------------------------------------------------------
-- monitor_list:訊號評估的全域監聽 universe(per user_label)
-- ---------------------------------------------------------------------------
create table if not exists monitor_list (
  user_label text not null,
  symbol     text not null references symbols(symbol),
  added_at   timestamptz default now(),
  primary key (user_label, symbol)
);

create index if not exists idx_monitor_list_label on monitor_list(user_label);

alter table monitor_list enable row level security;

create policy "anon can read monitor_list"
  on monitor_list for select
  to anon, authenticated
  using (true);

-- ---------------------------------------------------------------------------
-- active_signals:per-rule Discord 通知開關
-- ---------------------------------------------------------------------------
alter table active_signals
  add column if not exists notify_discord boolean not null default true;

-- ---------------------------------------------------------------------------
-- Backfill 1:每個 user 的「自選」書籤股票 → monitor_list
--   (舊 scope=watchlist 等於這份,migrate 後語意不變)
-- ---------------------------------------------------------------------------
insert into monitor_list (user_label, symbol)
select distinct bg.user_label, wi.symbol
from watchlist_items wi
join bookmark_groups bg on bg.id = wi.group_id
where bg.user_label is not null
  and bg.name = '自選'
on conflict do nothing;

-- ---------------------------------------------------------------------------
-- Backfill 2:舊 active_signals.scope.symbols (scope=symbols) → monitor_list
--   exists 子查詢確保被加入的 symbol 還在 symbols 表(避免 FK 撞 delisted)
-- ---------------------------------------------------------------------------
insert into monitor_list (user_label, symbol)
select distinct a.user_label, sym.symbol
from active_signals a,
     lateral jsonb_array_elements_text(a.scope->'symbols') as sym(symbol)
where a.scope->>'type' = 'symbols'
  and jsonb_typeof(a.scope->'symbols') = 'array'
  and exists (select 1 from symbols s where s.symbol = sym.symbol)
on conflict do nothing;
