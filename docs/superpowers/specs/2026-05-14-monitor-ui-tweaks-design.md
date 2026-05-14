# 即時監控 UI 微調 — Design (2026-05-14)

## 範圍

九項使用者反饋的小到中型改動，集中在「即時監控」單一頁面（`frontend/src/pages/Monitor.tsx`）。
不涉及訊號規則、策略、自動交易、Schema 變更。

| # | 反饋 | 類型 |
|---|------|------|
| 1 | 分時走勢 CDP label 只顯示價位 | UI tweak |
| 2 | 均線（VWAP）加價位 label | UI tweak |
| 3 | 自選清單加目前股價 + 漲幅 | 新資料流 |
| 4 | 觸發歷史每 row 字加大 + 加漲幅 | UI tweak + 新資料流 |
| 5 | 觸發歷史 / 自選 / 明細欄高不超過畫面 | Layout |
| 6 | 委買賣內 / 外% 全砍（含 API 欄位） | 移除 |
| 7 | 明細加張數欄 | 新資料流（WS payload 擴欄） |
| 8 | （Q）搜尋股票走 API 還是 supabase？ | 純問答 |
| 9 | 分時走勢的數字字級加大 | UI tweak |

Q8 回答：搜尋走 supabase 的 `symbols` 表（`/api/symbols`）。該表事先由 `POST /api/symbols/refresh` 從 TWSE + OTC 公開 OpenAPI upsert 進去，**不打富邦、不打外網**。後續不再贅述。

---

## 設計決策（已敲）

- **自選清單股價來源**：WS tick 即時 + 一次性 `prev_close`。新後端 endpoint `POST /api/quotes/snapshot` 一次回多檔的 `{prev_close, last_price}`，自選變動時打一次；後續 price 由 WS tick stream 即時推（hook 已有 `subscribeTicks` module-level bus）。
- **觸發歷史漲幅基準**：觸發當下的漲幅 = `(trigger_price - prev_close) / prev_close`。`prev_close` 共用 A1 的 snapshot endpoint，TriggerList 用過的 symbol 第一次出現時湊批拉一次。
- **內外% API 砍法**：後端 `/api/quote/{symbol}` 改成挑欄位 return，移除 `total` 整個物件（不是只前端不讀）。
- **分時字級**：`text-[10px]` → `text-[12px]`，含 Y/X 軸、CDP、今日高低、hover crosshair label。

---

## 後端變動

### A1. 新 endpoint `POST /api/quotes/snapshot`

- **路徑**：`backend/routes/quote.py`（既有檔案加新 handler）
- **Request body**：`{symbols: string[]}`（≤ 50 檔）
- **Response**：`{quotes: Array<{symbol, prev_close, last_price}>}`（API 邊界用 snake_case，與既有 `IntradayCandlesResponse.prev_close` 一致）
  - `prev_close`：富邦 `intraday_quote().previousClose`（命名以 SDK 為準，落地時驗）
  - `last_price`：富邦 `intraday_quote().lastPrice`（同上）
  - 缺資料 / 富邦失敗的 symbol：該 row 兩欄都為 `null`，仍包含在陣列裡
- **前端 type**：`frontend/src/lib/api.ts` 加 `SnapshotResponse` + `api.quotesSnapshot(symbols)`；hook 內把 `prev_close → prevClose` 等轉成 camelCase（與既有 hook 慣例一致）
- **內部實作**：
  - `asyncio.gather` 並發打富邦 `intraday_quote`，每個 call 走 `get_rate_limiter().acquire`（與 `fubon_client.intraday_quote` 一致）
  - 單一 symbol 失敗用 `return_exceptions=True` 包，不影響其他 row
  - symbol 格式驗證走既有 `SYMBOL_RE`
- **權限**：吃既有 `X-API-Key` middleware（與其他 `/api/*` 一致）

### A2. 改 `GET /api/quote/{symbol}` — 刪除 `total` 欄位

- **檔案**：`backend/routes/quote.py:18-39`
- 改 pass-through 為挑欄位 return：
  ```python
  result = await fubon.intraday_quote(symbol)
  return {"bids": result.get("bids", []), "asks": result.get("asks", [])}
  ```
- 前端 `lib/api.ts` 的 `QuoteResponse` 介面同步移除 `total` 整個欄位
- 前端 `useQuoteBook` 砍掉 `innerVolume / outerVolume` 兩個 state 及其 set

### A3. WS tick broadcast payload 加 `size`

- **檔案**：`backend/services/fubon_ws.py:217`
- 現況：`{event: "tick", data: {symbol, price}}`
- 改成：`{event: "tick", data: {symbol, price, size}}`
- 內部 `Tick` dataclass 已含 `size`（line 204），直接讀
- 前端 `useSignalsStream.ts` 的 `TickEvent` interface 加 `size: number`

---

## 前端 Hook 變動

### B1. 新 hook `useWatchlistQuotes(symbols)`

- **檔案**：`frontend/src/hooks/useWatchlistQuotes.ts`（新檔）
- 行為：
  - mount + `symbols` 變動時 → 打 `POST /api/quotes/snapshot` 拿 `prev_close + last_price` map
  - 訂閱 module-level `tickBus`（從 `useSignalsStream.ts` 既有的 `subscribeTicks` 取）累積每檔最新 price
  - 回傳 `Record<symbol, {price: number | null, prevClose: number | null, changePct: number | null}>`
- 缺資料時三欄都為 `null`（UI 顯示 `—`）

### B2. 改 `useTradeTape` — 加 size

- **檔案**：`frontend/src/hooks/useTradeTape.ts`
- `TradeRow` 加欄位 `size: number`
- subscribe handler 從 `t.size` 取
- WS payload 沒帶 size（舊版本相容）時預設 `0`

### B3. 新 hook `useSnapshotCache(symbols)`

- **檔案**：`frontend/src/hooks/useSnapshotCache.ts`（新檔）
- 對 `symbols` 集合的差集（沒見過的）湊批打 `/api/quotes/snapshot`
- 快取 `Record<symbol, {prevClose, lastPrice}>`，過期策略：當日有效
- 給 TriggerList 用（B1 已經對 watchlist 抓過，可共用快取避免重複請求 — 用 module-level Map 實作）

> **取捨備註**：B1 跟 B3 都打同一個 endpoint。實作上 B1 用 module-level cache，B3 透過同一個 cache 拿資料；watchlist symbols 抓回來後 TriggerList 命中 watchlist 的 row 不會再打 API。Non-watchlist 觸發紀錄才另外湊一批。

---

## 前端 UI 變動

### C1. Monitor 鎖視窗高度（# 5）

- **檔案**：`frontend/src/pages/Monitor.tsx`
- 改 `<main>` 結構：
  - `<main className="h-screen flex flex-col overflow-hidden">`
  - 內層 `mx-auto max-w-[1960px]` 容器加 `flex-1 min-h-0`
  - 既有 grid 加 `h-full`
- TopToolbar 不在 `<main>` 內，獨立佔上方
- 三欄內側 `flex-1 min-h-0 overflow-y-auto` 已就位（line 138, 160, 207），不動

### C2. WatchlistWithChips 加股價 + 漲幅（# 3）

- **檔案**：`frontend/src/components/WatchlistWithChips.tsx`
- 新 prop `quotes: Record<symbol, {price, prevClose, changePct}>`（Monitor 從 `useWatchlistQuotes` 傳入）
- 排版（line 80-91 區塊重做）：
  ```
  ┌─ 2330            ─── 605.00 ▲ +1.85% ─┐
  │  台積電                              │
  │  [chip] [chip] [chip]                │
  └──────────────────────────────────────┘
  ```
- 漲跌色：`changePct > 0` → `text-bull`、`< 0` → `text-bear`、`= 0` → `text-ink-muted`、`null` → `text-ink-dim` + `—`
- 排序維持原規則（hit desc）

### C3. TriggerList 字加大 + 加漲幅（# 4）

- **檔案**：`frontend/src/components/TriggerList.tsx`
- 字級調整：
  | 元素 | 原 | 新 |
  |------|----|----|
  | symbol | `text-base` | `text-lg` |
  | name | `text-xs` | `text-sm` |
  | rule name | `text-xs` | `text-sm` |
  | time | `text-xs` | `text-sm` |
  | price | `text-sm` | `text-base` |
- 加漲幅：第 2 行右側，price 旁加一個小字 chip（`text-xs`），格式 `+1.85%` / `-0.42%`，紅綠色按正負
- prev_close 缺資料 → 顯示 `—`，不擋整 row
- 新 prop `prevCloseMap: Record<symbol, number | null>`（Monitor 從 `useSnapshotCache` 傳入：餵入 TriggerList 出現過的 symbol 集合）

### C4. TradeTape 加張數欄（# 7）

- **檔案**：`frontend/src/components/TradeTape.tsx`
- Grid 4 欄：`grid-cols-[64px_1fr_36px_44px]`（時間 / 價 / 向 / 張）
- header 加「張」column（同樣 `text-2xs uppercase`）
- row 加 `<span className="text-right tabular-nums text-ink-muted">{r.size.toLocaleString()}</span>`

### C5. IntradayChart 三件事（# 1, # 2, # 9）

- **檔案**：`frontend/src/components/IntradayChart.tsx`

**C5a. CDP label 只顯示價位（# 1）**
- 第 243 行：`{k.toUpperCase()} {formatTickPrice(cdp[k])}` → `{formatTickPrice(cdp[k])}`

**C5b. VWAP 加價位 label（# 2）**
- 在 `polyVwap` 渲染區塊（line 250-254）後面加：
  - 取最後一根 candle 的 `average`
  - 在 `(scaleX(lastIdx), scaleY(lastAvg))` 右側畫 `text`（`textAnchor="start"`、x 偏移 +4），色 `fill-ink-dim`
  - 內容：`formatTickPrice(lastAvg)`
- 只在 `showVwap && candles.length > 0` 時畫

**C5c. 字級加大（# 9）**
- 全部 `text-[10px]` → `text-[12px]`：line 224 (Y 軸 baseline 字)、line 242 (CDP)、line 282 (today high)、line 296 (today low)、line 313 (X 軸時間)、line 343 (hover Y label)、line 351 (hover X label)
- VWAP label 同 `text-[12px]`

### C6. QuoteBook 砍內外盤（# 6）

- **檔案**：`frontend/src/components/QuoteBook.tsx`
- 刪除：
  - 解構 `innerVolume, outerVolume`（line 18）
  - `sumIO / innerPct / outerPct` 計算（line 33-34）
  - 內 / 外% bar + 量字整塊（line 47-60）
- 保留：委買 / 委賣總量 + 五檔 + 量條（line 62-106）

---

## 影響面 / 變更摘要

```
新檔
  backend            無
  frontend/src/hooks/useWatchlistQuotes.ts
  frontend/src/hooks/useSnapshotCache.ts
  docs/superpowers/specs/2026-05-14-monitor-ui-tweaks-design.md (this)

改檔
  backend/routes/quote.py             A1, A2
  backend/services/fubon_ws.py        A3 (broadcast +size)
  frontend/src/lib/api.ts             QuoteResponse 砍 total, snapshot endpoint
  frontend/src/hooks/useSignalsStream.ts  TickEvent +size
  frontend/src/hooks/useTradeTape.ts  TradeRow +size
  frontend/src/hooks/useQuoteBook.ts  砍 inner/outer state
  frontend/src/pages/Monitor.tsx      C1 height, 串新 hook 給 watchlist & trigger
  frontend/src/components/WatchlistWithChips.tsx  C2
  frontend/src/components/TriggerList.tsx         C3
  frontend/src/components/TradeTape.tsx           C4
  frontend/src/components/IntradayChart.tsx       C5
  frontend/src/components/QuoteBook.tsx           C6
```

---

## 邊角 / 風險

1. **`intraday_quote()` 在盤後 / 假日的行為**：富邦 SDK 在收盤後仍會回最後交易日的 quote，`previousClose` 仍可用。停牌或新上市股有可能缺 `previousClose`，A1 用 `null` 回覆，UI 顯示 `—`。
2. **A1 並發 N 個 quote call**：自選 10 檔同時打富邦，`rate_limiter` 已串好；自選變動才打，不是 polling。最壞情況自選 50 檔首次載入 ~1-2 秒延遲，可接受。
3. **A3 broadcast payload 體積**：每筆 tick 多一個 int 字段（~8 bytes），可忽略。
4. **`h-screen` vs `100dvh`**：桌面為主，`h-screen` 就好。手機暫不在範圍。
5. **C5b VWAP label 與 hover crosshair label 可能重疊**：VWAP label 在右側固定位置，hover 在游標附近；重疊機率低，先實作再看是否需要 z-order 處理。
6. **TriggerList 的 prev_close 命中率**：當日觸發紀錄 N 個不同 symbol；watchlist 命中外的部分湊批一次，可接受。

---

## 不在範圍

- 訊號規則 / 策略 / 自動交易任何改動
- Schema / migration 變更
- 任何效能優化（不在 9 項裡）
- TopToolbar / SignalRulesDialog 字級或排版（不在 9 項裡）
