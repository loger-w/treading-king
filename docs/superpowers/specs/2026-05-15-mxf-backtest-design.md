# 小台指策略回測 (MXF) 引擎 — 設計 Spec

**Date**: 2026-05-15
**Status**: ⏸ **WIP — 暫停在 B1 資料層;等使用者從期貨商取得歷史 1m K 後續寫**

---

## Context

`feature/mini-fut-backtest` 分支上的 placeholder 頁(`frontend/src/pages/MXFBacktest.tsx`)已重新命名與調文案完畢(commit `0f4033a`)。接著要把回測引擎的設計定下來。

第一個必須釘死的議題是「歷史 1m K 從哪來」,因為:
- 它決定 backend 資料層的 ETL 結構與本地儲存格式
- 它決定回測能跑多久的歷史區間(影響策略可信度)
- 它決定 MVP 啟動時程(是「明天就能跑」還是「等三個月累積」)

這份 spec **暫停在 B1 資料層**,等使用者從富邦/群益期貨後台取得歷史 1m K dump 後再續寫。

---

## 已確認的設計決策(不會改)

| 項目 | 決定 |
|---|---|
| 商品標的 | 小台指 (MXF) — **不含**微型台指 (MTX) |
| UI 文案 | 「小台指策略回測 (MXF)」,Sidebar / 頁面標題並列商品名 + 代碼 |
| 程式碼識別字 | 一律 `MXF` / `mxf_backtest` / `mxf` |
| 策略性質 | **波段為主**,接受隔夜部位;當沖/盤中強平由策略 DSL 內的條件表達式決定(不寫死「日盤收盤強平」邏輯) |
| 支援週期 | 1m / 3m / 5m / 7m / 13m / 15m / 30m / 60m / D — 富邦不直接給的(3m/7m/13m)由 1m resample |
| 策略形式 | UI 視覺化拼裝(no-code)→ JSON DSL → 後端條件式解譯,條件包含指標比較 + 邏輯運算 + 時間條件(`time_within` / `bars_since` 等) |
| 資料儲存 | 本地檔案快取(parquet 為候選),細節待 B1 釘死 |

---

## 已查到的關鍵事實(供後續 spec 續寫使用)

### 富邦 Neo Python SDK v2.2.x — 期貨 market data 能力

| 能力 | 結論 | 文件 |
|---|---|---|
| futopt market data 入口 | `sdk.marketdata.rest_client.futopt.intraday.*` 存在,**無 `historical.*`** | [getting-started.txt](https://www.fbs.com.tw/TradeAPI/docs/market-data-future/http-api/getting-started.txt) |
| 期貨歷史 K 線 API | **不存在**(對照 stock 有 historical,但分 K 也只回近 5 日) | [intraday/candles.txt](https://www.fbs.com.tw/TradeAPI/docs/market-data-future/http-api/intraday/candles.txt) |
| 期貨當日 K | `restfutopt.intraday.candles(symbol, session?, timeframe?)`,支援 1/5/10/15/30/60 分 K | 同上 |
| K 線資料欄位 | `date, open, high, low, close, volume, average`(**無 open_interest**) | 同上 |
| 日/夜盤 | `session` 參數區分:`afterhours` 切夜盤,預設日盤 | 同上 |
| MXF 月份符號 | 文件**沒列**(只給 TXF 範例),要用 `intraday.products?type=FUTURE&contractType=I` 動態查近月 | [intraday/products.txt](https://www.fbs.com.tw/TradeAPI/docs/market-data-future/http-api/intraday/products.txt) |
| Rate limit | 日內 300 req/min;期貨無 historical 端點 | [rate-limit.txt](https://www.fbs.com.tw/TradeAPI/docs/market-data-future/rate-limit.txt) |
| WS 訂閱 | `futopt.subscribe({'channel':'candles','symbol':...})`,有 `afterHours` 布林 | [websocket-api/candles.txt](https://www.fbs.com.tw/TradeAPI/docs/market-data-future/websocket-api/market-data-channels/candles.txt) |

**結論**:富邦 SDK 不提供期貨歷史 K,只能拉「今日盤中」(REST `intraday.candles`)或「即時推播」(WS)。要做歷史回測必須:
- 每日盤後落地累積(慢,等 1-3 個月才有夠用資料)
- 或從外部資料源 bootstrap

### FinMind 評估結果

- **沒有現成 1m K dataset**(只有 daily 免費 + tick 付費 sponsor)
- 即時 snapshot **不涵蓋 MXF**(僅 TXF/TMF/CDF)
- TaiwanFuturesTick (2011 起) 要付費 sponsor 才能拿,且要自己 resample 成 1m

**結論**:**不**走 FinMind 路徑(免費版沒 1m,付費還要自己組,不划算)。

### 後端現況(可 reuse 資源)

- `backend/services/fubon_client.py` — DMA login wrapper(singleton)
- `backend/services/rate_limiter.py` — Token bucket,5 req/s / 1 req/s historical
- `backend/services/ring_buffer.py` — Per-symbol tick deque(可擴展為 tick → live bar)
- `backend/services/supabase_client.py` — Supabase wrapper
- `backend/services/cdp.py` — Historical backfill 模式(daily OHLC)
- **沒有期貨支援**,需要從零建 futopt 相關 service
- **沒有本地檔案 cache**,所有資料目前都在 Supabase

---

## 待決議題(暫停在此)

**核心問題**:歷史 1m K 從哪來?

候選方案(已評估):

| 方案 | 評估 |
|---|---|
| FinMind | ❌ 沒 1m,只有 daily 免費 + tick 付費 |
| TAIFEX 逐筆 tick CSV(免費)+ 自組 1m K | 工程量大(寫 tick→bar ETL、處理巨量 CSV) |
| **期貨商歷史下載(富邦/群益)** | ✅ 使用者已選定方向,進行中 |
| 純富邦累積(等 1-3 個月) | 可作為備案 |
| 付費資料商(TEJ 等) | 留待未來 |

---

## 使用者下一步(對話暫停點)

使用者富邦期貨 + 群益期貨都有帳號。要去後台找「歷史 K 線匯出」:

### 富邦期貨
- 可從 **e 期貨**(網頁版)、**超贏**(下單軟體)、或 **e 行情** 找「歷史 K 線下載」或「圖表 → 匯出 CSV」
- 富邦客服 / 期貨營業員可協助索取

### 群益期貨
- **群益策略王 / 強棒贏家 / 群益贏家策略王(MultiCharts 整合版)** 都有 CSV 匯出功能
- 圖表畫面 → 右鍵 → 匯出歷史資料

### 建議下載參數
- **商品**:MXF(小台指,連續主力月 串接)
- **週期**:1 分 K(其他週期我們後端 resample 出來)
- **期間**:**至少近 2-3 年**(夠跑大部分波段回測,5 年更好)
- **日盤 + 夜盤**:如果系統有區分,兩段都要拿
- **欄位**:時間戳 / O / H / L / C / Volume(open_interest 有最好,沒有也可以)

### 拿到資料後

重啟對話時告訴 Claude:
- 「我拿到 MXF 歷史 1m K 了」
- 檔案位置 / 格式(CSV?Excel?)
- 大致筆數 / 覆蓋區間

然後我們從這份 spec 的「待決議題」續寫 B1(資料層 ETL 與 cache 結構)→ B2(DSL)→ B3(執行引擎)→ B4(API)→ B5(UI 結果展示)。

---

## 後續章節(暫停寫作,等資料來源確定後續)

- [ ] B1. 資料層(ETL + 本地 cache + N 分鐘 resample + 日夜盤對齊)
- [ ] B2. 策略 DSL(語法、支援指標、表達式運算子、UI builder)
- [ ] B3. 執行引擎(逐 bar 評估、隔夜部位、滑點/手續費、權益曲線)
- [ ] B4. API 介面(`/api/backtest/mxf`、`history-coverage`)
- [ ] B5. 前端結果展示(權益曲線、績效卡、交易明細、持倉時長分佈)
- [ ] B6. 開放議題(隔夜保證金、D K 日界線、跨夜盤對齊、優化器、A/B 比較)
