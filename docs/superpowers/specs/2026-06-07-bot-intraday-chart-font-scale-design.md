# Discord Bot 分時圖字級放大(feed 可讀)— 設計文件

**日期**:2026-06-07
**範圍**:共用畫圖層 `frontend/src/lib/intraday-chart-svg.tsx` 加 `scale` 參數 + bot 端 `bot/src/render.ts` 套用;網頁 `frontend/src/components/IntradayChart.tsx` 維持預設(不變)
**狀態**:設計完成,brainstorm 決策已由 user 拍板
**相關**:[Discord bot 分時推播微調 design](./2026-06-05-discord-bot-tweaks-design.md)、[個股查詢 bot design](./2026-06-05-discord-stock-bot-design.md)

---

## 1. 目的

bot 推到 Discord 的分時圖,在訊息串(欄寬 ~450px,Discord 硬上限)裡字太小看不清。把**圖內所有文字放大到 160%**、bot **標題帶放大到 150%**,並確保放大後**不超出圖片邊界、標籤不互相重疊**。

**關鍵約束**:畫圖層是網頁與 bot 共用。本案要求**只放大 bot 圖,網頁分時圖逐像素不變**。

---

## 2. 決策(user 拍板)

| # | 項目 | 決策 |
|---|---|---|
| 1 | 目標情境 | **A — feed 裡不點開就要看清楚**(非「點開更大」)。所以動的是「元素相對畫布的佔比」,不是畫布像素或 zoom 倍率 |
| 2 | 圖內字級 | **160%**(15→24px),滑桿實測選定 |
| 3 | bot 標題帶字級 | **150%**(22→33px)—— 標題帶空間最緊,150% 最穩 |
| 4 | 實作方式 | **方案 B:加 `scale` 參數**(預設 1),網頁傳 1.0、bot 傳 1.6。隔離,網頁零風險 |
| 5 | 防超界 | 加大留白 + 拉開碰撞間距 + 標題帶加高 + 長名截斷;**不刪格線/CDP 線、不降資訊密度** |

---

## 3. 設計:`scale` 參數機制(方案 B)

在 `IntradayChartInput` 加 `scale?: number`(預設 `1`)。`computeIntradayGeometry` 與 `IntradayChartStatic` 都讀它 —— **一處傳入、兩處生效**。

```diff
  export interface IntradayChartInput {
    candles: IntradayCandle[];
    prevClose: number | null;
    cdp: CdpLevels | null;
    camarilla: CamarillaLevels | null;
    ma: MaLevels | null;
    flags: ChartFlags;
    theme?: ChartTheme;
+   // 圖內元素(字級/留白/間距/線寬)相對畫布的放大倍率。網頁=1(預設、不變),bot=1.6。
+   // 畫布總尺寸 CHART_W/CHART_H/VOL_H 不隨 scale 變 —— 只放大「裡面的內容」,即字佔比變大。
+   scale?: number;
  }
```

### 3.1 隨 `scale` 放大 vs 固定

| 隨 scale ×(內容元素) | 現在 → scale 1.6 |
|---|---|
| 圖內 label 字級(Y軸/CDP·均線/高低點/X軸時間) | 15 → 24 |
| 量圖 VOL / 數字字級 | 13 → ~21 |
| 左右留白 `PAD_L` / `PAD_R` | 56 → ~90 |
| 上下留白 `PAD_T` / `PAD_B` | 12 / 28 → ~19 / ~45 |
| label 碰撞撐開間距(`resolveCollisions` 第 2 引數) | 20 → 32 |
| 走勢線 / VWAP / CDP·Cam·MA 線 / 高低點 marker 線寬·半徑 | ×1.6 |

| **不**隨 scale 變(畫布尺寸固定) | 值 |
|---|---|
| `CHART_W` / `CHART_H` / `VOL_H` / `VOL_GAP` / `TOTAL_H` | 不動 |
| 背景 Y 軸格線線寬(0.5 / 0.8) | 維持原細(背景不搶眼) |

**繪圖區後果**:左右留白 56→90 → 主圖繪圖區寬 708→~640px(窄約 10%),走勢線稍擠,資訊量不減。可接受(user 已同意取捨)。

**⚠️ 邊界 — 最長價格 label(防超界的真正關鍵)**:右側 CDP/均線 label 最長是「4 位數價格 + 小數 + `*`」,如高價股 `1085.0*`(7 字元);scale 1.6 下 24px 字約需 ~95px,而 `56×1.6 ≈ 90px` **不夠**。故 `padR = max(56 × scale, 最長 label 寬 + 右側 offset)`;左側 Y 軸 label(同為價格、無 `*`)同理校準 `padL`。**plan 階段以高價股最長 label 實測定值,不可只靠固定係數 ×scale 帶過。**(註:此超界在現況 scale=1 高價股就已逼近邊緣,本案順手以「最長 label」為準校準,但僅作用於 scale>1 的 bot 路徑,網頁 scale=1 維持現值不動。)

### 3.2 ⚠️ 實作正確性關鍵:effective padding 單一來源

目前 `IntradayChartStatic` 內部**直接引用 module 常數** `PAD_L`/`PAD_R`/`CHART_H` 來畫線、定位 label(例:格線 `x1: PAD_L, x2: CHART_W - PAD_R`、X 軸時間 `y: CHART_H - 8`)。而 `computeIntradayGeometry` 的 `scaleX/scaleY` 也用 padding。

**若只在 geometry 把 padding ×scale、卻漏改 `IntradayChartStatic` 裡直接用常數的地方 → 刻度(scaleX)與繪製線錯位。** 這是本方案最容易踩的雷。

**做法**:`computeIntradayGeometry` 算出 effective padding(`padL = PAD_L * scale` 等),**放進回傳的 `IntradayGeometry`**;`IntradayChartStatic` 一律從 `geometry` 讀 `padL/padR/padT/padB`,**不再直接用 module 常數**。module 常數 `PAD_L` 等保留作為 base(scale=1 即原值),網頁 `IntradayChart.tsx` 的 crosshair 仍可直接用(它跑 scale=1,與 geometry 一致)。

```diff
  export interface IntradayGeometry {
    yMin: number; yMax: number;
    scaleX: (m: number) => number;
    scaleY: (v: number) => number;
+   padL: number; padR: number; padT: number; padB: number;  // effective(已 ×scale)
+   fontScale: number;  // = scale,供 IntradayChartStatic 算 fontSize / strokeWidth
    ...
  }
```

> `computeIntradayGeometry` 在 `filteredCandles.length === 0` 的 early-return 物件,也要補齊 `padL/padR/padT/padB/fontScale`,否則 TS 編譯失敗。

### 3.3 fontSize / 線寬

`IntradayChartStatic` 內所有寫死的 `fontSize: 15` → `fontSize: 15 * fontScale`、`13` → `13 * fontScale`;走勢線/VWAP/CDP/Cam/MA `strokeWidth` 與高低點 marker `r` 同乘 `fontScale`。背景格線線寬不乘。

---

## 4. bot 端套用(`bot/src/render.ts`,bot-only)

- `renderChartPng` 組 `input` 時帶 `scale: 1.6`(傳進 `computeIntradayGeometry` + `IntradayChartStatic`)。
- **標題帶**(SVG 內,非共用層):
  - `TITLE_H` 44 → 58。
  - 兩處標題文字 `fontSize` 22 → 33。
  - `totalH = chartH + TITLE_H` 自動跟著變(`chartH` = `TOTAL_H` 或 `CHART_H`,不變;height 只因 TITLE_H 變)。viewBox/width 仍以 `CHART_W` 為寬。
  - **超長股名截斷**:`${symbol} ${name}` 過長會撞右側現價 → 名稱超過上限字數截斷補 `…`(上限依標題帶左側可用寬與 33px 字寬估算,plan 階段定數)。
- `zoom` 倍率不動(點開清晰度維持)。

網頁 `IntradayChart.tsx`:**不傳 `scale`**(吃預設 1)→ 輸出與現在逐像素相同。crosshair 那段不動。

---

## 5. 改動範圍

| 檔案 | 改什麼 | 影響面 |
|---|---|---|
| `frontend/src/lib/intraday-chart-svg.tsx` | 加 `scale`;geometry 回傳 effective padding + fontScale;`IntradayChartStatic` 改用 geometry 的 pad、fontSize/線寬 ×fontScale | 共用層(網頁 scale=1 不變) |
| `bot/src/render.ts` | 傳 `scale: 1.6`;TITLE_H 58 / 標題字 33 / 長名截斷 | bot-only |
| `frontend/src/components/IntradayChart.tsx` | 不改(預設 scale=1) | — |

---

## 6. 測試計畫

| 層 | 檔案 | 重點 |
|---|---|---|
| 前端回歸 | `frontend/src/lib/intraday-chart-svg.test.ts` | **scale=1(預設)snapshot 不變** —— 證明網頁零影響(不可 `-u` 蓋掉) |
| 前端新增 | 同上 | scale=1.6 時 geometry `padL/padR` ≈ 56/56 ×1.6、`fontScale===1.6`;render 出的 SVG 字級反映放大 |
| 前端意圖(Rule 9) | 同上 | **放大後不超界**:用**高價股案例**(prevClose 4 位數,如 1085)產生最長 label `1085.0*`,斷言 scale=1.6 下右側 label(錨點 + 字寬)≤ `CHART_W`、左側 Y 軸 label ≥ 0。encode「字不爆出圖片」這個本案核心意圖 |
| bot 單元 | `bot/src/render.test.ts` | scale=1.6 render 不丟例外;viewBox height = `TOTAL_H + 58`;標題帶字級 33;**超長股名截斷含 `…` 且長度受限** |
| bot 回歸 | 同上 | 既有降級測試(產圖失敗→null 等)不變 |
| 視覺(手動) | 產一張 PNG | feed 寬度下字夠大、無超界、無重疊 |

---

## 7. 待驗(需 user / 環境)

1. **盤中實機**(台股 9:00–13:30):`p代號` 查一檔,看 Discord feed 裡分時圖字夠大、CDP `*`/時間/價格都不超界、不重疊。
2. **肉眼確認網頁分時圖沒變**:開網頁分時圖,對照放大前後應一致(scale=1 回歸的人工複核)。

---

## 8. 不在本案 scope

- **五檔圖**(`frontend/src/lib/quote-book-svg.tsx` / `renderQuotePng`)—— user 指定只調分時走勢圖;五檔要放大另案。
- 改 `zoom` 倍率 / 點開大圖解析度(情境 A 不需要)。
- 改畫布長寬比、加高圖(高度上一案已調過)。
- 任何網頁外觀變更。
