# 大盤指數(加權 + 櫃買)分時走勢 — 設計

- 日期:2026-06-09
- 狀態:設計定案,待寫實作計畫
- 範圍:前端新頁「大盤指數」+ Discord bot 指數查詢

---

## 1. 目標

在 app 加入台股兩大盤指數的即時分時走勢:

- **加權指數**(發行量加權股價指數,價格)= `IX0001`(exchange=TWSE, market=TSE)
- **櫃買指數**(上櫃)= `IX0043`(exchange=TPEx, market=OTC)

兩個代碼都已透過正在跑的 backend `/api/candles/{code}/intraday` 實測確認可用(2026-06-09)。**注意 `IR` 開頭是報酬指數(含息)、`IX` 開頭才是看盤的價格指數**;本功能一律用 `IX`。

進入方式:左側 sidebar 新增單一入口「大盤指數」。

---

## 2. 已確認的產品決策

1. **單一入口**:sidebar 一項「大盤指數」(不是兩項)。
2. **版面可切換**:進入後一個切換鈕在「左右並排」與「重疊 %」之間切,狀態記在 localStorage。
   - **左右並排**:加權、櫃買各一張獨立分時圖,各自顯示原始點數、各自 autofit。
   - **重疊 %**:兩條線畫在同一張圖,各自從「昨收 = 0%」起算漲跌 %,共用一個 % 軸;誰在上面 = 今天誰強。
3. **重疊圖配色**:加權 = 金(`#f0b429`)、櫃買 = 藍(`#3b82f6`),固定識別色、不隨漲跌變紅綠(否則兩線同色分不出)。
4. **標題數字**:大字現價(正體粗體、tabular-nums),漲跌幅(點數 + %)在下一行,顏色隨漲跌紅綠 — 與既有個股圖一致。
5. **Discord bot**:`p加權` / `p大盤` → 加權單圖、`p櫃買` → 櫃買單圖(`p大盤` 等同 `p加權`,台股習慣「大盤」=加權)。重疊 % 只在前端,不做進 bot。

---

## 3. 架構策略

沿用專案既有的「**共用 SVG 渲染層**」pattern:前端畫圖的 lib 同時被 Discord bot 用(現有個股圖 `frontend/src/lib/intraday-chart-svg.tsx` 就是這樣被 `bot/src/render.ts` 重用)。指數圖做成**新的**共用 lib,前端與 bot 都吃它。

### 為什麼指數圖「新寫」而不複用個股 `intraday-chart-svg`

讀 code 確認的兩個硬理由:

1. **會 NaN**:`computeIntradayGeometry` 無條件用 `candle.average`(VWAP)算 Y 軸範圍(`intraday-chart-svg.tsx:121,145-146`),但**指數 candle 沒有 `average` 欄位**,`Math.min(..., undefined)` = NaN → 整張圖壞。
2. **線會被壓平**:個股 Y 軸寫死最小範圍 ±10%(`refPrice * 0.9 ~ 1.1`,為了顯示 CDP/漲跌停),但指數一天波動常 < 3%,套 ±10% 走勢線會擠成中間一條近乎平線。指數需要 **autofit**(隨當日高低自動縮放)。

新寫的指數 lib 只用 `open/high/low/close` + `prevClose`,完全不碰 `average`,且 Y 軸 autofit;也**不動既有個股圖**(連帶不影響既有快照測試 `intraday-chart-svg.test.ts.snap`)。

---

## 4. 元件分解

### 4.1 前端 — 新增

| 檔案 | 職責 |
| --- | --- |
| `frontend/src/lib/index-symbols.ts` | 指數常數 + helper。`INDEX_SYMBOLS = [{code:'IX0001', name:'加權指數', aliases:['加權','大盤']}, {code:'IX0043', name:'櫃買指數', aliases:['櫃買','上櫃']}]`;`resolveIndexAlias(input): string\|null`、`isIndexCode(code): boolean`、`indexName(code): string\|null`。前端 + bot 共用。 |
| `frontend/src/lib/index-intraday-svg.tsx` | 指數單圖渲染(純函式 geometry + presentational `<IndexIntradayStatic>`)。autofit Y、昨收基準線、紅綠填色 + 紅綠主價線、今日高低 marker、固定 9:00–13:30 時間軸。不畫 VWAP/CDP/Camarilla/MA/量。 |
| `frontend/src/lib/index-overlay-svg.tsx` | 重疊 % 圖渲染(純函式 + presentational)。輸入兩個指數的 candles/prevClose/color;各算 `pct=(close-prevClose)/prevClose*100`;autofit % 軸 + 0% 基準線 + 兩條固定色線 + 線尾 % 標籤。 |
| `frontend/src/components/IndexIntradayChart.tsx` | 單一指數圖元件。`useIntradayCandles(code)` 取 candles/prevClose(沿用 30 秒輪詢);算現價/漲跌/漲跌%;render `index-intraday-svg` + hover crosshair + 標題(大字現價 A 風格)。 |
| `frontend/src/components/IndexOverlayChart.tsx` | 重疊 % 圖元件。同時 `useIntradayCandles('IX0001')` + `useIntradayCandles('IX0043')`;組兩條 % 線;render `index-overlay-svg` + hover(顯示游標時刻兩指數各自 %)。 |
| `frontend/src/pages/IndexBoard.tsx` | 頁面。版面切換鈕(`split` ⇄ `overlay`,localStorage key `tk:index:layout`);`split` → 兩個 `IndexIntradayChart` 左右排(窄螢幕 `md:` breakpoint 自動轉上下);`overlay` → `IndexOverlayChart`。 |

### 4.2 前端 — 修改

| 檔案 | 修改 |
| --- | --- |
| `frontend/src/components/Sidebar.tsx` | `Page` 型別加 `'index_board'`;`NAV_ITEMS` 加一筆「大盤指數」(icon 用兩條交疊折線的 SVG path,實作時微調)。 |
| `frontend/src/App.tsx` | `import { IndexBoard }`;加 `<div hidden={page !== 'index_board'}><IndexBoard /></div>`。 |

### 4.3 Discord bot — 修改

| 檔案 | 修改 |
| --- | --- |
| `bot/src/symbol.ts` | `parseSymbolCommand` 擴充:先試現有數字 regex,失敗再用 `resolveIndexAlias`(`frontend/src/lib/index-symbols`)比對 `p` 後面的中文別名,回對應代碼。回傳維持 `string\|null`(代碼);呼叫端用 `isIndexCode` 判斷走哪條路。 |
| `bot/src/reply.ts` | `loadSlow` 對指數代碼走精簡路徑:不抓 CDP/MA/quote(五檔),`flags` 全關,render 用 `renderIndexChartPng`;`composeReply` 對指數只回 ①精簡文字(現價/漲跌/開高低 + 時間)②走勢圖,不加五檔圖。 |
| `bot/src/render.ts` | 加 `renderIndexChartPng`(重用 `index-intraday-svg`,沿用既有 160% 字級 scale + 標題帶 + resvg 管線)。 |
| `bot/src/data.ts` | `getName` 對指數代碼先查 `index-symbols` 常數回中文名,不打查不到的 `/api/symbols`。 |

### 4.4 後端

**不改**。`/api/candles/{IX0001|IX0043}/intraday` 已支援;指數名稱走前端/bot 常數表,不動 `symbols` 表。

---

## 5. 指數圖渲染細節

### 5.1 單圖(`index-intraday-svg`)

- **Y 軸 autofit**:`yMin = min(min(lows), prevClose) * (1 - buf)`、`yMax = max(max(highs), prevClose) * (1 + buf)`,`buf ≈ 0.0015`。納入 `prevClose` 確保昨收基準線一定在可視範圍。盤中只有少數點時也成立。
- **昨收基準線**:水平虛線於 `prevClose`,標籤「昨收」。漲跌、紅綠填色都以此為基準(台股慣例:平盤上紅、下綠)。
- **主價線**:close 折線,用 clipPath 切上下兩段,基準上紅(`#e85a4f`)、下綠(`#7fc99a`),與既有個股圖同色。
- **今日高低**:用 `candle.high`/`candle.low` 取極值,marker + 數字,顏色隨漲跌。
- **時間軸**:固定 9:00 / 10:00 / 11:00 / 12:00 / 13:00 / 13:30(沿用 `intraday-time` 常數 `MARKET_OPEN_MIN` / `TRADING_MINUTES`)。
- **scale 參數**:沿用 `IntradayChartInput.scale`(網頁=1、bot=1.6)機制,字級/線寬相對畫布放大。

### 5.2 重疊 %(`index-overlay-svg`)

- 兩個指數各算 `pct[i] = (close[i] - prevClose_i) / prevClose_i * 100`。
- **Y 軸**:autofit 於兩線 pct 的 min/max(對稱 round 到易讀刻度,如 ±0.5% 級距),0% 基準線(虛線、略粗)。
- **兩條線**:加權金 `#f0b429`、櫃買藍 `#3b82f6`,固定色不隨漲跌。
- **標籤**:線尾顯示各指數最新 %;圖例「● 加權 +x% ● 櫃買 −y%」。
- 兩指數 candle 各依自己的 minute-of-day 畫(`scaleX` 吃分鐘),不需強制逐點對齊;某指數某分鐘缺值不影響另一條。

---

## 6. Discord bot 細節

- **指令**:`p加權`、`p大盤` → `IX0001`;`p櫃買` → `IX0043`;既有 `p<數字代號>` 不變。
- **別名比對**:整則訊息 `p` + 別名(精確比對 `index-symbols` 的 aliases),避免雜訊洗頻(沿用既有「整則才觸發」精神)。
- **指數回覆(精簡)**:
  - ① 文字:`代號 名稱`、現價、漲跌(點 + %)、開盤、最高、最低、資料時間。**不含** CDP / 均線 / VWAP / 量 / 五檔。
  - ② 走勢圖:`renderIndexChartPng`(autofit 單圖)。
  - 不發第三則五檔圖。
- **降級**:沿用既有 `safeRender` — 產圖失敗回 null 退純文字;盤前/無分時資料回單則純文字提示。

---

## 7. 即時更新

- 沿用 `useIntradayCandles` 的 **30 秒輪詢** `/api/candles`。
- 指數即時逐筆(WS `indices` channel)**本版不接**:現有 `fubon_ws` 訂的是個股 trades,指數走 `indices` channel 且在 DMA 模式可否訂閱未測。30 秒輪詢對大盤指數足夠。列為後續(見 §10)。
- `useIntradayCandles` 的 `onTick`(WS 更新最後一根)對指數不會觸發(沒訂指數 tick),不影響輪詢更新。

---

## 8. 錯誤處理 / 邊界

- **盤前 / 非交易日**:candles 過濾後為空 → 圖顯示「無資料」(沿用既有 empty 處理);bot 回單則純文字。
- **指數無 CDP/Camarilla/MA/quote**:前端指數圖不呼叫這些 API(flags 關);bot 指數路徑不呼叫。即使誤呼叫,後端會回錯、呼叫端已有 catch → null,不炸。
- **重疊圖某指數抓不到**:該線缺、另一條照畫,圖例對缺的標「—」。
- **bot 產圖失敗**:`safeRender` → null → 退純文字(沿用既有降級機制,不靜默吞掉)。

---

## 9. 測試計畫

遵循專案慣例:**前端無 hook 測試環境 → 把邏輯抽成純函式測**(見既有 `*.test.ts` 與 lib 拆法);bot 有 vitest 測試環境。

- `index-symbols`:`resolveIndexAlias`(加權/大盤/櫃買/上櫃 → 代碼、未知 → null)、`isIndexCode`、`indexName`。
- `index-intraday-svg`:autofit Y 範圍計算(波動小時 Y 範圍貼合當日高低、不過寬;prevClose 一定在範圍內)、空 candles 安全回傳。
- `index-overlay-svg`:% 計算正確、autofit % 軸、單指數缺值時另一條仍算。
- `bot/src/symbol.test.ts`:`p加權`/`p大盤`/`p櫃買` 解析為對應代碼、`p2330` 不受影響、`p亂碼` 回 null。
- `bot/src/reply.test.ts`:指數代碼走精簡路徑(回 2 則、不含五檔/CDP/MA)。

測試需編碼**意圖**(Rule 9):例如「指數波動小仍要看得出起伏」用「±0.3% 假資料 autofit 後 Y 範圍 < ±1%」這種會在邏輯改錯時失敗的斷言,而非只測「有回傳值」。

---

## 10. 非目標 / 後續

- WS `indices` channel 即時逐筆推送(本版用 30 秒輪詢)。
- 報酬指數 `IR0001` / `IR0043`(含息),非看盤值。
- 指數的 CDP / Camarilla / MA(指數無這些資料源 / 未驗證)。
- bot 的重疊 % 圖(`p大盤` = 加權單圖)。
- 第三個以上指數(架構用常數表預留,本版只加權 + 櫃買)。
- 指數量能(DMA 模式 `volume=0`,本版不顯示量)。
