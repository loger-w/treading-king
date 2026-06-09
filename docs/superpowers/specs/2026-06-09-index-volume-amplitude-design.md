# 大盤指數 — 振幅 + 成交值(量能副圖)設計

- 日期:2026-06-09
- 狀態:設計定案,待寫實作計畫
- 範圍:在既有「大盤指數」單圖(並排 view)+ Discord bot 指數查詢,加上 **振幅** 與 **成交值**,以「數字 + 分時量能副圖」呈現
- 前置:延續 [`2026-06-09-index-intraday-design.md`](./2026-06-09-index-intraday-design.md)

---

## 1. 目標

指數目前只有價格走勢線(現價 / 漲跌 / 漲跌%)。本功能補上兩個看盤常用維度:

- **振幅**:當日高低相對昨收的擺動幅度。
- **成交值**:大盤量能(以「分時量能副圖」+ 全日總額數字呈現)。

全部資料**來自已抓回的 `candles`**(`/api/candles/{code}/intraday`),**零後端改動**。

---

## 2. 關鍵事實(2026-06-09 實測,直接打整包 quote/candles 確認)

1. **指數 candle 的 `volume` 不是張數,是「每分鐘成交值(元)」**。實證:加權各分鐘 `volume` 加總 = `1,151,930,775,120` = 同一支 quote 的 `total.tradeValue`(1.15 兆元)。⇒ 量副圖、總額一律以「**成交值(億元)**」表示,**不可**標成「量(張)」。
2. **振幅可純算**:`(今日高 − 今日低) / 昨收 × 100`。實證加權 = `(44821.71 − 43687.62)/43502.78 = 2.61%`,與 quote 的 `amplitude=2.61` 一致 ⇒ 不必為了 amplitude 去打 quote。
3. 指數 candle **沒有 `average`(VWAP)**、quote 沒有 `avgPrice` ⇒ 不做均價線(本來就沒有)。

---

## 3. 已確認的產品決策

1. **量能呈現 = 數字 + 圖下量能副圖**(非僅數字)。
2. 副圖**比照既有個股圖的成交量子圖**(`intraday-chart-svg.tsx` §10),沿用其已 export 的版面常數與幾何,只換單位與標籤。
3. **單位一律「億元」**:副圖每根 = 該分鐘成交值;pane 右上標當日單分鐘最大值(億);header/bot 顯示全日總成交值(億)。
4. **振幅**:`(高−低)/昨收`,本地算(= API amplitude),不打 quote。
5. **不做(YAGNI,本次明確排除):**
   - 階段二「後端透傳 `quote.total`」→ 全場成交量(張)/筆數、委買 vs 委賣力道(`bidVolume/askVolume`)。需動後端,邊際價值低,延後。
   - 漲跌 / 漲跌% 改讀 API 的 `change/changePercent`(現為本地算,值相同,畫面不變,不值得動)。
   - **重疊 % 圖(`IndexOverlayChart`)不加量副圖**:雙線共軸比較,塞單檔量沒有意義。量副圖只在「並排」單圖與 bot 單圖。
   - 均價線。

---

## 4. 架構策略

沿用「共用 SVG 渲染層」:`frontend/src/lib/index-intraday-svg.tsx` 同時被前端 `IndexIntradayChart` 與 bot `renderIndexChartPng` 用。**改這一份,網頁與 bot 兩邊同時生效。**

量副圖**直接對齊個股圖既有實作**(`intraday-chart-svg.tsx`):

- 版面常數已 export 可直接 import:`VOL_GAP=4`、`VOL_H=144`、`VOL_PAD_T=6`、`TOTAL_H = CHART_H + VOL_GAP + VOL_H`(= 600+4+144 = 748)。
- 量條幾何邏輯照抄:`maxVolume = max(1, …volume)`、`scaleVolY(v)`(pane 範圍 `[CHART_H+VOL_GAP+VOL_PAD_T, TOTAL_H]`)、`volBarW = slotW*0.7`、bar 顏色 `close>open?紅:close<open?綠:灰`。
- **唯二差異**:(a) 數值格式器用新的 `fmtIndexVol`(億元),不用個股的 `formatVolume`(M/K 股數);(b) pane 左上標籤「成交值(億)」,不是「Vol」。

價格區幾何(`computeIndexGeometry` 現有部分)**完全不動** —— 量 pane 加在 `CHART_H` 之下的新增高度,價格區的 `scaleY`/autofit 不受影響,降到最低風險。

---

## 5. 元件分解

### 5.1 `frontend/src/lib/index-intraday-svg.tsx`(核心,改最多)

| 改動 | 內容 |
| --- | --- |
| import | 加 `VOL_GAP, VOL_H, VOL_PAD_T, TOTAL_H`(自 `intraday-chart-svg`) |
| `fmtIndexVol(valueYuan)` | 新增 export。`元 → 億`(`/1e8`)、四捨五入整數、千分位(沿用 `fmtIndex` 同款 regex,不依賴 ICU)。回傳如 `"715億"` / `"11,519億"` |
| `IndexGeometry` | 介面加 `maxVolume: number; scaleVolY: (v:number)=>number; volBarW: number`;空資料 return 也補這三個(`0 / ()=>0 / 0`) |
| `computeIndexGeometry` | 算 `maxVolume / scaleVolY / volBarW`(照抄個股邏輯) |
| `IndexIntradayStatic` | 加「量能副圖」group:分隔線、pane 左上「成交值(億)」、右上 `fmtIndexVol(maxVolume)`、每分鐘 bar(顏色 `close vs open`) |

### 5.2 `frontend/src/components/IndexIntradayChart.tsx`(網頁)

| 改動 | 內容 |
| --- | --- |
| viewBox 高度 | `0 0 CHART_W CHART_H` → `0 0 CHART_W TOTAL_H`(容納量 pane) |
| header 數字 | 漲跌行下方加一行(中性色 `text-ink-muted`):`振幅 {amp}%　成交值 {fmtIndexVol(totalVal)}` |
| 計算 | `amp = baseline ? (geometry.todayHigh - geometry.todayLow)/baseline*100 : null`;`totalVal = sum(filteredCandles.volume)`;無資料/無昨收 → 顯 `—` |
| hover | 不動(十字線仍只在價格區 `padT…CHART_H-padB`) |

### 5.3 `bot/src/reply.ts` — `loadSlowIndex`

| 改動 | 內容 |
| --- | --- |
| 回傳 | 加 `amplitude: baseline ? (high-low)/baseline*100 : null`、`volume: intraday.reduce((n,c)=>n+c.volume,0)`(比照個股 `loadSlow` 已有的 volume 加總) |

### 5.4 `bot/src/embed.ts` — `buildIndexReply`

| 改動 | 內容 |
| --- | --- |
| args | 加 `amplitude: number\|null; volume: number` |
| 欄位 | 既有「開 / 高 / 低」inline field 之後,加一個 inline field「振幅 / 成交值」,value = `{amp}% / {fmtIndexVol(volume)}`(`amp` null → `—`) |

### 5.5 `bot/src/render.ts` — `renderIndexChartPng`

| 改動 | 內容 |
| --- | --- |
| PNG 高度 | 輸出尺寸由 `CHART_H` 改 `TOTAL_H`(比照個股 `renderChartPng` 用 `TOTAL_H` 的寫法;實作前讀 `render.ts` 對齊) |

### 5.6 `bot/src/reply.ts` — `composeReply`(指數分支)

`buildIndexReply({…, amplitude: s.amplitude, volume: s.volume})` 多帶兩個參數。

---

## 6. 邊界與降級

- **盤前 / 無分時資料**:`filteredCandles` 空 → 量 pane 不畫(沿用既有空資料 return);振幅 / 成交值顯 `—`。
- **`prevClose = null`**:振幅顯 `—`(振幅以昨收為分母,無昨收不硬算)。成交值不受影響(只看 volume 加總)。
- **volume 全 0**(極端):`maxVolume = max(1, …)` 已防除零;bar 高度 0、總額 `0億`。
- **resvg(bot)**:`fmtIndexVol` 不用 `toLocaleString`/ICU,純 regex 千分位,與 `fmtIndex` 一致。

---

## 7. 測試計畫(Rule 9 — 驗意圖,不只驗行為)

| 測試 | 釘住的「為什麼」 |
| --- | --- |
| `fmtIndexVol` 單測 | **指數量是成交值(元),單位是億**:`fmtIndexVol(1151930775120)` → `"11,519億"`、`fmtIndexVol(71496161960)` → `"715億"`、`0` → `"0億"`。防有人誤當張數或漏 `/1e8`。 |
| 振幅單測(抽純函式) | 振幅 = `(高−低)/昨收`,以 2026-06-09 加權實值驗 `≈2.61`(= API amplitude);`prevClose=null` → `null`。防改成用開盤或當日收當分母。 |
| `computeIndexGeometry` | `maxVolume`/`scaleVolY` 對非空 candles 正確;空 candles 三欄安全值。 |
| `index-intraday-svg` snapshot | 更新(量 pane 進 SVG 輸出);人工檢視確認 pane 標「成交值(億)」非「Vol」、bar 紅綠正確。 |
| `bot/src/reply.test.ts` | `loadSlowIndex` 回傳含 `amplitude`/`volume`;`buildIndexReply` 文字含振幅、成交值欄位。 |

既有測試:確認不動到 `intraday-chart-svg.test.ts`(個股圖未改)。`__snapshots__/intraday-chart-svg.test.ts.snap` 目前 git 已有未提交改動(非本次產生),**不在本次範圍**,不碰。

---

## 8. 不在本次範圍(備忘)

階段二(後端 `quote.total` 透傳:全場成交量張數 / 筆數 / 委買賣力道)若日後要做,`/api/candles` route 已在抓 quote(為了 `prevClose`),只需把整包 `total`(`tradeVolume`/`transaction`/`bidVolume`/`askVolume`)一併回傳即可,不必開新端點。實測欄位:`total.tradeVolume`(張)、`total.tradeValue`(元)、`total.bidVolume`/`askVolume`(全場累計委買 / 委賣量)。
