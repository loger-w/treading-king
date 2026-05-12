# Phase 3 — 即時 WebSocket + Watchlist 內嵌分時走勢（B-2'）

> Spec date: 2026-05-12
> Status: brainstormed, awaiting user review before writing-plans
> Predecessors: Phase 2a (commit 1f06cc6), Phase 2b (commit b694f31)
> Plan: `C:\Users\USER\.claude\plans\neo-api-https-github-com-phenomenoner-n-modular-newt.md` §Phase 3
> Estimate: 4.5 days

---

## 1. Context & Goals

Phase 2b 完成後，trading-king 已有：
- 全市場 1962 個股的每日指標快取（cache_runs / indicator_cache）
- 自定 Filter DSL + 策略 CRUD + 篩股結果頁面
- watchlist 表已建（migration 0003），但前端 UI 跟即時功能尚未啟用

Phase 3 解決三件事：
1. **即時資料管道**：WebSocket 訂閱 watchlist 內個股的 trades 串流，後端維護時序 buffer
2. **即時訊號**：使用者可組合「時窗條件」（N 分鐘漲 X%、量能爆衝）跟「跨指標條件」（即時價碰到 sma_20 / cdp_ah），達成時前端即時推播 + 寫歷史
3. **分時走勢圖**：Watchlist 內嵌每檔股票的當日 1 分 K 線、VWAP 動態線、可選 CDP 5 線（支撐壓力）

Out of scope（明確 future）：
- Discord 推播
- Browser Notification
- Paper-trading log（signals_log 加 outcome_pnl 欄）
- Technical 校驗 vs TradingView
- Phase 2.5 排程（scheduled_screen_runs 等）

---

## 2. Brainstormed Decisions

| 決策 | 選擇 | 理由 |
|---|---|---|
| 整體 scope | **B-2'**（簡化版）| 富邦 `intraday.candles` 直接給 1m K + average，整個 ring_buffer 全日 tick + 自算 VWAP 都不需要 |
| 分時走勢資料源 | REST `intraday.candles` 每分鐘輪詢 + WS trades 補末端 | 兩路徑互補：REST 拿準確，WS 補即時感 |
| ring_buffer 大小 | 30 分鐘 + maxlen=5000（不再是全日） | 只給 signal evaluator 算 N 分鐘漲跌幅用，最大窗口 30 min |
| CDP 資料來源 | `daily_ohlc` 表（新建），watchlist 加入時 backfill 一次 | 全市場 backfill 浪費（user 只看 watchlist 內的 CDP）|
| Watchlist 上限 | 不設硬上限（軟限制 500） | 前端在 200/400 時跳警示色；user 自然收斂 |
| WindowCondition 時窗 | 5 個固定值（60/180/300/600/1800） | 對齊 plan，避免 user 嘗試 noise 訊號（譬如 12 秒漲 3%） |
| Discord 推播 | **不做**（拍板拿掉，列 future TODO） | MVP 簡化；alerts.notify_critical 仍保留（系統異常用，與訊號分開） |
| `services/discord.py` | 不實作 | 同上 |
| `active_signals.discord_webhook_url` 欄位 | 不加 | 未來真要做 → migration 0005 補欄位 |
| schema_version | 維持 1 | DSL 加 cdp_* / window_conditions 屬非破壞性擴展 |
| `indicator_cache` schema | **不改** | high/low/open 不存到這表，CDP 走 daily_ohlc |

---

## 3. Architecture

```
┌────────────────────── 富邦 Neo API ──────────────────────────────┐
│  REST: intraday.candles (1m K + average)                         │
│        intraday.quote (即時報價 snapshot)                         │
│        historical.candles (日 K backfill)                         │
│  WS:   trades channel (per-symbol tick stream)                   │
└────────┬────────────────────────────────────┬────────────────────┘
         │                                    │
         ▼                                    ▼
┌────────────────────── BFF (FastAPI) ─────────────────────────────┐
│                                                                  │
│  fubon_ws.py     — WS pool (≤3 連線, 600 容量, refcount)         │
│       │                                                          │
│       ▼                                                          │
│  ring_buffer.py  — per-symbol deque (30 min, maxlen=5000)        │
│       │                                                          │
│       ▼                                                          │
│  signal_engine.py — bounded queue → evaluator (Window + Filter)  │
│       │                                                          │
│       ├──► /ws/realtime broadcaster (前端推訊號)                  │
│       ├──► supabase_writer (500ms batch flush 到 signals_log)    │
│       └──► (Discord 推 — future)                                  │
│                                                                  │
│  cdp.py          — 從 daily_ohlc 算 5 線 + in-memory cache       │
│  alerts.py       — 系統異常 webhook (Phase 1 既有)                │
└────────┬─────────────────────────────────────────────────────────┘
         │                                                          
         ▼                                                          
┌────────────── Supabase ─────────────┐  ┌─── 前端 SPA ────────────┐
│  active_signals  (新)               │  │  Watchlist 頁            │
│  signals_log     (新)               │  │   - 自選清單              │
│  daily_ohlc      (新)               │  │   - IntradayChart        │
│  watchlist       (Phase 2b 已建)    │  │     (REST + WS)          │
│  indicator_cache (Phase 2a)         │  │   - VWAP / CDP toggle    │
│  symbols (Phase 1)                  │  │  Signals 頁              │
│  strategies (Phase 2b)              │  │   - 即時訊號流 (WS push) │
└─────────────────────────────────────┘  │   - 過去訊號歷史         │
                                         └──────────────────────────┘
```

**新東西總清單**

| 類型 | 名稱 |
|---|---|
| backend service | `fubon_ws`, `ring_buffer`, `signal_engine`, `supabase_writer`, `cdp` |
| backend route | `routes/active_signals`, `routes/signals_history`, `routes/watchlist`, `routes/ws`, `routes/candles`, `routes/cdp` |
| migration | `0004_realtime_signals.sql` (active_signals + signals_log + daily_ohlc) |
| frontend page | `Watchlist.tsx`, `Signals.tsx` |
| frontend component | `IntradayChart.tsx`, `Sparkline.tsx`, `SignalCard.tsx`, `ActiveSignalEditor.tsx` |
| frontend hook | `useSignalsStream`, `useIntradayCandles`, `useWatchlist` |

---

## 4. Backend Components

### 4.1 `services/fubon_ws.py` — WebSocket pool

**職責**：管理 ≤3 條連線、200 sub/條、subscription refcount、自動重連、circuit breaker。

```python
pool = get_ws_pool()
await pool.subscribe(symbol="2330", owner_id="active_signal_xyz")
await pool.unsubscribe(symbol="2330", owner_id="active_signal_xyz")
pool.on_tick(callback=lambda symbol, tick: ...)
```

**內部關鍵**：
- Refcount registry: `dict[symbol, set[owner_id]]`，subscribe 第一個 owner 才真打富邦；unsubscribe 最後一個 owner 才真退訂
- Circuit breaker: 5 次連續重連失敗 → 停止重試 + alerts.notify_critical + health degraded
- Reconnect: exponential backoff 1→2→4→8→max 60s
- 過夜重連: 8:25 cron 觸發 `apikey_login + init_realtime` 重做、subscriptions 重建

**依賴**: `fubon_client`, `alerts`, `logging`

### 4.2 `services/ring_buffer.py` — Per-symbol 時間窗 deque

**職責**：thread-safe 時序 buffer，給 evaluator 算 N 分鐘漲跌幅。

```python
buf = get_ring_buffer()
buf.ensure(symbol)              # subscribe 時呼叫，預建 entry+lock
buf.append(symbol, tick)        # WS callback 餵
buf.discard(symbol)             # unsubscribe + refcount==0 才刪
ticks = buf.window(symbol, seconds=300)
```

**內部關鍵**：
- `dict[symbol, deque[Tick]]` + `dict[symbol, threading.Lock]`
- subscribe 時就預建 entry+lock（callback path 永遠不建）— 解 plan §R2 race
- 雙重保護：`maxlen=5000` OOM 防呆 + 每次 append tail trim 砍超過 1800s 的舊 tick

**依賴**: 純 Python

### 4.3 `services/signal_engine.py` — 訊號評估 + 廣播協調

**職責**：消費 ring_buffer 的 tick，跑兩類條件，達成 fan out。

```python
engine = get_signal_engine()
await engine.start()
await engine.enqueue(symbol, tick)
await engine.refresh_active_signals()
status = engine.health()  # {queue_depth, lag_ms, dropped_today}
```

**內部關鍵**：
- `asyncio.Queue(maxsize=5000)` — `put_nowait` 滿了 → `dropped_ticks_today += 1`，不 block 上游
- 兩類條件：
  - `WindowCondition` (price_change_pct/volume_burst/trade_count) → 從 ring_buffer 算
  - `Filter.conditions` (cross sma_20 / >= cdp_ah) → 用**全局** in-memory cache `dict[symbol, dict[field, value]]`（所有 active_signal 共用）；啟用任何 active_signal 時把涉及的 symbol × field 載入；engine startup 時批次載入全部 enabled active_signal 涉及的範圍
- per `(active_signal_id, symbol)` cooldown table：`dict[tuple, datetime]`，cooldown 內已觸發 → skip
- session-aware：`ignore_auctions=true` 且在集合競價時段 → skip volume_burst
- backpressure 降級：監控 evaluator_lag_ms，若 `lag > 5000ms 連續 30s` → 自動 disable 全部 active_signals + alerts + health degraded
- 每日 16:30 背景刷新 in-memory indicator_cache + cdp cache

**依賴**: `ring_buffer`, indicator_cache (DB), daily_ohlc (DB), `supabase_writer`, broadcaster

### 4.4 `services/supabase_writer.py` — Batch flush async writer

```python
writer = get_supabase_writer()
await writer.start()
writer.append(signal_row)  # 純 in-memory, 立即 return
```

**內部**: 獨立 async task；500ms 或 ≥100 列觸發 batch insert；失敗 retry 1 次仍失敗 → alerts + buffer 保留下次重送；buffer > 1000 列 → FIFO drop + metric。

### 4.5 `services/cdp.py` — CDP 5 線算 + cache

```python
cdp = get_cdp_service()
levels = await cdp.get(symbol="2330")
# {"ah": 612, "nh": 598, "cdp": 585, "nl": 575, "al": 570, "as_of_date": "2026-05-11"}
await cdp.refresh(symbol)
```

**公式**：給昨日 H/L/C：
- CDP = (H + L + 2C) / 4
- AH (最高值) = CDP + (H − L)
- NH (近高值) = 2 × CDP − L
- NL (近低值) = 2 × CDP − H
- AL (最低值) = CDP − (H − L)

**內部**: in-memory `dict[symbol, dict]` cache + 每日 16:30 cron 刷新 watchlist 內所有 symbol。

### 4.6 Backend routes

| Route | 用途 |
|---|---|
| `POST/DELETE /api/watchlist` | 加 / 移除 + 觸發 backfill + WS pool subscribe/unsubscribe |
| `GET /api/watchlist` | 列當前 + 每檔即時 quote |
| `GET /api/active_signals` `POST/PUT/DELETE` | 即時訊號規則 CRUD |
| `GET /api/signals/history?symbol=&since=` | 從 signals_log 撈訊號歷史 |
| `WS /ws/realtime` | 前端訂閱即時訊號 + 即時 tick 廣播 (X-API-Key 在 query string) |
| `GET /api/candles/{symbol}/intraday` | proxy fubon `intraday.candles` (266 筆 1m K + average) |
| `GET /api/cdp/{symbol}` | 回 CDP 5 線值 |

`/api/health` 加兩個欄位：
```json
{
  "ws_connections": {"active": 3, "subscribed_symbols": 47, "max_capacity": 600},
  "signal_engine": {"queue_depth": 12, "lag_ms": 8, "dropped_today": 0, "degraded": false}
}
```

---

## 5. Data Flows

### 5.1 訊號達成（核心熱路徑）

```
富邦 WS push tick
  → fubon_ws on_message: 過濾 event=="data" → Tick(symbol, price, size, time)
  → ring_buffer.append(symbol, tick)
  → signal_engine.queue.put_nowait(tick)  [滿 → drop + metric]
  → evaluator consume:
      1. 查 active_signals 涵蓋這 symbol 的有哪些
      2. 對每個 active_signal:
         - WindowCondition (從 ring_buffer 算)
         - Filter.conditions (從 in-memory cache 比即時)
         - AND/OR 組合
      3. 達成 → cooldown table 檢查 → 若新觸發 → 寫 cooldown
      4. fan-out:
         ├─► 前端 WS broadcast (async, 無 IO 阻塞)
         └─► supabase_writer.append (純 in-memory, 立即 return)
                → 500ms 後 batch INSERT signals_log
```

熱路徑 latency 預期: tick → broadcast < 50ms。

### 5.2 Watchlist 加入

```
POST /api/watchlist {symbol: "3711"}
  → INSERT watchlist
  → ws_pool.subscribe(symbol, owner="watchlist")
       refcount: {3711: {"watchlist"}}
  → ring_buffer.ensure(symbol)
  → asyncio.create_task(_backfill_cdp(symbol))  [背景, 不 block]
       → fubon historical.candles → INSERT daily_ohlc → cdp.refresh
  → 200 (~100ms)
前端: 列表新增 row → 點 → IntradayChart → CDP toggle 可立即點
```

### 5.3 啟用 active_signal

```
POST /api/active_signals
  → INSERT (enabled=true)
  → 解析 scope (watchlist 全部 / 手動 symbols)
  → 對每個 symbol: ws_pool.subscribe(owner=active_signal_id) + ring_buffer.ensure
  → signal_engine.refresh_active_signals()
       → 載入涉及的 indicator_cache + cdp 進 in-memory cache
  → 200
反向 (DELETE / disable): 對每個 symbol unsubscribe(owner=active_signal_id)，
  refcount==0 才真退訂；watchlist owner 還在的不退。
```

### 5.4 分時走勢開啟 (per IntradayChart)

```
useIntradayCandles(symbol):
  ├─ 初始: GET /api/candles/{symbol}/intraday → 266 筆 1m K → setState
  ├─ /ws/realtime 收 tick → 只更新「當前進行中的最後一根 candle」的 close
  │       (不重算 average — 富邦的 average 是 minute-level VWAP，
  │        前端不維護 cumulative sum，等下次 REST refresh 拿正確值)
  └─ setInterval(60s): 重打 REST 補精確 average + 跨分鐘時加新 candle
```

雙路徑互補: REST 拿準確、WS 拿即時感。worst case 60 秒延遲補正。
VWAP 線在 60 秒間隔內可能有「最後一根末端跳動」的視覺，可接受。

### 5.5 Backpressure 降級

```
監控 task (每秒一次):
  if lag_ms > 5000 連續 30s:
    UPDATE active_signals SET enabled=false WHERE enabled=true
    signal_engine.clear_active_set()
    alerts.notify_critical("evaluator overload, all signals disabled")
    health: status=degraded, signal_engine.degraded=true
前端 Health 頁顯示「訊號引擎已自動停用 (過載)」
user 改善後手動重啟 active_signals
```

選 disable 不選 drop tick：drop tick 會讓 user 以為訊號還在跑但實際漏掉，比公開 disable 危險。

### 5.6 過夜重連

```
8:25 cron (背景):
  ① ws_pool 進 paused 狀態
  ② await fubon_client.relogin()  [apikey_dma_login + init_realtime]
  ③ ws_pool.reconnect_all()  [拿 refcount 內所有 symbol 重訂]
  ④ 恢復 evaluator
  ⑤ health.ws_last_reconnect_at 更新
失敗 → alerts.notify_critical + fubon_status=error + evaluator paused
```

---

## 6. UI Design

風格延續 Phase 2a/2b: Editorial Dark + 台股紅綠 + 中文 + 章節編號。

### 6.1 Nav 變動

啟用 `即時訊號` 跟 `自選` 兩個 tab（原 Phase 2b 寫成 disabled）。

### 6.2 Watchlist 頁

```
┌──────────────────────────────────────────────────────────────────┐
│  壹  自選清單                  [搜尋代號或名稱] [+]               │
│  ┌──────── 列表 ─────────┐  ┌── 貳  分時走勢 ────────────────┐ │
│  │ ★ 2330 台積電         │  │ 2330 台積電  ▲ 580.0  +0.87%    │ │
│  │   580.0 ▲+5.0 +0.87% │  │ ┌──── chart ──────────────────┐ │ │
│  │   ⚡ 2 個訊號規則      │  │ │ 612 ─AH─ ─ ─ ─ ─ ─ ─ ─ ─    │ │ │
│  │ ─────────────────────│  │ │ 598 ─NH─ ─ ─ ─ ─ ─ ─ ─ ─    │ │ │
│  │   2317 鴻海           │  │ │ 585 ─CDP─ ─ ─ ─ ─ ─ ─ ─ ─   │ │ │
│  │   115.5 ▾-1.5 -1.28% │  │ │ 580 ───╱╲╱─即時─VWAP         │ │ │
│  │ ─────────────────────│  │ │ 570 ─AL─ ─ ─ ─ ─ ─ ─ ─ ─    │ │ │
│  │ ★ 3008 大立光         │  │ │     09:00            13:30   │ │ │
│  │   2850 ▴+30 +1.06%   │  │ └──────────────────────────────┘ │ │
│  │   ⚡ 1 個訊號規則      │  │ [✓ VWAP] [✓ CDP]                │ │
│  │                      │  │                                   │ │
│  │   6505 台塑化         │  │ 參  訊號規則                       │ │
│  │   85.2  — 0.0 0.00%  │  │ • 3 分內漲 ≥ 2%   [pause]         │ │
│  │                      │  │ • 跌破 sma_20    [pause]         │ │
│  │                      │  │ + 新增訊號規則                     │ │
│  └──────────────────────┘  └───────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

互動細節（節錄）:
- 搜尋輸入 typing → debounce 200ms → `GET /api/symbols?search=`
- 列表 row：hover 變亮（視覺 feedback），click 切換右側 detail panel 顯示該檔
- 即時報價: `/ws/realtime` push, 500ms throttle 避免閃爍
- Detail panel 沒選中: 顯示「← 點選左邊任一檔股票看分時走勢」
- 列表空: 顯示「自選清單還是空的 — 上面搜尋加入第一檔股票」
- CDP toggle 預設關（避免一進去太擠）

### 6.3 Signals 頁

```
┌──────────────────────────────────────────────────────────────────┐
│  壹  即時訊號                                                      │
│  ┌─ 已啟用規則 (3) ───────────────── [+ 新增] [全部停用] ────┐   │
│  │ ● 3 分內漲 ≥ 2%      watchlist 全部   30s cd  [編輯]      │   │
│  │ ● 跌破 sma_20        watchlist 全部   30 分 cd [編輯]      │   │
│  │ ● 量能爆衝 60s × 5x  自選 [2330,2317] 10 分 cd [編輯]      │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                  │
│  貳  即時訊號流  [🔊]  最近 50 筆                                  │
│  ──────────────────────────────────────────────────────────────  │
│  09:42:30 2330 台積電  ╱╲╱╲ ─ 30 點 sparkline                    │
│           580.0 ▲+5.0 (+0.87%) │ 5 分內漲 2.1%                   │
│  ──────────────────────────────────────────────────────────────  │
│  ...                                                              │
│                                                                  │
│  參  今日統計  訊號 24 · 涉及 12 檔 · 最活躍 [2330 ×8]            │
└──────────────────────────────────────────────────────────────────┘
```

互動: `[🔊]` toggle audio (首次點解鎖 browser audio policy)；row click 跳到 watchlist 該股；訊號流自動上滑，scroll 中段時 pause 不打擾。

### 6.4 ActiveSignalEditor (modal)

跟 Phase 2b ConditionEditor 同邏輯但加 WindowCondition 區塊 + Cooldown + Scope。版面見 Section 4 Brainstorm 紀錄。

### 6.5 Health 頁加新 row

```
─ WebSocket 訂閱 ──────  3 / 600 容量
   3 條連線、訂閱 47 檔
─ 訊號引擎 ────────────  執行中
   queue 12 / 5000、lag 8ms、今日 dropped 0
```

---

## 7. Error Handling

### 7.1 富邦連線層

| 情境 | 行為 | User 感受 |
|---|---|---|
| WS 斷線（瞬斷） | exp backoff 1→2→4→8→max 60s 重連 + 重訂閱 | Health 短暫 degraded → 自動恢復 |
| WS 連續 5 次失敗 | circuit open + alerts + evaluator paused | Health 紅；要手動「重新連線」 |
| 單檔 subscribe 拒絕 | refcount 標 failed，不重試該檔 | 該 row 顯示「無即時資料」 |
| 過夜 token 失效 | 重用 Phase 1 `fubon_client._login_with_retry`（3 次 1s/2s/4s backoff）後仍失敗 → fubon_status=error + alerts | Health fubon=error；evaluator paused |
| fubon REST 5xx | per-call retry 1 次後 raise | 對應 endpoint 502 |

### 7.2 Buffer / Evaluator 層

| 情境 | 行為 |
|---|---|
| queue 滿 | drop tick + dropped_ticks_today++ |
| evaluator lag > 5s × 30s | 自動停用全部 active + alerts + health degraded |
| cooldown table 過大 | LRU 淘汰最舊（cap 50000，幾乎不會發生）|
| in-memory cache stale | 16:30 cron 失敗 → log + alerts；evaluator 用舊 cache 繼續 |
| 涉及未訂閱 symbol | 啟用時自動 subscribe 補上；fubon 容量滿則拒絕啟用 |
| condition LHS/RHS 是 NULL | 一律算 False |

### 7.3 Supabase 層

| 情境 | 行為 |
|---|---|
| signals_log batch insert 失敗 | retry 1 次仍失敗 → alerts；buffer 保留 |
| buffer > 1000 持續寫不進 | FIFO drop + dropped_signal_writes++ |
| active_signals 查詢失敗 | startup 失敗 → fubon 不訂閱、evaluator paused；refresh 失敗 → 用舊 list |
| daily_ohlc backfill 失敗 | log warn；不擋 watchlist 加入；CDP toggle 時 lazy retry |

### 7.4 前端層

| 情境 | 行為 |
|---|---|
| `/ws/realtime` 斷線 | exp backoff 重連，頂部 indicator 顯示「重連中」 |
| API 5xx | 對應元件顯示錯誤 banner + 手動 retry |
| `/api/candles/{sym}/intraday` 503 | skeleton + 5s 後 retry 1 次 |
| `/api/cdp/{sym}` 503 | spinner + 2s retry |
| 離開 Signals 頁久了再回來 | 重連 WS + `/api/signals/history?since=` 補歷史 |
| 多 tab 同時開 | 每 tab 各自 WS 連線；後端 broadcast 寄 N 份 |

### 7.5 Startup 順序

```
lifespan startup:
  ① fubon_client.init()
  ② supabase.init()
  ③ ring_buffer pre-fetch active_signals + watchlist 涉及的 symbol
  ④ ws_pool.subscribe_all()
  ⑤ signal_engine.start() (load active_signals + 涉及的 indicator/cdp)
  ⑥ supabase_writer.start()
  ⑦ accept HTTP 請求
任何步驟失敗 → 進 degraded mode（不阻塞 startup）
```

---

## 8. DB Schema (migration 0004)

### 8.1 `active_signals`

```sql
create table if not exists active_signals (
  id                uuid primary key default gen_random_uuid(),
  name              text not null,
  filter_json       jsonb not null,
  scope             jsonb not null,
  cooldown_seconds  int  default 1800 check (cooldown_seconds between 60 and 86400),
  ignore_auctions   boolean default true,
  enabled           boolean default true,
  created_at        timestamptz default now()
);

create index if not exists idx_active_signals_enabled
  on active_signals(enabled) where enabled;
```

### 8.2 `signals_log`

```sql
create table if not exists signals_log (
  id                bigserial primary key,
  active_signal_id  uuid references active_signals(id),
  symbol            text references symbols(symbol),
  triggered_at      timestamptz default now(),
  trigger_price     numeric,
  trigger_volume    bigint,
  context_json      jsonb
);

create index if not exists idx_signals_log_triggered_desc
  on signals_log(triggered_at desc);
create index if not exists idx_signals_log_symbol_time
  on signals_log(symbol, triggered_at desc);
create index if not exists idx_signals_log_active_signal_time
  on signals_log(active_signal_id, triggered_at desc);
```

### 8.3 `daily_ohlc`

```sql
create table if not exists daily_ohlc (
  symbol  text not null references symbols(symbol),
  date    date not null,
  open    numeric,
  high    numeric,
  low     numeric,
  close   numeric,
  primary key (symbol, date)
);

create index if not exists idx_daily_ohlc_date
  on daily_ohlc(date);
```

容量估: ≤500 檔 × 365 天 ≈ 180K row/年 × 50 bytes ≈ 9 MB/年（極小）。

### 8.4 RLS

```sql
alter table active_signals enable row level security;
alter table signals_log    enable row level security;
alter table daily_ohlc     enable row level security;

create policy "anon can read active_signals" on active_signals for select to anon, authenticated using (true);
create policy "anon can read signals_log"    on signals_log    for select to anon, authenticated using (true);
create policy "anon can read daily_ohlc"     on daily_ohlc     for select to anon, authenticated using (true);
-- 寫只 service_role bypass RLS
```

---

## 9. Pydantic Models (`backend/models/condition.py` 擴充)

### 9.1 `ConditionField` 加 5 個 CDP

```python
ConditionField = Literal[
    # Phase 2b 既有 16 個
    "close", "change_pct", "volume", "amount",
    "rsi_14", "macd", "macd_signal",
    "kdj_k", "kdj_d", "kdj_j",
    "sma_5", "sma_20", "sma_60",
    "bbands_upper", "bbands_middle", "bbands_lower",
    # Phase 3 新增
    "cdp_ah", "cdp_nh", "cdp", "cdp_nl", "cdp_al",
]
```

### 9.2 `WindowCondition`

```python
WindowConditionType = Literal["price_change_pct", "volume_burst", "trade_count"]
WindowSeconds = Literal[60, 180, 300, 600, 1800]

class WindowCondition(BaseModel):
    type: WindowConditionType
    window_seconds: WindowSeconds
    operator: Literal["gt", "gte", "lt", "lte"]
    value: float
```

### 9.3 `ActiveFilter`

```python
class ActiveFilter(Filter):
    """即時訊號專用 — 加上時窗條件。"""
    window_conditions: list[WindowCondition] = Field(default_factory=list)
```

### 9.4 `Scope` (discriminated union)

```python
class WatchlistScope(BaseModel):
    type: Literal["watchlist"]

class SymbolsScope(BaseModel):
    type: Literal["symbols"]
    symbols: list[str] = Field(min_length=1)

Scope = WatchlistScope | SymbolsScope
```

### 9.5 `ActiveSignalCreate` / `ActiveSignalOut`

```python
class ActiveSignalCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    filter_json: ActiveFilter
    scope: Scope
    cooldown_seconds: int = Field(default=1800, ge=60, le=86400)
    ignore_auctions: bool = True
    enabled: bool = True

class ActiveSignalOut(ActiveSignalCreate):
    id: str
    created_at: str
```

### 9.6 Schema 演進策略

| 場景 | 處理 |
|---|---|
| Phase 2b 舊 strategy 載入 | 預設 `window_conditions=[]`，cdp_* 不在 conditions 內就無事 — 完全相容 |
| 新 cdp_ah condition 但無 daily_ohlc | 評估時 LHS/RHS=None → False；user 自查 |
| Future cross_above 等 | schema_version 升 2 + DSL migrator |
| Future Discord push | migration 0005 加 discord_webhook_url |

---

## 10. Test Strategy

### 10.1 Module-level smoke (< 5s, 每改 service 跑)

| Service | script | 驗 |
|---|---|---|
| `ring_buffer.py` | inline `__main__` | append / window / tail trim / 多執行緒 race |
| `signal_engine.py` | `scripts/probe_signal_engine.py` | mock tick → WindowCondition 達成 → cooldown skip → ignore_auctions → backpressure |
| `cdp.py` | inline `__main__` | OHLC `(2300,2320,2280,2290)` → CDP=2295 / AH=2335 / NH=2310 / NL=2270 / AL=2255 |

### 10.2 Integration smoke (~30s, 連真 fubon + supabase)

| 對象 | script | 驗 |
|---|---|---|
| WS pool refcount | `scripts/probe_ws_pool.py` | sub(2330,A) → sub(2330,B) → 富邦只 1 訂閱；unsub A → 還在；unsub B → 真退訂 |
| 過夜重連 | `scripts/probe_overnight_reconnect.py` | 強制 disconnect → 重 login → 訂閱還原 |
| supabase_writer | `scripts/probe_writer.py` | 1000 mock signal → 等 500ms → 驗 1000 列 |
| daily_ohlc backfill | `scripts/probe_backfill_cdp.py` | 對 2330 → daily_ohlc 5/11 row + CDP 5 值合理 |
| End-to-end signal | `scripts/probe_e2e_signal.py` | 啟用「3 分內漲 0.001%」→ 跑 10 分鐘 → signals_log 有 entry + cooldown 有效 |

### 10.3 Manual UAT (盤中 / 盤後)

對齊 plan §Phase 3 驗證清單:
- 「3% / 5 分鐘」watchlist 條件 → 盤中熱絡時段 30 分內見真實命中
- 雙分頁同步收到 broadcast
- 啟用兩個 active_signal 都監控 2330 → 取消其一另一個正常收 tick
- WS 殺掉（手動 kill 網路 30 秒）→ 自動重連 + 訂閱還原
- 連殺 5 次 → circuit open + alerts.notify_critical
- BFF kill 重啟 → cache + signals_log query 仍可用
- load test: mock 灌 1000 tick/sec × 30s → dropped_ticks=0 + lag<1000ms

---

## 11. Estimate (4.5 days)

| Day | Task |
|---|---|
| **Day 1 上** | Migration 0004 + Pydantic 擴充 + ring_buffer + smoke |
| **Day 1 下** | fubon_ws WS pool + refcount + 過夜重連 + probe |
| **Day 2 上** | signal_engine + cooldown + backpressure + smoke |
| **Day 2 下** | supabase_writer + cdp + backfill + 各自 smoke |
| **Day 3 上** | routes (active_signals/watchlist/signals_history/candles/cdp/ws) + main.py + Health 加欄 |
| **Day 3 下** | api.ts + hooks (useSignalsStream/useIntradayCandles/useWatchlist) |
| **Day 4 上** | Watchlist.tsx + IntradayChart.tsx (VWAP+CDP) + Sparkline.tsx |
| **Day 4 下** | Signals.tsx + ActiveSignalEditor + WindowCondition row |
| **Day 5 上** | App.tsx nav + zh-TW 校對 + 端到端 + integration smoke |

---

## 12. Risks

| 風險 | 緩解 |
|---|---|
| 富邦 WS 200 sub/條真實上限沒實測 | Day 1 下午先 probe，若上限 < 200 → 縮 watchlist 軟上限 |
| evaluator 在 1000 tick/sec 下 lag 失控 | Day 2 末 load test，不過 → Day 3 加 batched evaluator (每 100ms 一批) |
| 富邦 historical.candles 對非個股 error | backfill 加 try/except per symbol；watchlist 加入時攔 invalid symbol |
| user 啟用過多 active × symbol 把 cache 撐爆 | per active × symbol cap (active 20 × symbol 500 = 10K)；超過拒絕啟用 |
| 富邦過夜 token 失效機制未驗證 | Day 5 末 manual UAT 跑「跨日 + 隔天 8:25 reconnect」 |

---

## 13. Out of Scope (明確 future TODO)

- ❌ Discord 推播
- ❌ Browser Notification
- ❌ Sound 進階（音量、自選音效）— MVP 只 on/off
- ❌ Watchlist 群組分類（持倉 / 觀察分頁）
- ❌ 訊號歷史搜尋 / filter UI
- ❌ Paper-trading log（signals_log 加 outcome_pnl + hourly job）
- ❌ Technical 校驗 vs TradingView
- ❌ scheduled_screen_runs（Phase 2.5 排程）

---

## 14. References

- Plan: `C:\Users\USER\.claude\plans\neo-api-https-github-com-phenomenoner-n-modular-newt.md`
- Phase 2a state: `C:\Users\USER\.claude\projects\C--side-project-trading-king\memory\project_phase_2a_state.md`
- Phase 2b state: `C:\Users\USER\.claude\projects\C--side-project-trading-king\memory\project_phase_2b_state.md`
- Predecessor commits: 1f06cc6 (phase 2a), 20a552f (polish), b694f31 (phase 2b)
- 富邦 SDK probes: `backend/scripts/probe_technical_fields.py`, `probe_sdk_paths.py`, `probe_candles.py`

---

## 15. UAT Checklist (盤中跑)

依 plan §11.3 — 端到端 manual verification（需在交易時段 09:00-13:30 TWST）：

- [ ] Watchlist 頁加 2330 → 看到列表 + 即時報價更新
- [ ] 點 2330 → 右側分時走勢圖出現 (260+ 點 / 整日)
- [ ] toggle CDP → 5 條水平線疊上去 (有 label AH/NH/CDP/NL/AL)
- [ ] toggle VWAP → 灰虛線消失/出現
- [ ] Signals 頁建一個「3% / 5 分鐘」watchlist 規則 → 30 分內收到至少 1 筆推播
- [ ] 雙分頁同時開 Signals → 兩邊都收到同一筆訊號
- [ ] 啟用兩條 active_signal 都監控 2330 → 取消其中一條 → 另一條仍正常觸發
- [ ] 手動 disconnect 網路 30 秒 → 自動重連 + 訂閱還原
- [ ] Health 頁 ws_connections + signal_engine 數字會動
- [ ] 跑 `backend/scripts/probe_e2e_signal.py` 盤中 10 分鐘內 signals_log 至少 1 筆 + 自動 cleanup
