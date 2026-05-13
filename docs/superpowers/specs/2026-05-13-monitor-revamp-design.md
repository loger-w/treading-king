# Monitor 頁改版 v12 — Layout + Trade Tape + Quote Book + Search Flow

**Date:** 2026-05-13
**Branch:** main (起點 commit `7419886`)
**Mockup:** `docs/superpowers/specs/mockups/monitor-v12.html`

## 1. 目標

把即時監控頁 (Monitor) 重新編排成更接近專業看盤介面的 4 欄 layout，並補上**明細 (Trades Tape)** 與**五檔 (Quote Book)** 兩個 panel。同時調整搜尋流程：搜尋不再直接加自選，改為先在分時走勢預覽、由使用者按鈕加入。

## 2. 範圍與不做

**做：**
- 4 欄等高 layout（觸發歷史 / 自選 / 分時走勢+五檔 / 明細）
- Toolbar 重組（含搜尋框，欄位對齊下方 main grid）
- 分時走勢加大（460 → 520px）
- 訊號規則編輯器：欄位 `close` 的 label 從「收盤價」改為「即時價」+ 加 hint
- 搜尋流程改造（select-then-add）
- 新元件：TradeTape、QuoteBook

**不做（明確排除）：**
- DSL 擴充「相對指標 ±%」(原需求 6)：使用者明確指示先不實作。`Condition` schema 不動。
- 五檔走 WebSocket `books` channel（路線 B）：本次用 **REST poll**（路線 A）。等之後 tick 量上升再升級。
- 改 `close` 的 DSL key（key 仍叫 `close`，只改前端 label）。

## 3. Layout 規格

### 3.1 整體框

```
max-w: 1960px ; px: 36px ; align-items: stretch
grid-template-columns: 300px 340px 1fr 300px ; gap: 24px
```

斷點：
- `≥ 1500px`：上述 4 欄
- `1200–1499px`：欄位寬縮為 `260px 300px 1fr 260px`、gap 16
- `< 1200px`：折成 2 欄、中央列 `grid-column: 1 / -1`，scroll-panel max-height 480px

### 3.2 Toolbar (對齊 main grid)

```
grid-template-columns: 300px 340px 1fr 300px ; gap: 24
  col 1-2: ● 連線中 (justify-self: start)
  col 3:   ⌕ 搜尋框 (撐滿)
  col 4:   ⚙ 訊號規則按鈕 (justify-self: end)
```

搜尋框：邊框 `line-strong`，focus 轉 `accent`；附 `/` 鍵盤捷徑 hint（按 `/` 聚焦輸入）。

### 3.3 4 個 column 內容

| 欄 | 寬 | 內容 | scroll 行為 |
|---|---|---|---|
| 1. 觸發歷史 | 300 | 每 row：股票/時間/規則/觸發價 | inner scroll |
| 2. 自選清單 | 340 | 每 row：股票/命中 chip/即時價/% | inner scroll |
| 3. 分時走勢 + 五檔 | 1fr | chart-frame (520h) → gap 24 → book-frame | 不滾動（自然高） |
| 4. 明細 | 300 | 每 row：時間/價/量/內外盤 | inner scroll |

**等高機制：** `.grid-4 { align-items: stretch }`，中央欄高度（chart + book + gap）驅動其他 3 欄高。其他欄內部 `display: flex; flex-direction: column`，header `flex-shrink: 0`，scroll-panel `flex: 1; min-height: 0; overflow-y: auto`。

## 4. 元件改動

### 4.1 `frontend/src/pages/Monitor.tsx`（大改）

從 grid-2 + 下方全寬 → 改為 grid-4 stretch + 中央欄上下堆疊。

- 移除：自選 column 內的 `SymbolSearch` 區塊
- 移除：底下全寬的 `TriggerHistoryTable`（拆成 column 1 的單欄式 TriggerList）
- 新增：第 3 欄下方插入 `<QuoteBook symbol={selected} />`
- 新增：第 4 欄插入 `<TradeTape symbol={selected} />`
- Selected 預設值不變（第一檔自選股）

### 4.2 `TopToolbar.tsx`（中改）

**內嵌** `SymbolSearch` 到 toolbar 第 3 格（不透過 prop 傳 input value 進來）。TopToolbar 新增 prop `onPickSymbol: (s: string) => void` 一路 forward 給 `SymbolSearch`。改 layout 為 grid 4-col 對齊 main grid。

加 keyboard handler：`/` 鍵聚焦輸入（global window listener，input 已聚焦或 modifier key 按下則略過）。

### 4.3 `SymbolSearch.tsx`（語意改）

`onPick: (symbol) => void` 由「加自選」改為「**setSelected(symbol)**」。Monitor.tsx 接住後驅動 chart 切換。**移除自動 add 行為**。

### 4.4 `IntradayChart.tsx`（小改）

- 高度 460 → **520px**
- Header 右上加按鈕：
  - 若 `symbol ∈ watchlist`：disabled 樣式，文字「已在自選 ✓」
  - 若 `symbol ∉ watchlist`：accent outline，點擊呼叫 `add(symbol)`
- Props 新增 `onAddToWatchlist`、`inWatchlist: boolean`

### 4.5 `WatchlistWithChips.tsx`（小改）

- 移除元件外部依賴 `SymbolSearch`（Monitor.tsx 不再傳）
- 右側價格 + % 簡化顯示（價在上、% 在下，灰 dim）
- chip 顯示策略不變

### 4.6 `TriggerHistoryTable.tsx`（拆）

原本 4-col grid table 不適合 300px 窄欄。改寫成新元件 `TriggerList`（單欄、每 row 兩行：股票+時間 / 規則+觸發價）。動態合併 `historical` + `recent`，按時間倒序。

### 4.7 `ActiveSignalEditor.tsx`（最小改）

```diff
 const FIELD_LABEL: Record<ConditionField, string> = {
-  close: "收盤價",
+  close: "即時價",
   change_pct: "漲跌幅 %", ...
 };
```

跨指標條件區塊上方新增一行 `<p class="text-2xs text-ink-dim mt-2">即時價 = 最新一筆成交價；盤後 / 未開盤時為前一日收盤。</p>`。

DSL key 與後端 `signal_engine._eval_filter_cond` 邏輯不動（已用 `tick.price`，正確）。

### 4.8 新元件：`TradeTape.tsx`

職責：訂閱「目前 selected symbol」的最近 N 筆成交。

- Props：`symbol: string | null`
- 資料來源：**新 hook `useTradeTape(symbol)`** 自己開一個 WS 監聽（與 `useSignalsStream` 共用同一個 `/ws` connection，透過已有的 broadcaster `event: tick` 訊息）。**不**改 `useSignalsStream` 的 signature（保留 `onTick` 不變，TradeTape 走獨立 listener，避免兩個 hook 競爭同個 callback）
- 顯示：時間 / 價 / 單量 / 內外盤
- 內外盤判定：本期**簡化** — 跟前一筆價比，比前一筆高 = 外（買方主動，紅）、低 = 內（賣方主動，綠）、平 = 上次方向
- 上限：最近 50 筆，超過捨棄最舊
- 切換 symbol 時清空

### 4.9 新元件：`QuoteBook.tsx`

職責：顯示 selected symbol 的委買賣五檔。

- Props：`symbol: string | null`
- 資料來源：**REST `GET /api/quote/{symbol}`**（已存在，route 38 行 `quote.py`），每 **2 秒 poll** 一次
- 顯示：左邊 5 檔買、右邊 5 檔賣，每 row：價 + 量 + 量條（widthproportional max qty）
- 切換 symbol 時立即 fetch + reset timer
- 切換到背景 tab (`document.hidden`) 時暫停 poll
- 顯示「每 2 秒 refresh · HH:MM:SS」（最後成功時間）
- 錯誤：API 失敗顯示 dim「報價暫時無法取得」，不打 retry storm（2s 一次本身就是 retry）

## 5. Backend 改動

**幾乎不動。**

- **No** new endpoint：`GET /api/quote/{symbol}` 已在 `backend/routes/quote.py` 提供
- **No** schema change：DSL 不擴充 ±%
- **No** WS subscribe change：trades channel 維持 200/連線；不加 books channel

唯一可能要動：`fubon_client.intraday_quote(...)` 回傳的 bids/asks 結構需驗證跟 Health 頁的 `Book` 元件一致（已知 Health.tsx 使用過，應沒問題）。實作時跑 `scripts/probe_e2e_signal.py` 旁加一支 probe 確認。

## 6. 資料流圖

```
WS trades ──┬─→ ring_buffer ────→ signal_engine ──→ broadcast(signal)
            ├─→ broadcast(tick) ──┬→ useSignalsStream.onTick → IntradayChart
            │                     └→ useTradeTape (filter by selected) → TradeTape
            └─→ (nothing else)

REST quote (every 2s for selected) ──→ useQuoteBook → QuoteBook
```

## 7. 錯誤處理

- WS 斷線：toolbar ws-pill 變紅，TradeTape / IntradayChart 凍結最後值
- Quote API 失敗：QuoteBook 顯示「報價暫時無法取得」，下次 tick 繼續嘗試
- selected 為 null：IntradayChart / TradeTape / QuoteBook 都顯示 placeholder（"← 從自選 / 搜尋挑一檔"）

## 8. 測試

**手動 UAT（盤中 + 盤後）：**
1. 開盤後，連線中 → 點自選任一檔 → chart / book / tape 都跑
2. 點觸發歷史一列 → 切換 selected → 3 個 panel 同步
3. 搜尋一檔不在自選的（譬如 1101）→ chart 切過去 + 顯示「+ 加入自選」按鈕 → 點按鈕 → 自選清單出現該檔，按鈕變「已在自選 ✓」
4. 按 `/` → 搜尋框聚焦
5. 訊號規則 dialog 開啟 → 跨指標條件 dropdown 確認顯示「即時價」+ hint 文字
6. 視窗縮到 1100px → 折成 2 欄
7. 切換 browser tab 5 分鐘 → 回來看 QuoteBook 沒有 stale 資料 / 沒有 burst request

**回歸：**
- `python -m scripts.probe_e2e_signal` 仍通
- 訊號觸發 → recent 累計 + 觸發歷史欄出現 row + tape 出現對應 tick
- 自選 add/remove 仍正常

**測試不做（範圍外）：** unit test。本專案目前無前端測試框架，沿用 manual UAT 為主。

## 9. 實作階段（建議順序）

1. **Backend probe** — 確認 `/api/quote/{symbol}` 回的 bids/asks shape；加 typed response model
2. **DSL label 改名** — 最小、無相依（5 分鐘）
3. **Search 流程改造** — `SymbolSearch.onPick` 語意改、IntradayChart 加按鈕
4. **Layout 重組** — Monitor.tsx 改 grid-4、TopToolbar 改 grid、移除自選內搜尋
5. **TriggerList 元件** — 取代 TriggerHistoryTable
6. **QuoteBook 元件** + `useQuoteBook` hook（poll）
7. **TradeTape 元件** + `useTradeTape` hook（共用 WS tick）
8. **盤中 UAT** + 寫 docs/decisions 紀錄

每個階段可獨立 commit；4 之前的 1-3 完全不破壞既有 layout，可先 ship。

## 10. 風險

- REST quote API 在 selected symbol 切換頻繁時可能造成 富邦 rate limit 壓力。**緩解：** poll interval 2s + symbol change 立即 fetch 但取消未完成 request（AbortController）
- TradeTape 共用 WS tick stream — selected symbol 切換時 broadcast 的 tick 都會進 hook，要在 hook 內 filter（`if (t.symbol !== selected) return`）以免效能/快取累積
- 4 欄等高在小視窗下會「中央欄太高 → 其他 column 也跟著高 → scroll-panel 很長」，1200px 斷點折 2 欄已 mitigated

## 11. 未來

- 五檔升級為 ws `books` channel（須驗證 trades + books 共連線容量）
- DSL ±% 條件（暫時擱置，等使用者重新提）
- 觸發歷史可考慮加 hover 浮出完整規則 detail
