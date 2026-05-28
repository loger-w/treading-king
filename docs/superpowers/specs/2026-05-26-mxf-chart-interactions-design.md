# MXF 分時圖互動 — Zoom / 時間軸 / 即時報價 Header 設計文件

**日期**：2026-05-26
**位置**：`frontend/src/components/MXFIntradayChart.tsx`
**狀態**：設計完成，待寫實作計畫
**相關**：[MXF intraday chart 第一版設計](./2026-05-24-mxf-intraday-chart-design.md)

---

## 1. 目的

第一版 MXF 即時分時走勢圖已上 (`MXFIntradayChart` + toolbar + crosshair)，但缺三個關鍵互動：

1. **可縮放** — 1m TF 一天約 1500 根 candle，全部塮進 1400px 看不清，user 想要 zoom in 看細節、zoom out 看整體
2. **時間軸** — 目前只有 session gap 虛線，沒任何時間 label，user 看不出「現在是 10:00 還是 12:00」
3. **即時報價 header** — 左上角缺 symbol + 大字現價 + 漲跌幅；股票版 (`IntradayChart`) 已有，期貨版要對齊

---

## 2. 範圍

### 2.1 本次包含

- **Scroll-wheel zoom** + drag-to-pan，以滑鼠位置為 anchor
- **預設視窗** = 最近 N 根 candle (N 依「每根至少 6px」推導)
- **Zoom 邊界** = 最小 5 根可見 (in) / candle 寬不得 < 6px (out)
- **WS push 新 candle** 行為 = 貼右自動跟、否則凍結
- **時間軸 HH:MM label** 加上 adaptive interval (5/15/30/60/120/240 分鐘自動選)
- **Session boundary 上方** 標 `MM/DD 日盤/夜盤`
- **左上角 header** = symbol + 44px italic price + ▲/▾ change/pct (對齊股票版風格、無書籤按鈕)
- **漲跌幅基準** = 今日日盤開盤價 (08:45 第一根 candle.open)
- **既有 feature 適配** = MA / VWAP / VOL / 高低 marker / Crosshair / session gap 虛線都要在 sliced viewRange 下正確運作

### 2.2 不在這版 scope

- **前一交易日結算價** 作為漲跌基準 — 期貨業界標準但需 backend 加 endpoint，留待後續
- **Touch / pinch zoom** — 目前以 desktop user 為主、行動裝置體驗下版再做
- **Keyboard shortcut** (`+/-/0` 之類) — desktop 鍵盤捷徑不在第一版
- **Drag range slider / mini-map** — scroll wheel + pan 已足夠
- **歷史資料分頁載入** — 假設 `useMXFCandles` 回的範圍已涵蓋今日所有 candle (含夜盤)

---

## 3. 需求決議 (brainstorm 確認)

| 項目 | 決議 |
|---|---|
| Zoom 互動 | 滑鼠滾輪 |
| 預設視窗 | 最近 N 根 candle (N = floor(innerW / 6px)) |
| 最小 candle 寬度 | 6px (後續可調) |
| Pan 互動 | mousedown + drag (drag 中暫時隱藏 crosshair) |
| WS 新 candle | 貼右自動跟、否則凍結 viewRange |
| 時間軸 label 密度 | 自動 adaptive (5/15/30/60/120/240 min) |
| Session boundary | Gap 右側上方加 `MM/DD 日盤/夜盤` 小字 |
| Header 風格 | 對齊股票版 (22px serif symbol + 44px italic price + 18px change/pct) |
| 漲跌基準 | 今日日盤開盤價 (08:45 第一根 candle.open) |
| 架構方案 | Slice by index — 餵 sliced candles 給現有 SVG 子元件、95% render code 不動 |

---

## 4. 整體架構

採 **Approach A — Slice by index**：

```
state: viewRange = { startIdx: number; endIdx: number } | null
       ↓
visibleCandles = candles.slice(viewRange.startIdx, viewRange.endIdx + 1)
       ↓
所有 SVG render fn (CandlestickSeries / LineSeries / MALine / VolumeSubChart /
                    sessionBoundaries / inferSessions / handleMouseMove crosshair) 
都吃 visibleCandles
```

**選 A 不選 zoom-transform 的原因**：
- chart-svg 子元件 stateless、直接餵 slice 就能用、現有測試不必改
- 1m TF 1500+ 根 candle 全部 render 有 SVG perf 風險、slice 後一次最多 ~150 根
- viewRange 內 sessions 從 sliced 重算 (`inferSessions`) — 視窗只看到日盤就只一段 session、跨 session 就兩段、邏輯天然正確

---

## 5. Zoom 資料狀態 + 互動

### 5.1 State

```ts
const [viewRange, setViewRange] = useState<{ startIdx: number; endIdx: number } | null>(null);
const [isDragging, setIsDragging] = useState(false);
```

`viewRange = null` 代表未初始化 (loading / candles 空)，render 時 fallback 顯示 loading。

### 5.2 初始化

```ts
useEffect(() => {
  if (candles.length === 0) {
    setViewRange(null);
    return;
  }
  if (viewRange === null) {
    const maxVisible = Math.floor(innerW / MIN_CANDLE_PX);  // 6px
    const startIdx = Math.max(0, candles.length - maxVisible);
    const endIdx = candles.length - 1;
    setViewRange({ startIdx, endIdx });
  }
}, [candles.length === 0, innerW]);
```

切 timeframe 時 `useMXFCandles` 會清空 `candles` 再重 fetch，導致 viewRange 暫時指到不存在 idx — 上述 effect 在 `candles.length === 0` 時 reset null，重 init。

### 5.3 WS push 新 candle

需要 `useRef<number>` 追蹤上一輪 `candles.length` 才能判斷是否剛 push：

```ts
const prevLenRef = useRef(candles.length);

useEffect(() => {
  if (!viewRange || candles.length === 0) {
    prevLenRef.current = candles.length;
    return;
  }
  const prevLen = prevLenRef.current;
  const anchoredRight = viewRange.endIdx === prevLen - 1;
  if (anchoredRight && candles.length > prevLen) {
    const shift = candles.length - prevLen;
    setViewRange({ startIdx: viewRange.startIdx + shift, endIdx: viewRange.endIdx + shift });
  }
  // 不貼右 → viewRange 不動，新 candle 在視窗外
  prevLenRef.current = candles.length;
}, [candles.length]);
```

### 5.4 Scroll wheel zoom

```ts
function handleWheel(e: React.WheelEvent<SVGSVGElement>) {
  e.preventDefault();
  if (!viewRange) return;
  const rect = e.currentTarget.getBoundingClientRect();
  const svgX = ((e.clientX - rect.left) / rect.width) * CHART_W;
  if (svgX < PAD_L || svgX > CHART_W - PAD_R) return;  // 不在 chart area 外

  const visible = viewRange.endIdx - viewRange.startIdx + 1;
  const factor = e.deltaY > 0 ? 1.15 : 1 / 1.15;
  let newVisible = Math.round(visible * factor);
  // Clamp
  newVisible = Math.max(5, newVisible);
  const maxVisible = Math.floor(innerW / MIN_CANDLE_PX);
  newVisible = Math.min(maxVisible, newVisible, candles.length);

  // Anchor: 滑鼠下那根 candle 保持原位
  const mouseRatio = (svgX - PAD_L) / innerW;
  const anchorIdx = viewRange.startIdx + Math.round(mouseRatio * (visible - 1));
  let newStart = Math.round(anchorIdx - mouseRatio * (newVisible - 1));
  newStart = Math.max(0, Math.min(candles.length - newVisible, newStart));
  setViewRange({ startIdx: newStart, endIdx: newStart + newVisible - 1 });
}
```

### 5.5 Drag-to-pan

```ts
function handleMouseDown(e: React.MouseEvent<SVGSVGElement>) {
  setIsDragging(true);
  setHover(null);  // 暫時隱藏 crosshair
  dragStartX.current = e.clientX;
  dragStartRange.current = viewRange;
}

function handleMouseMove(e) {
  if (isDragging) {
    const dx = e.clientX - dragStartX.current;
    const rect = e.currentTarget.getBoundingClientRect();
    const pxPerCandle = innerW / (dragStartRange.current.endIdx - dragStartRange.current.startIdx + 1) * (rect.width / CHART_W);
    const deltaIdx = -Math.round(dx / pxPerCandle);  // 向右拖 = 看更早的歷史 (start/end 減)
    let newStart = dragStartRange.current.startIdx + deltaIdx;
    const size = dragStartRange.current.endIdx - dragStartRange.current.startIdx + 1;
    newStart = Math.max(0, Math.min(candles.length - size, newStart));
    setViewRange({ startIdx: newStart, endIdx: newStart + size - 1 });
    return;
  }
  // 不在 drag → crosshair hover (現有邏輯)
}

function handleMouseUp() { setIsDragging(false); }
function handleMouseLeave() { setIsDragging(false); setHover(null); }
```

### 5.6 Cursor 狀態

- Default = `cursor-crosshair`
- Drag 中 = `cursor-grabbing` (動態 className)

---

## 6. 時間軸 (adaptive)

### 6.1 Label 位置與樣式

- y = `CHART_H - PAD_B + 14`
- text-anchor middle
- 格式：`HH:MM` (本地時區，跟 crosshair 時間 label 一致)
- className：`fill-ink-dim text-[11px] tabular-nums`

### 6.2 Interval 自動選擇

```ts
const INTERVALS_MIN = [5, 15, 30, 60, 120, 240];
function pickInterval(visibleMinutesSum: number, targetLabelCount = 7): number {
  for (const iv of INTERVALS_MIN) {
    if (visibleMinutesSum / iv <= targetLabelCount) return iv;
  }
  return 240;
}
```

`visibleMinutesSum` = 視窗內所有 session 在視窗內的時長加總 (排除 session gap 那段、因為 gap 時間沒有 candle、不需要 label)。

### 6.3 Label 點生成

對每個可見 session：
1. 列出該 session 範圍內所有「`hour * 60 + minute` 是 interval 倍數」的 HH:MM 時刻
2. 用 `scaleX_compressed(iso, sessions, innerW)` 投影到 px
3. 與已生成 label 集合 x 距離 < 40px 就丟掉

### 6.4 Session boundary marker

對 `sessionBoundaries(sessions, innerW)` 回傳的每個 gap：

- 位置：標在 gap 的**右側**(新 session 開頭)上方
- y = `PAD_T - 4`
- 文字 = `${MM/DD} ${sessionType}`
  - date 取 next session `startIso` parse 出 month/day (session 跨午夜時用「session 開始那天」當代表日期)
  - sessionType = `(hour < 8 || hour >= 14) ? "夜盤" : "日盤"` (從 startIso 的 hour 判)
- className：`fill-ink-dim text-[10px]`

### 6.5 第一個 session 的 marker

最左邊那個 session 沒有 gap 但也要標 (不然 user 看不出最左是什麼) — 在 `sx(visibleCandles[0].date)` 上方畫同樣 marker。

---

## 7. 左上角 Header

### 7.1 Layout

```
MXFR1                           ← 22px serif, font-medium
18234   ▲ 86 (+0.47%)            ← 44px italic serif | 18px change/pct
```

放在 `MXFIntradayChart` 最上方 (toolbar 之前)、`mb-4` 隔開。

### 7.2 JSX

```tsx
<div className="mb-4">
  <div className="font-serif text-[22px] tracking-tight text-ink leading-tight font-medium">
    {symbol}
  </div>
  <div className="flex items-baseline gap-4 mt-1">
    <span className={`font-serif italic text-[44px] tabular-nums leading-none ${dirCls}`}>
      {latest ? latest.close : "—"}
    </span>
    {latest && baselineOpen && (
      <span className={`text-[18px] tabular-nums ${dirCls}`}>
        {isUp ? "▲" : change < 0 ? "▾" : "—"} {Math.abs(change)} 
        ({changePct >= 0 ? "+" : ""}{changePct.toFixed(2)}%)
      </span>
    )}
  </div>
</div>
```

- `dirCls` = `change > 0 ? "text-bull" : change < 0 ? "text-bear" : "text-ink-muted"`
- 台股慣例：紅漲綠跌

### 7.3 Baseline 計算

```ts
function dayOpenBaseline(candles: MXFCandle[]): number | null {
  const today = new Date().toDateString();
  for (const c of candles) {
    const d = new Date(c.date);
    if (d.toDateString() !== today) continue;
    const m = d.getHours() * 60 + d.getMinutes();
    if (m >= 525) return c.open;  // 525 = 08:45
  }
  return null;
}
```

### 7.4 Edge cases

- **凌晨夜盤中、今日日盤未開**：baseline = null → 不顯示 change/pct、只顯示現價
- **盤後 / candles 空**：整 header 用 `—` 佔位
- **跨日重整**：`today` 變新日期、自動找新日盤 open、舊夜盤資料不影響

### 7.5 Latest 資料源

`latest = candles[candles.length - 1].close` — `useMXFCandles` 已透過 WS 把最後一根 candle 持續更新到最新成交價、不需新 API。

**Header 跟 zoom 互相獨立** — user pan 到歷史區、header 仍顯示「最新 candle 的 close」、不是視窗內 close。

---

## 8. 既有 feature 適配

### 8.1 MA 計算位置

❌ 在 sliced array 上算 `computeMA` → 前 19 根會是 NaN
✅ 在**完整** `candles[]` 上算 `ma5/ma20`，再 slice 對應段餵 `MALine`

### 8.2 今日高/低 marker (`showHighLow` toggle)

- `todayHigh / todayLow` 從**完整 `candles`** 算
- marker 只在「高/低點的 idx 落在 viewRange 內」時 render
- 高低點在視窗外時 toggle 等於默默隱藏、不報錯

### 8.3 Crosshair hit test

- `hover.idx` 改為**索引 sliced array** (而非原 candles)
- handleMouseMove 內遍歷 visibleCandles
- mouseDown drag 中 hover state set null、不顯示 crosshair

### 8.4 VWAP / VOL / Session gap 虛線

- VWAP：用 sliced candles 的 `average`，直接畫 (per-candle 資料、不需重算)
- VOL：用 sliced candles 的 volume、`maxVolume = max(sliced.volume)` — 視窗內最大量決定 bar 高度
- Session gap 虛線：`inferSessions(slicedCandles)` 重算，視窗只看日盤就一段、跨 session 就多段

---

## 9. Testing

### 9.1 單元測試

新增 `frontend/src/lib/mxf-chart.test.ts` (或加進 `chart-svg.test.ts`)：

1. **`pickInterval(visibleMinutesSum)`**
   - 餵 30 → 預期 5
   - 餵 180 → 預期 30
   - 餵 600 → 預期 120
   - 餵 2000 → 預期 240

2. **`dayOpenBaseline(candles)`**
   - 餵純夜盤 candles (全 < 08:45) → 預期 null
   - 餵含 08:45 之後 candle → 預期該 candle 的 open
   - 餵跨日資料 (昨日夜盤 + 今日日盤) → 預期今日日盤的 open
   - 餵 candles 空 → 預期 null

3. **`computeNewViewRange(prevRange, mouseRatio, deltaY, candlesLen, innerW, minCandlePx)`** 抽純函式
   - Zoom in: deltaY < 0、visible 縮小、anchor 維持
   - Zoom out: deltaY > 0、visible 放大、anchor 維持
   - Clamp: zoom in 到 5 根停、zoom out 到 candle 寬 = 6px 停
   - Anchor 在最右邊 zoom out 時 startIdx 推向 0

### 9.2 視覺驗收 (手動)

- 1m TF 載入後預設視窗 ≈ 148 根 (innerW 888 / 6)
- Scroll wheel zoom in 到 5 根時不再縮
- Scroll wheel zoom out 到 candle 寬 < 6px 時不再寬
- Drag pan 到歷史區、新 WS candle 進來 viewRange 不跳走
- 切 1m → 5m timeframe → viewRange reset 到最新 148 根 (5m)
- Crosshair drag 中隱藏、drag 結束恢復
- 時間軸 label 隨 zoom 自動變密 / 變疏
- Session boundary 上方有 `5/26 夜盤` / `5/27 日盤` 之類小字
- Header 大字現價會跟著 WS 跳動 (不受 pan 影響)
- 凌晨夜盤中只顯示現價、不顯示 change/pct

---

## 10. 開放問題

無 — brainstorm 已逐項確認。後續 implementation 階段如有發現再回 spec 補。

---

## 11. 後續工作 (out-of-scope, 留 ticket)

- **後端加 `prev_settle` endpoint** 讓漲跌基準改用前一交易日結算價 (期貨業界標準)
- **Touch / pinch zoom** 支援行動裝置
- **Keyboard shortcut** (`+/-/0` 縮放、`←/→` pan)
- **歷史資料分頁載入** — 若未來資料量超過單次 API 上限
