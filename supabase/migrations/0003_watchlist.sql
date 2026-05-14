-- watchlist：自選清單 = 即時監控池(active_signals.scope 會用)

create table if not exists watchlist (
  symbol      text primary key references symbols(symbol),
  added_at    timestamptz default now(),
  note        text
);

-- RLS：anon 可讀(個人自用,不嚴格區隔讀寫),service_role 才能寫
alter table watchlist enable row level security;

create policy "anon can read watchlist"
  on watchlist for select
  to anon, authenticated
  using (true);
