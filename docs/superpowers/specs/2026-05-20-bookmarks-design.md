# 書籤功能 + 搜尋名稱 bug 修正

**Date**: 2026-05-20
**Status**: 設計確定、待實作

## Summary

把 trading-king 目前單一「自選清單」改造為書籤群組架構,同時修一個搜尋顯示的 bug、並透過富邦 API 建立一個「大漲股」系統書籤。共 4 項改動:

1. **修 bug** — 搜尋非自選股時、結果只顯示代號不顯示名稱
2. **書籤群組化** — 同一檔股票可放多個書籤、有「全部」聚合 view
3. **管理書籤** — 新增 / 改名 / 刪 / 拖拉排序、批次跨書籤搬股票、編輯模式 inline 加股票
4. **大漲股系統書籤** — 富邦 `snapshot.movers` API、盤中每 1 分鐘自動更新、所有 user 共用

## Goals

- 修掉 `symbols.name` 缺漏導致搜尋 dropdown 空白的 bug
- 改造 watchlist 為多書籤架構、平滑升級(既有 user 開頁面就看到「自選」書籤 + 原本股票)
- 同一檔股票可在多個書籤(電子 + 半導體 重疊很正常)
- 「全部」聚合 view 一眼看完所有書籤
- 大漲股每分鐘自動更新、所有 user 看到一致結果

## Non-goals

- 不刪除大型權值股(例如台積電)— user 後來決定保留
- 不重做 Editorial Dark 風格 — 沿用既有 design tokens
- 不引入 react-query / zustand — 保持既有 React useState pattern
- 不一次清除舊 `watchlist` 表 — 下個 migration 再做

## Architecture

```
backend/
├── routes/
│   ├── bookmarks.py                       [新] 書籤 CRUD + items + 大漲股
│   ├── symbols.py                         [改] refresh 改用 ISIN 表(bug 修正)
│   ├── watchlist.py                       [改] 內部 query 改打新 schema(API 保留)
│   ├── active_signals.py                  [改] _scope_symbols 改撈「自選」書籤
│   └── ...
├── jobs/
│   └── top_gainers_scheduler.py           [新] 大漲股 1 分鐘排程
├── services/
│   └── fubon_ws.py                        [不動] refcount per-owner 已支援多書籤
└── main.py                                [改] 註冊新 router + 排程

frontend/src/
├── components/
│   ├── BookmarksPanel.tsx                 [新] 取代 WatchlistWithChips
│   ├── BookmarkSidebar.tsx                [新] 110px sidebar
│   ├── BookmarkList.tsx                   [新] 主清單(含「全部」mode)
│   ├── BookmarkEditMode.tsx               [新] 編輯模式 + inline 搜尋區
│   ├── BookmarkManageDialog.tsx           [新] 「管理書籤」modal
│   ├── BookmarkNewDialog.tsx              [新] 「新增書籤」modal
│   ├── AddToBookmarksDialog.tsx           [新] 「加股票到書籤」modal
│   ├── MoveCopyDialog.tsx                 [新] 編輯模式「移動 / 複製」
│   ├── SymbolSearch.tsx                   [改] name fallback
│   ├── IntradayChart.tsx                  [改] 「+ 加入自選」改「+ 加入書籤」
│   └── WatchlistWithChips.tsx             [內化或廢棄]
├── hooks/
│   ├── useBookmarks.ts                    [新] 群組 CRUD + selected group
│   ├── useBookmarkItems.ts                [新] 單一/全部書籤股票
│   └── useWatchlist.ts                    [改] 內部改打新 API(薄包裝)
├── lib/api.ts                             [改] 加 bookmark types
└── pages/Monitor.tsx                      [改] 第二欄 swap

supabase/migrations/
└── 0007_bookmarks.sql                     [新] 3 張新表 + backfill
```

---

## Item 1 · Bug 修正:搜尋名稱顯示

**根因**:
1. `backend/routes/symbols.py:78-96` `refresh_symbols` 從 TWSE OpenAPI `STOCK_DAY_ALL` 抓資料,該 endpoint 只回**當日有交易**的股票。停牌、低流動性、新上市股票會缺漏 → `symbols.name` 為 NULL。
2. `frontend/src/components/SymbolSearch.tsx:47` 直接渲染 `{r.name}` 無 fallback。

**Backend** (`backend/routes/symbols.py`):
- 改用 ISIN 表(`https://isin.twse.com.tw/isin/C_public.jsp?strMode=2` 上市 與 `strMode=4` 上櫃)作為**主要來源**,STOCK_DAY_ALL 為補充。
- ISIN 表是 HTML table(big5 編碼),要 parse;但範圍是「全部上市/上櫃股票」、不限當日有交易。
- 解析步驟:
  1. `httpx.get(url)` 拿 raw bytes
  2. `.decode('big5', errors='replace')` 解碼
  3. `BeautifulSoup` parse table、欄位是 `代號 名稱 / 國際代碼 / 上市日 / ...`
  4. 「代號 名稱」用空格切、第一段是代號、其餘是名稱
- ISIN 解析失敗 fallback 既有的 OpenAPI 邏輯(現有 code 不刪)
- `_is_non_stock` 邏輯保持不變

**Frontend** (`frontend/src/components/SymbolSearch.tsx:47`):

```tsx
<span className="ml-2 text-ink-muted">{r.name || '(無名稱)'}</span>
```

**驗證**:
1. `POST /api/symbols/refresh` 後、`select count(*) from symbols where name is null or trim(name) = ''` = 0
2. 搜尋一個已知停牌股代號,確認 dropdown 顯示中文名稱

---

## Item 2 · Schema 設計

新增 3 張表。RLS 跟既有 `watchlist` 一致(anon 可讀、service_role 可寫;user 隔離靠 backend `eq("user_label", ...)`)。

### `bookmark_groups` (新)

```sql
create table if not exists bookmark_groups (
  id uuid primary key default gen_random_uuid(),
  user_label text,                         -- NULL = 系統書籤(所有 user 共用)
  name text not null,
  sort_order int not null default 0,
  is_system boolean not null default false,
  source_type text,                        -- NULL = manual; 'top_gainers' = 大漲股
  created_at timestamptz default now()
);

create index idx_bookmark_groups_user on bookmark_groups(user_label);

-- 同一 user 下書籤名稱唯一(user_label NULL 不參與唯一性、由 source_type 處理)
create unique index uniq_bookmark_user_name
  on bookmark_groups(user_label, name) where user_label is not null;

-- 系統書籤同一 source_type 只能一個
create unique index uniq_bookmark_system_source
  on bookmark_groups(source_type) where source_type is not null;
```

### `watchlist_items` (新,取代 `watchlist`)

```sql
create table if not exists watchlist_items (
  id uuid primary key default gen_random_uuid(),
  group_id uuid not null references bookmark_groups(id) on delete cascade,
  symbol text not null references symbols(symbol),
  added_at timestamptz default now(),
  note text,
  unique (group_id, symbol)
);

create index idx_watchlist_items_group on watchlist_items(group_id);
create index idx_watchlist_items_symbol on watchlist_items(symbol);
```

### `top_gainers_snapshot` (新)

```sql
create table if not exists top_gainers_snapshot (
  symbol text primary key references symbols(symbol),
  change_pct numeric not null,
  volume_lots int not null,
  market text not null,
  rank int not null,
  captured_at timestamptz not null default now()
);

create index idx_top_gainers_rank on top_gainers_snapshot(rank);
```

排程每分鐘 `delete from top_gainers_snapshot;` + `insert ... values (...);`(一個 transaction 全部刷掉、寫入新一批),避免漸進式 churn。

---

## Item 3 · Migration `0007_bookmarks.sql`

1. 建 `bookmark_groups`、`watchlist_items`、`top_gainers_snapshot` (DDL 如上)
2. **Backfill**: 為每個既有 `user_label` 建立預設書籤「自選」
   ```sql
   insert into bookmark_groups (user_label, name, sort_order, is_system)
   select distinct user_label, '自選', 0, false from watchlist;
   ```
3. **Backfill**: 把舊 `watchlist` 資料搬到 `watchlist_items`
   ```sql
   insert into watchlist_items (group_id, symbol, added_at, note)
   select bg.id, w.symbol, w.added_at, w.note
   from watchlist w
   join bookmark_groups bg
     on bg.user_label = w.user_label and bg.name = '自選';
   ```
4. 建立系統書籤「大漲股」
   ```sql
   insert into bookmark_groups (user_label, name, sort_order, is_system, source_type)
   values (null, '大漲股', 100, true, 'top_gainers');
   ```
5. 舊 `watchlist` 表**先保留**作為「自選」書籤的 backup。下個 migration 再 drop。

---

## Item 4 · Backend APIs

新 router `backend/routes/bookmarks.py`。舊 `backend/routes/watchlist.py` 保留 API 簽名、內部 query 改打新 schema(active_signals scope=watchlist 透過此 alias 繼續運作)。

### 書籤群組 CRUD

```
GET    /api/bookmarks
       回:  { groups: [{ id, name, sort_order, is_system, source_type, count }] }
       含系統書籤、`count` 從 `watchlist_items` 或 `top_gainers_snapshot` 算

POST   /api/bookmarks
       body: { name: string }
       新建使用者書籤、自動 sort_order = max+1

PATCH  /api/bookmarks/{id}
       body: { name?: string, sort_order?: number }
       系統書籤拒絕(回 403)

DELETE /api/bookmarks/{id}
       刪除書籤(連帶刪 watchlist_items)、系統書籤拒絕
```

### 書籤內股票 CRUD

```
GET    /api/bookmarks/{id}/items
       回:  { items: [{ symbol, name, market, is_etf, added_at, note }], count }
       系統書籤(source_type='top_gainers')從 top_gainers_snapshot join 取得

POST   /api/bookmarks/{id}/items
       body: { symbols: string[] }   # 批次加入
       系統書籤拒絕(回 403)、unique violation 略過

DELETE /api/bookmarks/{id}/items/{symbol}
       系統書籤拒絕

PATCH  /api/bookmarks/items/move
       body: { symbols: string[], from_group_id: uuid, to_group_ids: uuid[], op: 'move'|'copy' }
       批次跨書籤搬移 / 複製。to_group_ids 不可含系統書籤。
```

### 大漲股

```
GET    /api/bookmarks/top-gainers
       回:  { items: [{ symbol, name, market, change_pct, volume_lots, captured_at }] }

POST   /api/bookmarks/top-gainers/refresh
       手動觸發 refresh、給除錯與測試用
```

### 舊 `/api/watchlist` 行為調整

- `GET /api/watchlist` 改為「列出 user 的『自選』書籤股票」
- `POST /api/watchlist` 加股票到「自選」書籤
- `DELETE /api/watchlist/{symbol}` 從「自選」書籤移除

`backend/routes/active_signals.py:_scope_symbols` 中 scope=watchlist 改為:

```python
bg = sb.client.table("bookmark_groups").select("id") \
    .eq("user_label", get_user_label()).eq("name", "自選").limit(1).execute()
if not bg.data: return []
items = sb.client.table("watchlist_items").select("symbol") \
    .eq("group_id", bg.data[0]["id"]).execute()
return [it["symbol"] for it in items.data]
```

### WS subscribe 整合

**關鍵設計**:`WSPool.unsubscribe` 的 refcount 是 **set of owner_id**,只有 set 全空才真 unsubscribe(`fubon_ws.py:102-116`)。

因此書籤的 owner_id 一律用 `f"bookmark:{group_id}"` — WSPool 自動處理「同一檔股票在多書籤」的情境,**不需要**額外 query「其他書籤是否還有這檔」。

- **加入**: `subscribe(symbol, f"bookmark:{group_id}")`、backfill CDP、refresh signal_engine
- **移除**: `unsubscribe(symbol, f"bookmark:{group_id}")`、refresh signal_engine
- **舊 `watchlist.py`**: 內部改用「自選」group 的 `f"bookmark:{group_id}"`,既有 owner_id "watchlist" 遷移到新格式

`main.py` startup 訂閱也要改:遍歷該 user 所有書籤(`bookmark_groups` + `watchlist_items`)、分別用 `f"bookmark:{group_id}"` subscribe。

---

## Item 5 · 大漲股排程

新檔 `backend/jobs/top_gainers_scheduler.py`:

```python
async def refresh_top_gainers():
    sdk = get_fubon_sdk()
    results = []

    for market in ("TSE", "OTC"):
        movers = sdk.stock.snapshot.movers(
            market=market, direction="up", change="percent"
        )
        for item in movers.data:
            if item.changePercent <= 4: continue
            if item.tradeVolume <= 3_000_000: continue   # 3000 張 = 3,000,000 股
            if not re.match(r"^\d{4}$", item.symbol): continue  # 非 4 位數
            results.append((item.symbol, item.changePercent, item.tradeVolume // 1000, market))

    # 排除 ETF
    symbols_meta = sb.client.table("symbols") \
        .select("symbol, is_etf").in_("symbol", [r[0] for r in results]).execute()
    etf_set = {s["symbol"] for s in symbols_meta.data if s["is_etf"]}
    filtered = [r for r in results if r[0] not in etf_set]

    # 排序 by change_pct desc, 取前 50
    filtered.sort(key=lambda r: -r[1])
    top50 = filtered[:50]

    # 整批 replace
    sb.client.table("top_gainers_snapshot").delete().neq("symbol", "").execute()
    rows = [
        {"symbol": s, "change_pct": pct, "volume_lots": vol_lots, "market": mkt, "rank": i+1}
        for i, (s, pct, vol_lots, mkt) in enumerate(top50)
    ]
    if rows:
        sb.client.table("top_gainers_snapshot").insert(rows).execute()
```

### 觸發機制
- 排程觸發:`asyncio.create_task` 內 sleep loop(類似 overnight_loop)
- 時間:**9:00 - 13:30**(實際 8:45 - 13:45 也 OK)
- 頻率:每 1 分鐘
- rate limit: 每次 2 個 API call、無壓力(富邦 limit 300/min)

### 試撮處理

recent commit `dc90043` 把 8:30-9:00 試撮期間的 signal 訊號擋掉。大漲股排程也遵守同樣規則 — 「盤中」定義 = 9:00-13:30,8:30-9:00 不跑。reuse 既有「是否在交易時段」 helper(若有)。

### 啟動位置

`backend/main.py` lifespan 註冊(類似 `overnight_task`)。

---

## Item 6 · Frontend 視覺規格

### 書籤 column 改造 (`Monitor.tsx` 第二欄)

- 寬度仍 340px
- 內部 grid 110px sidebar + 1fr main(`border-right` 分隔)
- Sidebar 包含「全部」、所有 user 書籤、系統書籤(☆ 前綴)、底部「+ 新增」 dashed button
- 「全部」 mode:右側清單用 `font-serif italic` section heading 分組、同檔多書籤時只顯示一次、歸入第一個 section
- 單一書籤 mode:沿用既有 `WatchlistWithChips` row 樣式

### 視覺細節

- **Selected 書籤**: sidebar 該項 bg-bg-card、左 3px accent marker、字色變 ink
- **System 書籤**: ☆ accent 前綴、無 hover delete
- **加股票成功**: 列表新項目 0.4s 紅光 fade-out 動畫
- **編輯模式 header**: 「+ 加入股票」accent 紅 link、active 時加 underline; 「✕ 結束編輯」灰
- **Inline 搜尋區**: bg-bg-card、padding 14px、搜尋結果 hover 帶左 2px accent border、已在書籤的結果灰階 + ✓ 取代 +
- **「全部」section heading**: `font-serif italic` 14px ink-muted、橫線右延

### 既有元件調整

- `IntradayChart.tsx`: chart header 的「+ 加入自選」改為「+ 加入書籤」→ 開 `AddToBookmarksDialog`(checkbox 多書籤、已勾的 = 該股票目前已在那些書籤)
- `useWatchlist.ts`: 留著但內部改打新 API(轉成「自選」書籤的薄包裝)
- `Monitor.tsx`: 第二欄的 `<WatchlistWithChips>` 換成 `<BookmarksPanel>`

### 視覺 mockup 參考

設計階段的 HTML mockup 保存於:
- `.superpowers/brainstorm/96-1779290496/content/bookmarks-v1.html` — 整頁 + 細部 + 三個 modal
- `.superpowers/brainstorm/96-1779290496/content/bookmarks-v2.html` — 編輯模式三 state

實作時可 reopen visual companion server 對照看細節。

---

## 實作順序

1. **Migration + Schema** (`0007_bookmarks.sql`、跑在 dev、確認 backfill)
2. **Backend bookmarks router** (CRUD + items endpoints + tests)
3. **Backend 大漲股排程** (寫排程 + mock fubon 測試 + 接 main.py)
4. **Backend bug 修正** (ISIN refresh + 跑 refresh 確認 symbols.name 滿)
5. **Frontend hooks** (`useBookmarks`、`useBookmarkItems`)
6. **Frontend 書籤 panel UI** (sidebar + list + 「全部」mode)
7. **Frontend modals** (新增、加股票、管理、移動複製)
8. **Frontend 編輯模式 + inline 搜尋加股票**
9. **Frontend SymbolSearch name fallback + IntradayChart 按鈕改寫**
10. **E2E 手動驗證**

---

## Testing strategy

| 範圍 | 測試 |
|---|---|
| Bug 修正 | `test_symbols_search_fallback.py` — mock ISIN response + 既有 OpenAPI 失敗 → 確認 fallback path |
| Bookmarks CRUD | `test_bookmarks_crud.py` — 新增/改名/刪、系統書籤拒絕 PATCH/DELETE、批次 move/copy |
| 大漲股排程 | `test_top_gainers_scheduler.py` — mock `sdk.stock.snapshot.movers` 回 fake data,assert 過濾條件 + 排序 + DB upsert |
| WS subscribe refcount | 手動驗:同 user 同股票加入兩書籤、刪一邊不 unsubscribe |
| 既有 active_signals scope=watchlist | 手動驗:scope=watchlist 規則仍以「自選」書籤為範圍 |
| E2E flow | 見「驗證」section 8 步驟 |

### E2E 驗證 (Stage 10)

1. 升級後第一次開頁面 — 自動看到「自選」書籤、原本的股票都在
2. 新增書籤「測試」、加入 2330 — 「全部」 view 看得到、「測試」 view 看得到、「自選」 view 看不到
3. 把 2330 也加進「自選」 — 「全部」 view 仍只顯示一次(不重複)
4. 編輯模式 → 選中 2330 → 點「移動」→ 選「自選」、取消「測試」 → 2330 從「測試」移到「自選」
5. 編輯模式 → 「+ 加入股票」 → 搜尋 3008 → 點 + → 列表 highlight 1 秒、可繼續搜
6. 刪除書籤「測試」 — 連帶刪股票、其他書籤同股票不受影響
7. 系統書籤「大漲股」— 無「✎ 編輯」、無 hover delete、無「+ 加入股票」
8. 重啟 backend、開盤等 1 分鐘、確認「大漲股」內容變動;手動跑 `POST /api/bookmarks/top-gainers/refresh` 即時更新

---

## 設計取捨

| 決策點 | 結論 | 理由 |
|---|---|---|
| 同檔多書籤 | 允許 | 提供未來擴展性(電子 + 半導體 重疊很正常) |
| 既有 watchlist | 變預設書籤「自選」 | 平滑升級 |
| 大漲股可見性 | 系統書籤、所有 user 共用 | 1 份算就好、資料一致 |
| 權值股排除 | **不排除** | user 決定保留(原本想排,後來改主意) |
| Refresh 頻率 | 盤中每 1 分鐘 | rate limit 無壓力 |
| 排序 | 漲幅高到低 | 符合「大漲股」語意 |
| 成交量門檻 | 3000 張 | user 指定 |
| UI layout | 內嵌 sidebar 110+220px | 既有 340px column 內部分割、最小布局變動 |
| 大漲股 row 顯示 | 同其他書籤(代號名稱價格漲%) | 一致體驗 |
| 新增/加股票/管理 | 全部走 modal | 體驗統一 |
| 編輯模式加股票 | inline 搜尋區、可連續加 | 不打斷批量整理 flow |
| 舊 `/api/watchlist` | 保留為「自選」書籤 alias | 避免一次改動太大 |
| 大漲股獨立 table | `top_gainers_snapshot` 不塞 `watchlist_items` | churn 高、結構不同 |
| WS owner_id | `f"bookmark:{group_id}"` | refcount 由 pool 處理、不需自行 query |

---

## Open questions / risks

- **ISIN 表解析失敗**:HTML 結構可能變動,要寫 fallback(回 OpenAPI 既有邏輯)
- **同一股票多書籤的 unsubscribe**:`fubon_ws.py:102-116` 已驗證 set-based refcount、設計正確
- **大漲股排程啟動時機**:lifespan 啟動;若 backend 凌晨啟動、排程要等到 9:00 才動,期間 `top_gainers_snapshot` 為空、UI 顯示 empty state
