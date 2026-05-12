# Phase 3.1: Watchlist + Signals 整合設計

> Date: 2026-05-13
> Scope: Standard 整合（兩頁合一 + Scope chip + 修 watchlist 不刷新 evaluator 的 bug + today_counts endpoint）
> Est: ~2 天工程

## 1. 背景

Phase 3（commit `df72ee0` ~ `26a69d9`）已建好 watchlist 跟 active_signals 兩條獨立功能，但目前是兩個分離頁面：

- **Watchlist 頁** (`pages/Watchlist.tsx`)：自選清單 + 分時走勢圖
- **Signals 頁** (`pages/Signals.tsx`)：訊號規則 CRUD + 即時觸發流

問題：
- 兩頁互動斷裂 — 使用者看自選時不知道哪檔正被訊號規則監控／哪檔今天命中過
- 訊號流 SignalCard 點 symbol 無法跳回看分時細節
- **Backend bug**：`routes/watchlist.py` POST/DELETE 沒呼叫 `signal_engine.refresh_active_signals()`，新加自選股對 `scope=watchlist` 訊號**不會被 evaluator 評估**（因 `_scope_includes` 用 `symbol in self._field_cache` 判定，field_cache 沒重 fill）

## 2. 目標

整合兩頁成單一「即時監控」頁面，加上以下行為：

1. 自選清單每檔旁邊用 **Scope chip** 顯示「這檔被哪些規則 scope 包含」（命中時 chip 紅邊框 + 上標當日命中次數）
2. **今日有命中的自選股自動置頂** — 強化「事件感」
3. **觸發歷史**顯示全部觸發（不再 filter by selected symbol），點任一筆 row → 分時走勢同步切換到該檔
4. **訊號規則 CRUD** 收到 Dialog（按 toolbar 上「⚙ 訊號規則」按鈕開啟），主畫面更聚焦
5. **修 watchlist add/remove 不刷新 evaluator 的 bug**

## 3. 整體 Layout（依 v11 mockup）

```
┌─────────────────────────────────────────────────────────────┐
│ masthead: treading · king                  日期 · 盤中竞價   │  ← 不動
├─────────────────────────────────────────────────────────────┤
│ nav: [系統狀態] [即時監控]                                    │  ← tab 合併
├─────────────────────────────────────────────────────────────┤
│ toolbar (無 border, transparent):                              │  ← 新增
│   ● 連線中                              [⚙ 訊號規則 (3)]     │
├─────────────────────────────────────────────────────────────┤
│ main grid-2 (480px / 1fr, gap 56px):                          │
│ ┌─────────────────┬─────────────────────────────────────┐  │
│ │ 自選清單 (4)     │  分時走勢                            │  │
│ │                 │  [chart 2330]                       │  │
│ │ search...       │  CDP/VWAP/MA toggles                │  │
│ │ ▌2330           │                                      │  │
│ │  台灣積體電路    │                                      │  │
│ │  [開盤突破³][RSI]│                                      │  │
│ │ ▌6505           │                                      │  │
│ │  台塑石化       │                                      │  │
│ │  [量爆 5 分鐘¹] │                                      │  │
│ │  0050           │                                      │  │
│ │  元大台灣五十   │                                      │  │
│ │  [開盤突破][RSI]│                                      │  │
│ │  3008           │                                      │  │
│ │  大立光電       │                                      │  │
│ │  [chips...]     │                                      │  │
│ └─────────────────┴─────────────────────────────────────┘  │
│ (▌ = 3px accent 紅 marker，命中股票才有，置頂排序)            │
│                                                              │
│ 全寬 (margin-top 56px):                                       │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ 觸發歷史 (8)                                              │ │
│ │ ─────────────────────────────────────────────────────  │ │
│ │ 時間       股票           規則         觸發資訊          │ │
│ │ ─────────────────────────────────────────────────────  │ │
│ │ 09:48:21  2330 台積電    開盤突破    1,085.00 vol 50    │ │
│ │ 09:42:08  2330 台積電    開盤突破    1,082.50 vol 32    │ │
│ │ ... (max-height 480px scroll)                            │ │
│ └─────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

寬度：`max-width 1600px`，padding-x 60px。

字級：base **17px**（從原 15px 提升），對應 chart price 40px / section title 30px / chip 13px / dialog title 28px。

## 4. Components 拆解

### 4.1 新增 frontend components

| Component | 職責 |
|---|---|
| `pages/Monitor.tsx` | 主整合頁，組合 watchlist + chart + history + rules-dialog |
| `components/TopToolbar.tsx` | nav 下方 utility bar：連線狀態 + 訊號規則按鈕 |
| `components/WatchlistWithChips.tsx` | 自選 list，每 row 含 sym/name/chips；命中置頂排序 |
| `components/SignalChip.tsx` | 單個 chip — `規則名 ³` 樣式，hit 時紅邊+紅底+上標 |
| `components/TriggerHistoryTable.tsx` | 全寬 4-col grid 觸發歷史 |
| `components/SignalRulesDialog.tsx` | 規則 CRUD modal（取代原 Signals.tsx §壹 區塊） |

### 4.2 保留複用

| Component | 來源 | 改動 |
|---|---|---|
| `IntradayChart` | 現有 | 無 |
| `ActiveSignalEditor` | 現有 | 從 `SignalRulesDialog` 內部呼叫（nested modal） |
| `SymbolSearch` | 現有 | 在 watchlist 上方 |
| `Sparkline` | 現有 | 不直接用，留給 future |
| `SignalCard` | 現有 | 不再使用，由 `TriggerHistoryTable` 取代視覺 |

### 4.3 新增 frontend hooks

| Hook | 職責 |
|---|---|
| `hooks/useTodayHits.ts` | 管 `Record<symbol, Record<active_signal_id, count>>`。Mount 時打 `GET /api/signals/today_counts` 拿基準；提供 `bump(symbol, signal_id)` 給 WS 累加 |

`selectedSymbol` state 直接在 `Monitor.tsx` 用 `useState<string | null>(null)`，不抽 hook（沒有跨 page 複用需求）。

### 4.4 保留複用 hooks

`useWatchlist` / `useActiveSignals` / `useIntradayCandles` / `useSignalsStream` 全部不動。

## 5. Data Flow

### Monitor.tsx state

```ts
const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);
const { items: watchlist } = useWatchlist();
const { items: rules } = useActiveSignals();
const { counts, bump } = useTodayHits();      // 新 hook
const { status, recent } = useSignalsStream({ onSignal: handleSignal });
const { candles } = useIntradayCandles(selectedSymbol);
```

### Mount 時並行：
- `useWatchlist()` → watchlist rows
- `useActiveSignals()` → rules（給 chip + dialog 用）
- `useTodayHits()` → 打 `GET /api/signals/today_counts` 拿基準計數
- `useSignalsStream({ onSignal })` → 開 WS

### WS onSignal：
```ts
onSignal: (s) => {
  todayHits.bump(s.symbol, s.active_signal_id);
  // SignalCard / TriggerHistoryTable 已透過 useSignalsStream 的 recent state 自動更新
}
```

### Chip 計算（前端 join）：
```ts
for each watchlist symbol w:
  for each active_signal a in activeSignals:
    if a.scope.type === "watchlist" → include
    if a.scope.type === "symbols" && a.scope.symbols.includes(w.symbol) → include
  → 渲染 chips [{ rule_name, count: todayHits[w.symbol][a.id] ?? 0, hit: count > 0 }]
```

### Watchlist 排序：
```ts
sort by:
  1. has-hit (有任一規則 count>0) → 置頂
  2. total today hits desc
  3. added_at desc (原順序)
```

### Selected symbol 連動：
- 點 watchlist row → setSelected(sym) → chart 切換 + 該 row .selected
- 點 history row → setSelected(sym) → chart 切換 + 該 row 對應 watchlist .selected + scroll 回 chart panel
- 預設 selected：watchlist 第一檔（命中置頂後通常是命中最多那檔）

## 6. Backend Changes

### 6.1 修 bug: `routes/watchlist.py`

POST 結尾（在 `ws_pool.subscribe()` 之後、`return` 之前）：
```python
# 通知 signal_engine 重 fill field_cache，新自選股才會被 scope=watchlist 訊號評估
try:
    await get_signal_engine().refresh_active_signals()
except Exception as e:
    logger.warning("watchlist add: refresh_active_signals failed: %s", e)
```

DELETE 同樣加在 `ws_pool.unsubscribe()` 之後。

### 6.2 新 endpoint: `GET /api/signals/today_counts`

加到 `routes/signals_history.py`：

```python
@router.get("/api/signals/today_counts")
async def today_counts() -> dict:
    sb = _ensure_supabase()
    # Asia/Taipei tz 今天 00:00:00
    tz_tw = ZoneInfo("Asia/Taipei")
    today_start = datetime.now(tz_tw).replace(hour=0, minute=0, second=0, microsecond=0)

    res = await asyncio.to_thread(
        lambda: sb.client.table("signals_log")
        .select("symbol, active_signal_id")
        .gte("triggered_at", today_start.isoformat())
        .execute()
    )
    rows = res.data or []
    # 前端 group 比較簡單 — backend 直接回 raw rows
    return {
        "as_of": datetime.now(tz_tw).isoformat(),
        "today_start": today_start.isoformat(),
        "counts": rows,  # [{symbol, active_signal_id}, ...]
    }
```

前端在 `useTodayHits` 內 group by (symbol, active_signal_id) → count。

> 不在 backend 做 GROUP BY 是因為 PostgREST/supabase-py 不直接支援 aggregate；要嘛用 RPC，要嘛前端 group。觸發頻率不高（cooldown ≥ 1800s × N 規則 × N 自選），row 量小，前端 group 沒問題。

### 6.3 不動

- `services/signal_engine.py` — `_scope_includes` 跟 `_refill_field_cache` 邏輯不動
- `routes/active_signals.py` — 已正確呼叫 `refresh_active_signals`
- `services/fubon_ws.py` / `models/condition.py` / migrations — 都不動
- `routes/signals_history.py` 既有 endpoints 不動

## 7. UI 視覺規格（依 v11）

### 7.1 Theme 沿用

完全沿用 Editorial Dark：`bg #14110c` / `accent #e85a4f` / `Inter Tight + Source Serif 4` / paper grain + warm radial。

### 7.2 Toolbar

```css
.toolbar-bar {
  background: transparent;
  border: none;  /* 無 border */
}
.toolbar-bar .page-inner {
  padding-top: 26px; padding-bottom: 10px;
  display: flex; justify-content: space-between;
}
```

連線狀態 pill (左)：
- `● 連線中` — bear 綠 dot + ink-dim caps 文字
- 斷線時：accent 紅 dot + 「已斷線 重試中...」

訊號規則按鈕 (右)：
- 預設：transparent + accent 邊框 + accent 文字 + accent 實心 badge
- Active (dialog 開啟時)：accent 實心底 + bg 文字 + bg 底 badge with accent 字
- hover：rgba(232,90,79,0.1) 微紅底

### 7.3 自選 row

- 命中：`.has-hit::before` — 左側 3px × 22px accent 紅 marker
- selected：`bg-card` + 2px 紅 border-left + padding-left 12（marker hidden）
- symbol 19px / name 15px / chip 13px
- chip 命中：`border: rgba(232,90,79,0.5)` + `bg: rgba(232,90,79,0.05)` + 上標 10px accent 紅數字

### 7.4 History 4-col grid

```css
grid-template-columns: 120px 200px 1fr 280px;
gap: 32px;
padding: 18px 16px;
```

- 時間：14px ink-muted + 11px ink-dim 日期（小字第二行）
- 股票：18px ink sym + 13px ink-muted name
- 規則：18px Source Serif 4 italic 700 accent
- 觸發資訊：右對齊 18px price + 12px ink-dim vol

header row 用 sticky top:0，10px caps tracking 2px ink-dim 標題。

最新一筆 `.fresh`：accent 左邊框 + 微紅底。

### 7.5 Dialog

```css
.dialog {
  width: min(740px, 90vw);
  max-height: 82vh;
  background: bg-card;
  border: 1px solid line-strong;
}
```

進場：opacity 0→1 + translate 20px→0（200ms ease）。
關閉：點 × / 點 backdrop / 按 Esc。
Backdrop: `rgba(13,10,7,0.85)` + `backdrop-filter: blur(2px)`。

規則 row：toggle switch（accent 開 / line-strong 關）+ pill 標籤（自選全部 / cd 1800s / AND·N 條件）+ 編輯/刪除 action-btn。

## 8. Error Handling

| 情境 | 行為 |
|---|---|
| `GET /today_counts` 失敗 | useTodayHits fallback in-memory 0 baseline（WS 累加從 0 起算）。UI 不擋。重整頁可重抓 |
| `refresh_active_signals` 失敗 | 已 log warning。watchlist add/remove 仍成功（不擋 user）。後續一次 active_signal CRUD 會 trigger 重 refresh |
| WS 斷線 | `useSignalsStream` 已有 reconnect logic（1s/2s/4s/8s/16s/30s backoff）。toolbar pill 顯示「已斷線」 |
| chip 命中數字暫時對不上 | 重整頁觸發 today_counts 重抓即可；不需要實時校正 |
| 自選清空時 | 「自選清單還是空的 — 上面搜尋加入第一檔股票」（沿用） |
| 歷史空時 | 「等待第一筆訊號…」 |
| 規則空時（dialog 內） | 「還沒有訊號規則 — 點上方『+ 新增』設第一條」（沿用） |

## 9. Migration & Cleanup

### App.tsx 改動
```diff
-type Page = "health" | "screener" | "signals" | "watchlist";
+type Page = "health" | "screener" | "monitor";

// Nav items
 const items: Array<{ id: Page; label: string }> = [
   { id: "health", label: "系統狀態" },
-  { id: "signals", label: "即時訊號" },
-  { id: "watchlist", label: "自選" },
+  { id: "monitor", label: "即時監控" },
 ];

-{page === "signals" && <Signals />}
-{page === "watchlist" && <Watchlist />}
+{page === "monitor" && <Monitor />}
```

### 刪除
- `frontend/src/pages/Watchlist.tsx`
- `frontend/src/pages/Signals.tsx`

### 保留但無修改
- `components/IntradayChart.tsx`
- `components/SymbolSearch.tsx`
- `components/ActiveSignalEditor.tsx`
- `components/Sparkline.tsx`
- 所有現有 hooks

### `SignalCard.tsx` 處理
不再使用，但保留 file（萬一將來 dashboard 需要）。

### API client (`lib/api.ts`)
新增 `api.signals.todayCounts()` 一個 method。其餘不動。

## 10. Testing

### 10.1 Backend probes (新增 2 個)

**`probe_watchlist_refresh.py`** — 驗 bug fix：
```python
# 1. POST /api/watchlist (新 symbol 譬如 2454)
# 2. 等 1s
# 3. assert "2454" in (await get_signal_engine())._field_cache
# 4. cleanup: DELETE /api/watchlist/2454
```

**`probe_today_counts.py`** — 驗新 endpoint：
```python
# 1. 插 5 筆 mock signals_log (triggered_at = today TW)
# 2. GET /api/signals/today_counts
# 3. assert len(counts) == 5
# 4. cleanup: DELETE WHERE context_json->>'probe' = 'true'
```

### 10.2 Frontend manual UAT（盤中）

- [ ] 進「即時監控」tab → 看到自選 + chart + history + toolbar
- [ ] 加新 symbol 到自選 → ws 訂閱 + 出現在 list 底部
- [ ] 等 ws tick 進來 → chart 線往右走（IntradayChart 原有行為）
- [ ] 設一條 scope=watchlist 的訊號規則 → 對自選每檔的 chip 都顯示該規則
- [ ] 命中發生時：chip 紅邊+上標+1 / history 加 row / toolbar pill 不變
- [ ] 命中股票自動置頂（重整或 chip count 變化時 reorder）
- [ ] 點 history row → chart 切到該檔 + watchlist 對應 row 高亮 + smooth scroll 回 chart
- [ ] 點 watchlist 任一檔 → chart 切換 + selected 高亮
- [ ] 點 ⚙ 訊號規則按鈕 → dialog 開啟 + button 變實心
- [ ] dialog 內：toggle 啟用切換 / 編輯 / 刪除 / 新增規則
- [ ] Esc / × / 背景 → dialog 關閉
- [ ] 重整頁 → today_counts 重抓，chip 數字校正

## 11. Out of Scope（留 Phase 3.2+）

- IntradayChart 上標今日命中 marker（Full 整合方案的一部分）
- chip 命中當下閃爍動畫
- 點 chip 上標數字 → 該規則該檔 timeline drawer
- 點 history row 後 chart 自動 scroll/zoom 到觸發時間點
- chip「條件到才顯示」mode（hit-only chip）— 失去「監控中」訊息，目前判斷不值得
- Discord 推播 / Browser Notification（Phase 3 一直 out）
- Paper-trading P&L 欄

## 12. 重點檔案清單

### 新增
- `frontend/src/pages/Monitor.tsx`
- `frontend/src/components/TopToolbar.tsx`
- `frontend/src/components/WatchlistWithChips.tsx`
- `frontend/src/components/SignalChip.tsx`
- `frontend/src/components/TriggerHistoryTable.tsx`
- `frontend/src/components/SignalRulesDialog.tsx`
- `frontend/src/hooks/useTodayHits.ts`
- `backend/probe_watchlist_refresh.py`
- `backend/probe_today_counts.py`

### 修改
- `backend/routes/watchlist.py` — POST/DELETE 加 `refresh_active_signals()`
- `backend/routes/signals_history.py` — 加 `GET /api/signals/today_counts`
- `frontend/src/lib/api.ts` — 加 `api.signals.todayCounts()`
- `frontend/src/App.tsx` — Nav tab 整合

### 刪除
- `frontend/src/pages/Watchlist.tsx`
- `frontend/src/pages/Signals.tsx`

## 13. 關連 Memory / Spec

- 上游：`docs/superpowers/specs/2026-05-12-phase-3-realtime-design.md`（Phase 3 核心）
- Memory：[[trading-king-phase-3]] / [[phase-3-next-topic-watchlist-signals-integration]]
- Brainstorm session：`.superpowers/brainstorm/310-1778603419/content/layout-v11.html`（v11 final mockup）
