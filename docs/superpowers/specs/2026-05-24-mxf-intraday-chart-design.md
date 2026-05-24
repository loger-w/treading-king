# MXF 小台指即時分時走勢圖 — 設計文件

**日期**:2026-05-24
**位置**:`MXFBacktest` 頁面(現為 placeholder)
**狀態**:設計完成,待寫實作計畫
**相關**:[MXF 回測 design](./2026-05-15-mxf-backtest-design.md)、[CDP/Cam 延後 memory](../../../C:/Users/USER/.claude/projects/C--side-project-treading-king/memory/project_mxf_intraday_cdpcam_deferred.md)

---

## 1. 目的

在 `MXFBacktest` 頁面加入「小台指(MXF)即時分時走勢圖」,作為**未來 MXF 回測引擎的即時對照**。回測會跑歷史 K + 策略訊號,使用者需要一張「跟歷史對齊的視覺語言」呈現當下市場,以便眼睛比對「現在是不是會出訊號」。

「回測介面」與「即時圖」放同頁,讓波段 / 日內波 trader 都能用同一張圖切換週期觀察。

---

## 2. 範圍

### 2.1 第一版(MVP)包含

- **主圖形態**:K 線 + 走勢線可切換,預設 K 線
- **時段**:日盤 + 夜盤連續顯示(夜盤在前、日盤在後)
- **預設週期**:5m,可切 1m / 5m / 10m / 15m / 30m / 60m(富邦支援這六種)
- **指標**:
  - VWAP(成交加權均價,當交易日累積)
  - MA 5/20(移動平均)
  - 今日交易日高 / 低點標記
- **成交量子圖**:主圖下方 ~20% 高度,bar 對齊 K 棒
- **X 軸 gap 處理**:壓縮 gap + 虛線分隔(只在 05:00–08:45 休市時段)
- **合約**:自動追蹤「近月」(每月第三週三結算後自動換到下個月)

### 2.2 不在這版 scope

- **CDP / Camarilla** — 需要前一交易日 OHLC,富邦期貨無歷史 daily 端點,須等後端 daily OHLC 累積服務做完才能上(見 [memory](../../../C:/Users/USER/.claude/projects/C--side-project-treading-king/memory/project_mxf_intraday_cdpcam_deferred.md))
- **回測訊號標記** — MXF 回測引擎尚未實作
- **期貨專屬指標**:POC、Volume Profile、Open Range
- **多商品**:TXF 大台、期權 — 第一版只 MXF
- **下單功能**:依循專案約束「只訂行情、不下單」

---

## 3. 需求清單(brainstorm 確認)

| 項目 | 決議 |
|---|---|
| 用途 | 回測的即時對照 |
| 主圖形態 | K 線 + 走勢線可切換,預設 K 線 |
| 顯示時段 | 日盤 + 夜盤連續(夜盤起點、日盤收尾) |
| 預設週期 | 5m;支援切 1/5/10/15/30/60 |
| 位置 | `MXFBacktest` 頁(同頁未來會加回測介面) |
| X 軸 gap | 壓縮 + 虛線分隔 |
| 第一版指標 | 成交量子圖、VWAP、MA 5/20、今日高低 |
| 第一版**不包**指標 | CDP、Camarilla(延後) |
| 合約選擇 | 自動追蹤近月 |
| 結構方案 | 抽 `lib/chart-svg.tsx` 工具函式 + 兩個獨立元件(`IntradayChart` 股票版 / `MXFIntradayChart` 期貨版),不改既有股票版邏輯 |

---

## 4. 整體架構

```
┌─────────────────────────────────────────────────────┐
│ Frontend: MXFBacktest 頁                            │
│   └─ <MXFIntradayChart />                           │
│         ↑ candles, activeSymbol, currentSession     │
│   └─ useMXFCandles()                                │
│         ├─ GET /api/mxf/symbol/active               │
│         ├─ GET /api/mxf/candles?tf=5                │
│         └─ WS subscribe channel "mxf:candle"        │
└─────────────────────────────────────────────────────┘
                         ▲
                         │
┌─────────────────────────────────────────────────────┐
│ Backend FastAPI                                     │
│   routes/mxf.py                                     │
│     ├─ GET /api/mxf/symbol/active                   │
│     ├─ GET /api/mxf/candles?tf=N                    │
│     └─ WS broadcast: "mxf:candle"                   │
│                                                     │
│   services/fubon_futures.py                         │
│     ├─ resolve_active_symbol()                      │
│     ├─ fetch_candles(symbol, tf)                    │
│     ├─ subscribe_ws(symbol, session)                │
│     └─ determine_current_session(now)               │
│                                                     │
│   services/fubon_client.py (現有 / DMA login)        │
│   services/fubon_ws.py (現有,新增 futopt channel)   │
└─────────────────────────────────────────────────────┘
                         ▲
                         │
                   富邦新一代 API
                  (futopt REST + WS)
```

---

## 5. 前端設計

### 5.1 元件結構

```
frontend/src/
├─ lib/
│   └─ chart-svg.tsx                ← 新建,從 IntradayChart 抽出
│      ├─ <CandlestickSeries />     K 棒繪製
│      ├─ <LineSeries />            走勢線
│      ├─ <VolumeSubChart />        量子圖
│      ├─ <HoverCrosshair />        hover 十字線
│      ├─ scaleX_compressed()       跨日盤+夜盤的 gap 壓縮 X 軸
│      ├─ scaleY_clamped()
│      ├─ computeVWAP()
│      └─ computeMA()
│
├─ components/
│   ├─ IntradayChart.tsx            ← 改用 chart-svg 的版本(等值重構)
│   └─ MXFIntradayChart.tsx         ← 新建
│
├─ hooks/
│   ├─ useIntradayCandles.ts        ← 現有(股票)
│   └─ useMXFCandles.ts             ← 新建
│
└─ pages/
    └─ MXFBacktest.tsx              ← 整合 <MXFIntradayChart />
```

### 5.2 `MXFIntradayChart` Props

```ts
interface Props {
  symbol: string;             // "MXFF6" 之類
  candles: MXFCandle[];       // 日盤 + 夜盤合併、按 ts 排序
  currentSession: "day" | "night" | "closed";
  loading: boolean;
  error: string | null;
}
```

### 5.3 UI Toggle(圖右上)

- 形態:K 線 ↔ 走勢線
- 週期:1m / 5m / 10m / 15m / 30m / 60m
- 指標:VWAP / MA / 成交量子圖 / 今日高低(各別開關)

切換週期時 → `useMXFCandles` 重打 REST,但 WS 訂閱不變(session 沒變)。

### 5.4 視覺風格

- 沿用股票版 IntradayChart 的色系(平盤上紅、平盤下綠)
- X 軸壓縮 gap,虛線雙線分隔(05:00–08:45)
- Y 軸 auto-scale 到當前 candles 的 [min, max] + 約 5% padding(不像股票版用昨收 ±10% 固定範圍 — 期貨 trader 看的是相對 price action,固定範圍會導致大部分時間圖縮在中間一小條)

---

## 6. 後端設計

### 6.1 `services/fubon_futures.py`(新建)

```python
# 介面摘要(實作細節在 plan 階段)

def resolve_active_symbol() -> str:
    """查富邦 futopt.intraday.products,filter MXF*,sort by expiry,
    回第一個未到期。Cache 1h。"""

def fetch_candles(symbol: str, timeframe: int) -> list[MXFCandle]:
    """打兩段 REST 拿 candles:
      1. intraday.candles(symbol, tf, session='afterhours')  → 夜盤段
      2. intraday.candles(symbol, tf)                        → 日盤段
    合併 + 按 ts 排序(夜盤在前),去重,回。"""

def subscribe_ws(symbol: str) -> None:
    """根據當前時間判斷 session,訂富邦 futopt WS candles channel。
    Session 邊界時(15:00 / 05:00 / 08:45 / 13:45)自動切訂閱。"""

def determine_current_session(now: datetime) -> Literal["day", "night", "closed"]:
    """根據時間判斷:
      08:45 ≤ t ≤ 13:45        → "day"
      15:00 ≤ t 或 t ≤ 05:00   → "night"(且非週末)
      其他                     → "closed"
    特例:週五 13:45 後到週一 08:45 之間皆 "closed"(週五無夜盤)。"""
```

### 6.2 `routes/mxf.py`(新建)

```
GET  /api/mxf/symbol/active       → { symbol: "MXFF6", expiry: "2026-06-17" }
GET  /api/mxf/candles?tf=5        → { symbol, candles: [...], currentSession }
WS   channel "mxf:candle"         → { symbol, candle: {...}, isUpdate: bool }
```

### 6.3 與既有服務整合

- `fubon_client.py`:登入後傳 SDK 實例 → 期貨服務複用 `sdk.marketdata.rest_client.futopt` / `sdk.marketdata.websocket_client.futopt`
- `fubon_ws.py`:新增 `futopt` channel 訂閱與 broadcast 邏輯,**不動現有股票邏輯**
- 不需要新 DB schema(in-memory cache 即可,第一版)

---

## 7. 資料拼接策略

### 7.1 「交易日」定義

期貨交易日 D = **D 的前一天 15:00 → D 當天 13:45**:

```
[夜盤 14h]                 gap 3h45m       [日盤 5h]
15:00 ── 21:00 ── 01:00 ── 05:00  →  08:45 ── 11:00 ── 13:45
```

特例:
- **週五無夜盤** — 週五日盤 13:45 結束後到下週一 08:45 之間無資料

### 7.2 「現在打開頁面」對應的圖

| 當下時間 | 顯示內容 |
|---|---|
| 日盤中(08:45–13:45) | 完整夜盤(已收) + 當前日盤(累積中) |
| 夜盤中(15:00–翌日 05:00) | 夜盤累積中,日盤空 |
| 休市 13:45–15:00 | 凍結到剛結束的日盤最後一根 |
| 休市 05:00–08:45 | 凍結到剛結束的夜盤最後一根 |
| 週末 / 國定假日 | 凍結到最近一個交易日 |

### 7.3 Session 切換流程

```
15:00  (新交易日起點) → 後端清 in-memory cache 重新開始累積
                       → 訂 WS afterHours=true
05:00                  → 取消 WS 訂閱(夜盤結束、休市)
08:45                  → 訂 WS afterHours=false(同交易日的日盤段)
13:45                  → 取消 WS 訂閱(交易日結束)
直到下個 15:00 才開始新一輪
```

前端**不需要 reload 整頁**,WS broadcast 把新 K append 到 candles。

### 7.4 更新頻率

- WS 為主,delay < 1s
- WS 斷線時 fallback 30s polling
- WS 重連後 → 停 polling、回 WS、重拉一次 REST 補缺口

---

## 8. 錯誤處理 / 邊界

| 情況 | 行為 |
|---|---|
| 富邦未登入 | 圖區顯示「未連線到富邦行情服務」+ retry button |
| WS 斷線 | 自動重連(沿用 `fubon_ws.py`)+ 30s polling fallback |
| `symbol/active` 失敗 | 「無法取得 MXF 近月合約」+ retry |
| 全休市(週末 / 假日) | 凍結最後一根 K + 角落「目前休市」chip |
| candles 為空 | 「等待第一根 K 形成」骨架圖 |
| WS push 時間不在當前 session | log warn、忽略不 update |
| 收到 push 的 candle ts 早於 in-memory 最後一根 | log warn、丟棄(防舊資料污染) |

---

## 9. 測試策略

### 9.1 後端 unit test

- `resolve_active_symbol()` — products fixture(月底邊界、跨年、結算當週)
- `merge_candles(day, night)` — 排序正確(夜盤在前)、ts 去重
- `determine_current_session(now)` — 邊界:08:44 / 08:45 / 13:44 / 13:45 / 04:59 / 05:00 / 14:30 / 15:00 / 週五 16:00 / 週六 / 週日 09:00

### 9.2 前端 unit test

- `scaleX_compressed(t, sessions)` — 夜盤 14h + 日盤 5h 壓縮比例驗證
- `computeVWAP` / `computeMA` — fixture 對齊
- candles 排序與去重

### 9.3 手動驗證(每個版本必跑)

- 開盤時段打開頁面 → K 棒實時 update
- 切週期(1m → 5m → 15m)→ 整圖重畫、WS 訂閱不重連
- 切 K 線 ↔ 走勢線 → 主圖瞬切、不重拉資料
- 切 toggle(VWAP / MA / 量子圖 / 高低)→ 對應線顯隱
- 跨 session 切換(13:45 / 15:00)→ 不 reload、K 棒順利 append
- WS 斷線模擬 → 進入 polling、不卡死

---

## 10. 實作順序(MVP 階段)

```
階段 1  後端 fubon_futures.py(含 unit test)
         ├─ resolve_active_symbol
         ├─ determine_current_session
         └─ fetch_candles 合併邏輯
階段 2  後端 routes/mxf.py + fubon_ws.py 加 futopt channel
         ├─ /api/mxf/symbol/active
         ├─ /api/mxf/candles
         └─ WS broadcast "mxf:candle"
階段 3  前端 lib/chart-svg.tsx(從 IntradayChart 抽出,等值重構)
         ├─ 純函式(scale、compute)
         ├─ 子元件(CandlestickSeries / LineSeries / VolumeSubChart)
         └─ smoke test:Monitor 頁仍正常
階段 4  前端 MXFIntradayChart 元件
階段 5  前端 useMXFCandles hook
階段 6  整合進 MXFBacktest 頁
階段 7  ⚠️ 風險點實測 + 修正(見第 11 段)
```

每階段都需通過該層的 unit test + 手動 smoke,再進下一階段。

---

## 11. 風險與待驗證項

實裝前必須**實測富邦 API**,以下假設**未經驗證**:

| 假設 | 為何不確定 | 驗證方法 |
|---|---|---|
| `intraday.candles(session='afterhours')` 回傳「最近一場」夜盤 | 文件未明說「最近」是「已結束」還是「進行中」 | 在不同時段(夜盤中 / 日盤中 / 休市)分別 call,看回應的時間範圍 |
| WS push 是「每根 K 完成才推一次」 vs「每秒更新累積中那根」 | 文件未明說 | 訂閱 1m candles,記錄 push 頻率與內容 |
| Session 切換的精確時間點 | 13:45:00 或 13:45:30?15:00:00 或 15:00:00 後幾秒?是富邦會立刻停推、還是有 grace period? | 在切換時間前後實際監聽 WS |
| 「近月」products 結算當週的行為 | products list 結算後是不是會立刻把該合約 expire? | 觀察結算週的 products list 變化 |
| 跨日(00:00)是否會有特殊 push 行為 | 文件未提 | 監聽 24:00 前後 5 分鐘 |

驗證結果寫進 `docs/notes/mxf-fubon-api-observations.md` 留底。

---

## 12. Future work(不在這版)

- **CDP / Camarilla**:依賴 daily OHLC 累積服務 — 後端做個「每日 13:45 收盤 + 05:00 夜盤收盤後寫 daily K 進 DB」的 cron,累積一週後即可開啟 CDP/Cam
- **回測訊號標記**:等 MXF 回測引擎完成,在主圖疊出策略訊號(進場 / 出場箭頭)
- **期貨專屬指標**:POC、Volume Profile、Open Range
- **多商品**:TXF 大台、選擇權
- **歷史回放模式**:用即時圖的視覺,搭配歷史 candles 回放某一天的走勢

---

## 13. 變更記錄

- 2026-05-24:初版(brainstorm 確認後)
