# 分時走勢圖 X 軸固定到 13:30

**日期**:2026-05-22
**狀態**:Design — 待 user 審核
**檔案範圍**:`frontend/src/components/IntradayChart.tsx`(單檔)

## 動機

目前 `IntradayChart` 的 X 軸是 index-based:`scaleX(i) = PAD_L + (i / (N-1)) * xRange`。
N 是當下擁有的 candle 數。後果:

- 9:05 打開時,5 根 candle 拉伸佔滿整個 chart 寬度,視覺嚴重失真
- 隨著時間推進,X 軸刻度持續位移、走勢線比例不斷被壓縮
- X 軸標籤是按 candle 數量等分(0/25/50/75/100),交易中顯示 9:05 / 9:08 / 9:11 / 9:14 / 9:17 之類的時間,沒參考價值

需求:讓 X 軸**始終**顯示 9:00 → 13:30 的完整交易窗,不管現在幾點。

## 設計

### X 軸:index-based → time-based

把 X 軸從「候選 candle 數量」改成「分鐘 of day」決定位置。

```
MARKET_OPEN_MIN  = 540   // 9:00
MARKET_CLOSE_MIN = 810   // 13:30
TRADING_MINUTES  = 270

scaleX(minuteOfDay) = PAD_L + ((minuteOfDay - 540) / 270) * xRange
```

每根 candle 用自己的 `date` (台北時間)算出 `minuteOfDay`,代入 `scaleX`。

**Pre-market 過濾**:Fubon `intraday.candles` 可能回試撮(8:30-9:00)的 candle(目前後端只在訊號評估擋,chart route 沒擋)。進 `useMemo` 前先 client-side filter:`540 ≤ minuteOfDay(c.date) ≤ 810`,擋下盤前 / 盤後資料(13:30 含 — 收盤集合競價若打出 candle 仍保留)。

### X 軸標籤

從目前的 5 個比例刻度(`[0, 0.25, 0.5, 0.75, 1]` × candle index)改成 6 個**固定分鐘點**:

| 分鐘 | 顯示 |
|---|---|
| 540 | 9:00 |
| 600 | 10:00 |
| 660 | 11:00 |
| 720 | 12:00 |
| 780 | 13:00 |
| 810 | 13:30 |

直接從常數陣列 render,不再從 candle 抓時間。

### 走勢相關元素行為

| 元素 | 行為 |
|---|---|
| 主價線(漲紅跌綠) | 停在最新 candle,右邊不延伸 |
| VWAP 線 | 同上 |
| 紅綠 fill 區(走勢線 ↔ baseline) | 同上 |
| 成交量 bar | 每根用自身時間定位;bar 寬 = `(xRange / TRADING_MINUTES) * 0.7`(約 1.8 px) |
| 今日 High / Low marker | 邏輯不變,位置改用 `scaleX(該 candle 的 minuteOfDay)` |
| CDP / MA / VWAP label(右邊 margin) | **不變** — 水平線跨整個 chart |
| Y 軸格線 + 基準線 | **不變** |

### 未來時間區的視覺處理

交易中右邊「尚未到」的時間區:**純空白**。
- 只有 X / Y 軸格線
- 不加「現在」垂直線
- 不加灰底 / 條紋

### Hover 行為

```
mouse svgX → 換算回 minuteOfDay
  → 若 minuteOfDay > 最新 candle 的 minuteOfDay  → 不顯示 crosshair
  → 否則 snap 到時間最接近的 candle,顯示 crosshair
```

避免 cursor 在未來區、crosshair 卻 stick 在最新 candle 拉很遠的違和感。

## 實作範圍

**只動** `frontend/src/components/IntradayChart.tsx`:

1. 頂部加常數 `MARKET_OPEN_MIN` / `MARKET_CLOSE_MIN` / `TRADING_MINUTES`
2. 加 helper:`minuteOfDay(iso: string): number`(從 ISO 字串抓台北時間 hour*60+minute)
3. `useMemo` 入口先 filter candles 到 9:00-13:30 窗內
4. 改寫 `scaleX` — 從 `(i) => ...` 改成 `(minuteOfDay) => ...`(call site 都改成傳 minuteOfDay)
5. 改 X label render(原 #468-479 行):從等分比例 → 固定 6 個分鐘點
6. 改 `handleMouseMove`(原 #191-204):svgX → minuteOfDay → 找最近 candle,過了最新 candle 就 `setHover(null)`
7. 改 volume bar 寬:從 `xRange / candles.length` → `xRange / TRADING_MINUTES`

**不動**:
- 後端 (`backend/routes/candles.py`)
- API shape
- 任何其他 component
- Y 軸邏輯、CDP / MA / VWAP 線、紅綠 fill 邏輯、今日 High/Low marker 邏輯

## 邊界情況

- **開盤前打開 chart**(< 9:00):若 backend 回空 array,維持目前「載入中…」狀態 — 不畫空 frame
- **週末 / 假日**:同上
- **9:01 ~ 9:02 早盤**:只有 1-2 根 candle,正確位於 X 軸最左側;hover 過了最新 candle 就消失
- **試撮 candle 漏出**(8:30-9:00 從 Fubon 回來):被 client filter 擋掉,不畫
- **盤後 candle 漏出**(13:31+,理論上不該有但 Fubon 行為未驗證):被 client filter 擋掉

## 非目標

- 不在右側畫「現在」標記
- 不顯示盤前試撮 candle
- 不改後端 / API
- 不引入 Y 軸縮放或其他互動變更
- 不重構成獨立 hook / util(改動侷限在現有 component 內)

## 驗收標準

人工開 chart 場景:

- 早盤 9:05:X 軸顯示完整 9-13:30,走勢線只佔最左 1/54
- 中午 12:00:X 軸顯示完整 9-13:30,走勢線佔約 2/3
- 收盤後 13:30+:X 軸顯示完整 9-13:30,走勢線填滿
- Hover 在未來時間區(右側空白)→ 無 crosshair
- Hover 在已成交區 → crosshair snap 到對應 candle
- 切換不同股票(symbol) — X 軸結構穩定不會變

## 後續(本 spec 範圍外)

無依賴後續工作。實作完成可直接 merge。
