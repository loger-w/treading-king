-- 2026-05-20 — 書籤群組化:單一 watchlist → 多書籤架構 + 大漲股系統書籤
-- 設計見 docs/superpowers/specs/2026-05-20-bookmarks-design.md
--
-- 同檔股票可在多書籤;「全部」聚合 view 跨書籤展示;系統書籤(user_label NULL)
-- 不可由 user 改動。舊 watchlist 表保留作為「自選」書籤的 backup,下個 migration 再 drop。

-- ---------------------------------------------------------------------------
-- bookmark_groups:書籤群組
-- ---------------------------------------------------------------------------
create table if not exists bookmark_groups (
  id uuid primary key default gen_random_uuid(),
  user_label text,                                -- NULL = 系統書籤(所有 user 共用)
  name text not null,
  sort_order int not null default 0,
  is_system boolean not null default false,
  source_type text,                               -- NULL = manual; 'top_gainers' = 大漲股
  created_at timestamptz default now()
);

create index if not exists idx_bookmark_groups_user on bookmark_groups(user_label);

-- 同一 user 下書籤名稱唯一(user_label NULL 不參與唯一性、由 source_type 處理)
create unique index if not exists uniq_bookmark_user_name
  on bookmark_groups(user_label, name) where user_label is not null;

-- 系統書籤同一 source_type 只能一個
create unique index if not exists uniq_bookmark_system_source
  on bookmark_groups(source_type) where source_type is not null;

alter table bookmark_groups enable row level security;

create policy "anon can read bookmark_groups"
  on bookmark_groups for select
  to anon, authenticated
  using (true);

-- ---------------------------------------------------------------------------
-- watchlist_items:書籤內股票
-- ---------------------------------------------------------------------------
create table if not exists watchlist_items (
  id uuid primary key default gen_random_uuid(),
  group_id uuid not null references bookmark_groups(id) on delete cascade,
  symbol text not null references symbols(symbol),
  added_at timestamptz default now(),
  note text,
  unique (group_id, symbol)
);

create index if not exists idx_watchlist_items_group on watchlist_items(group_id);
create index if not exists idx_watchlist_items_symbol on watchlist_items(symbol);

alter table watchlist_items enable row level security;

create policy "anon can read watchlist_items"
  on watchlist_items for select
  to anon, authenticated
  using (true);

-- ---------------------------------------------------------------------------
-- top_gainers_snapshot:大漲股動態資料(每分鐘整批 replace)
-- ---------------------------------------------------------------------------
create table if not exists top_gainers_snapshot (
  symbol text primary key references symbols(symbol),
  change_pct numeric not null,
  volume_lots int not null,
  market text not null,
  rank int not null,
  captured_at timestamptz not null default now()
);

create index if not exists idx_top_gainers_rank on top_gainers_snapshot(rank);

alter table top_gainers_snapshot enable row level security;

create policy "anon can read top_gainers_snapshot"
  on top_gainers_snapshot for select
  to anon, authenticated
  using (true);

-- ---------------------------------------------------------------------------
-- Backfill 1:為每個既有 user_label 建立預設書籤「自選」
-- ---------------------------------------------------------------------------
insert into bookmark_groups (user_label, name, sort_order, is_system)
select distinct user_label, '自選', 0, false
from watchlist
on conflict do nothing;

-- ---------------------------------------------------------------------------
-- Backfill 2:把舊 watchlist 資料搬到 watchlist_items
-- ---------------------------------------------------------------------------
insert into watchlist_items (group_id, symbol, added_at, note)
select bg.id, w.symbol, w.added_at, w.note
from watchlist w
join bookmark_groups bg
  on bg.user_label = w.user_label and bg.name = '自選'
on conflict do nothing;

-- ---------------------------------------------------------------------------
-- Backfill 3:建立系統書籤「大漲股」(所有 user 共用、由排程更新內容)
-- ---------------------------------------------------------------------------
insert into bookmark_groups (user_label, name, sort_order, is_system, source_type)
values (null, '大漲股', 100, true, 'top_gainers')
on conflict do nothing;
