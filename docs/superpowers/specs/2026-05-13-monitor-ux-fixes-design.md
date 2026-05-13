# Monitor 頁 UX 修正設計

> Date: 2026-05-13
> Scope: 分時走勢圖 / CDP 行為 / 切換體驗的 5 項使用者回饋
> Est: ~0.5 day 工程

## 1. 背景

Phase 3.1（commit `b27c8a2` ~ `acd6fa7`）整合 Monitor 頁後，使用者實際使用回饋出 5 個 UX 問題：

1. 圖表卡片內股票識別資訊（代號）字太小、沒中文名稱、跟下方的股價分離
2. 切換股票時，舊 candles 跟舊 CDP 線會殘留在新圖上，過場視覺奇怪
3. CDP toggle 每次重整 / 重新打開都要重勾，沒有記憶
4. CDP 純數學算出的值（如 `1004.5`）落在台股 tick 規則的「不可能成交價」上，導致 `close eq cdp_xx` 規則永遠不會觸發
5. Y 軸範圍依資料自動算，CDP 線可能拉到 ±10% 以外（台股漲跌停範圍），分時圖視覺被壓縮

## 2. 目標

讓「即時監控」頁的圖表體驗符合台股看盤習慣：

1. 圖表標題突出股票身份 + 即時報價，看盤主視角
2. 切換股票過場乾淨、不殘影
3. CDP 偏好持久化（單機 localStorage）
4. CDP 對齊台股 tick，讓「股價碰到 CDP」這類規則真的會觸發
5. 圖表 Y 軸預設鎖 ±10%（台股漲跌停範圍），CDP 線超出範圍隱藏；股價超出範圍時自動拉大

## 3. Issue 1：股票資訊區塊重排版

### 現況

`IntradayChart.tsx`：
- 上方：`<div className="text-xs text-ink-dim mb-2">{selected}</div>` ── 只顯示代號（12px dim）
- 下方：`border-t` 之後 `flex justify-between`，左側 `latest.close` 20px italic + 漲跌、右側 VWAP/CDP toggle

`Monitor.tsx:134` 把 `selected` 字串塞進去（純代號，無名稱）。

### 設計

```
┌─ 圖表卡片 (border p-7) ────────────────────────┐
│                                                 │
│   台積電 · 2330                  ← 22px font-serif tracking-tight  │
│                                                 │
│   580.50      ▲ 2.30 (+0.40%)    ← 44px italic tabular / 18px     │
│                                                 │
│   ─── (mb-4 spacing) ───                         │
│                                                 │
│   ┌─ Chart SVG ──────────────────────┐          │
│   │                                  │          │
│   │       (分時走勢圖)                │          │
│   │                                  │          │
│   └──────────────────────────────────┘          │
│                                                 │
│   ─────── border-t border-line ───────          │
│                                                 │
│                       [VWAP] [CDP]              │
└─────────────────────────────────────────────────┘
```

### 改動

- **`Monitor.tsx`**：
  - 移除 `<div className="text-xs text-ink-dim mb-2">{selected}</div>` (line 134)
  - 把 `symbolNames[selected]` 透過 prop `name` 傳給 `IntradayChart`
- **`IntradayChart.tsx` props**：新增 `name?: string | null`
- **`IntradayChart.tsx` header section**：
  - Line 1：`{name ?? "—"} · {symbol}`
    - `font-serif text-[22px] tracking-tight text-ink leading-tight`
  - Line 2：股價 + 漲跌
    - 股價：`font-serif italic text-[44px] tabular-nums {dirCls}`，無資料時顯示 `—`
    - 漲跌：`text-[18px] tabular-nums {dirCls}`，格式 `{arrow} {abs} ({signed}%)`
    - 百分比：`(latest.close - first.open) / first.open * 100`，到小數 2 位
- **下方原報價區**：刪掉左半（股價 + 漲跌），右半保留 VWAP/CDP toggle，整列改 `flex justify-end`

### 邊界條件

- 無 candles 時：股價顯示 `—`，漲跌不畫
- name 為 null（symbolNames 沒這檔，例如剛從 history 點過來但尚未在 watchlist）：顯示 `—`

## 4. Issue 2：切換股票過場 — 純文字 loading

### 現況

- `useIntradayCandles`：`fetchOnce` 開始時設 `loading=true`，但 `candles` 維持舊值直到新資料覆蓋
- `IntradayChart` 內部 `cdp` state 沒在 symbol 變動時清空，舊 CDP 線會跟新圖一起畫一陣子

### 設計

切換瞬間 → 圖表 SVG 區換成居中文字「載入中…」，容器高度不變 → 新資料進來 → 線重畫 + 新標題股價。

### 改動

- **`useIntradayCandles.ts`**：`useEffect` 進入時先 `setCandles([])` 再 `fetchOnce(symbol)`
- **`IntradayChart.tsx`**：
  - `useEffect` deps `[symbol]` 時先 `setCdp(null)`、`setCdpError(null)`，再依 `showCdp` 決定要不要 fetch
  - Header section 在 `candles.length === 0` 時顯示 `—`，避免高度跳
  - Chart SVG 區塊容器固定高（套用 `viewBox` + `className="w-full h-auto"` 已經是這樣，但 candles 空時整塊改成：

    ```tsx
    {candles.length === 0 ? (
      <div className="h-[360px] flex items-center justify-center text-ink-dim font-serif italic">
        載入中…
      </div>
    ) : (
      <svg viewBox={...}>...</svg>
    )}
    ```

### 移除

- 原本只有 `loading && candles.length === 0` 才顯示「分時資料載入中…」 → 改成 `candles.length === 0` 就顯示「載入中…」，無論 loading 狀態
- 文案統一 `載入中…`（更短）

## 5. Issue 3：CDP toggle 持久化

### 設計

只持久化 CDP toggle，VWAP 維持預設勾起、不持久化（這是使用者明確選的）。全局狀態（不分 symbol）。

### 改動

新增 `frontend/src/hooks/useLocalToggle.ts`：

```typescript
import { useEffect, useState } from "react";

export function useLocalToggle(key: string, defaultValue: boolean): [boolean, (v: boolean | ((p: boolean) => boolean)) => void] {
  const [value, setValue] = useState<boolean>(() => {
    if (typeof window === "undefined") return defaultValue;
    try {
      const raw = localStorage.getItem(key);
      return raw === null ? defaultValue : raw === "true";
    } catch {
      return defaultValue;
    }
  });

  useEffect(() => {
    try { localStorage.setItem(key, String(value)); } catch { /* quota / private mode */ }
  }, [key, value]);

  return [value, setValue];
}
```

`IntradayChart.tsx`：

```typescript
// 替換
const [showCdp, setShowCdp] = useState(false);
// 改成
const [showCdp, setShowCdp] = useLocalToggle("tk:chart:cdp", false);
```

VWAP 維持 `useState(true)` 不動。

### 命名規範

localStorage key 統一 `tk:` 前綴（trading-king）+ 區塊 + 用途。未來新增 toggle 都走這個規範。

## 6. Issue 4：CDP tick 對齊

### 問題

台股 tick ladder：

| 價格範圍 | tick |
|---------|------|
| < 10 | 0.01 |
| 10 ≤ p < 50 | 0.05 |
| 50 ≤ p < 100 | 0.10 |
| 100 ≤ p < 500 | 0.50 |
| 500 ≤ p < 1000 | 1.00 |
| ≥ 1000 | 5.00 |

`compute_cdp` 是純數學運算，沒對齊 tick。1000 元以上股票算出 `cdp_ah = 1004.5` 時，沒有任何成交價會等於 1004.5。

### 設計

`compute_cdp` 算完 raw 後對齊到台股 tick，**方向意識型**：

- `ah`, `nh` (阻力位) → 向上取（`ceil_to_tick`）
- `al`, `nl` (支撐位) → 向下取（`floor_to_tick`）
- `cdp` (中線) → 取最近（`round_to_tick`）

理由：阻力位向上取 = 「真的突破阻力」，支撐位向下取 = 「真的跌破支撐」，這跟交易語意一致。

### 改動：`backend/services/cdp.py`

新增：

```python
from typing import Literal

# 台股 tick ladder
_TICK_LADDER = (
    (10, 0.01),
    (50, 0.05),
    (100, 0.10),
    (500, 0.50),
    (1000, 1.00),
    (float("inf"), 5.00),
)

def _tick_size(price: float) -> float:
    for upper, tick in _TICK_LADDER:
        if price < upper:
            return tick
    return 5.00  # unreachable

def round_to_tick_tw(price: float, direction: Literal["up", "down", "nearest"]) -> float:
    """對齊台股 tick。direction:
    - up:   向上取（ceil）— 阻力位
    - down: 向下取（floor）— 支撐位
    - nearest: 取最近 — 中線
    """
    tick = _tick_size(price)
    units = price / tick
    if direction == "up":
        rounded = math.ceil(units) * tick
    elif direction == "down":
        rounded = math.floor(units) * tick
    else:
        rounded = round(units) * tick
    # 浮點誤差修正（tick 0.05 / 0.1 / 0.5 都會踩到）
    return round(rounded, 2)
```

改 `compute_cdp`：

```python
def compute_cdp(o: float, h: float, l: float, c: float) -> dict[str, float]:
    cdp = (h + l + 2 * c) / 4
    ah_raw = cdp + (h - l)
    nh_raw = 2 * cdp - l
    nl_raw = 2 * cdp - h
    al_raw = cdp - (h - l)
    return {
        "ah":  round_to_tick_tw(ah_raw, "up"),
        "nh":  round_to_tick_tw(nh_raw, "up"),
        "cdp": round_to_tick_tw(cdp,    "nearest"),
        "nl":  round_to_tick_tw(nl_raw, "down"),
        "al":  round_to_tick_tw(al_raw, "down"),
    }
```

需 `import math` 在檔案頂部。

### Smoke test 補充

`cdp.py` 既有 `__main__` smoke 區段加 Step：

```python
step(4, "tick rounding — 1000+ 跨越 tick 5 / tick 1 邊界")
r = compute_cdp(1000, 1010, 990, 1002)
# raw cdp = (1010+990+2*1002)/4 = 1001       → nearest tick 5      → 1000
# raw ah  = 1001 + (1010-990) = 1021         → ceil  tick 5 (1021≥1000) → 1025
# raw nh  = 2*1001 - 990 = 1012              → ceil  tick 5 (1012≥1000) → 1015
# raw nl  = 2*1001 - 1010 = 992              → floor tick 1 (500≤992<1000) → 992
# raw al  = 1001 - 20 = 981                  → floor tick 1 (500≤981<1000) → 981
expected = {"ah": 1025, "nh": 1015, "cdp": 1000, "nl": 992, "al": 981}
for k, v in expected.items():
    if abs(r[k] - v) > 0.001:
        fail(f"{k} 不對: 算到 {r[k]}, 預期 {v}")
ok(f"tick 對齊 + 跨 band 正確: {r}")

step(5, "tick rounding — 500-1000 band 用 tick 1（純 band 內）")
r = compute_cdp(580, 600, 560, 590)
# raw cdp = (600+560+2*590)/4 = 2340/4 = 585        → nearest tick 1 → 585
# raw ah  = 585 + 40 = 625                          → ceil  tick 1   → 625
# raw nh  = 2*585 - 560 = 610                       → ceil  tick 1   → 610
# raw nl  = 2*585 - 600 = 570                       → floor tick 1   → 570
# raw al  = 585 - 40 = 545                          → floor tick 1   → 545
expected = {"ah": 625, "nh": 610, "cdp": 585, "nl": 570, "al": 545}
for k, v in expected.items():
    if abs(r[k] - v) > 0.001:
        fail(f"{k} 不對: 算到 {r[k]}, 預期 {v}")
ok(f"500-1000 band 對齊正確: {r}")
```

注意：原 `__main__` smoke 的 step 1（OHLC=2300/2320/2280/2290）預期值是 raw 數學運算結果。新版 `compute_cdp` 會對齊 tick，需要更新該 step 的 expected：
```python
# 原 expected = {"ah": 2335, "nh": 2310, "cdp": 2295, "nl": 2270, "al": 2255}
# 1000+ 用 tick 5：
#   2335 → ceil  tick 5 → 2335 (already on grid)
#   2310 → ceil  tick 5 → 2310
#   2295 → nearest tick 5 → 2295
#   2270 → floor tick 5 → 2270
#   2255 → floor tick 5 → 2255
# 全部本來就在 grid 上 → 預期值不變
```

### 影響範圍

- **既有 in-memory cache**：backend 重啟後自動套用新行為（lazy compute）
- **`daily_ohlc` 表**：不動，CDP 是衍生計算
- **`signal_engine._field_cache`**：在 `_refill_field_cache` 透過 `cdp.get()` 拿值，自動拿到對齊後的值
- **規則語意**：`close eq cdp_ah` 現在能命中了

### 不做的事

- 不引入新 operator（如 `touch`）── 對齊到 tick 後 `eq` 就能用，足夠
- 不對 indicator_cache 內的其他指標做 tick 對齊 — 那些是分析用，不是交易價

## 7. Issue 5：Y 軸固定 ±10%

### 問題

`IntradayChart.tsx:39-40`：

```typescript
const yMin = Math.min(...allY) * 0.998;
const yMax = Math.max(...allY) * 1.002;
```

`allY` 含 closes、vwaps、（CDP 啟用時）`cdp.ah` + `cdp.al`。CDP 線超出 ±10% 時 Y 軸被拉到很怪。

### 設計

以「今天開盤價」為基準（≈ 昨日收盤，相差不超過 1 個漲跌幅）：

- 預設 Y 軸範圍：`[refPrice × 0.9, refPrice × 1.1]`
- CDP 線：超出 ±10% 範圍的線不畫
- 股價本身超出 ±10%（新股、特殊狀況）：Y 軸自動拉大到包住股價

### 改動：`IntradayChart.tsx`

修改 `useMemo`：

```typescript
const { yMin, yMax, scaleX, scaleY, polyClose, polyVwap, visibleCdpKeys } = useMemo(() => {
  if (candles.length === 0) {
    return { yMin: 0, yMax: 0, scaleX: () => 0, scaleY: () => 0, polyClose: "", polyVwap: "", visibleCdpKeys: [] };
  }
  const closes = candles.map((c) => c.close);
  const vwaps = candles.map((c) => c.average);

  const refPrice = candles[0].open;
  const refMin = refPrice * 0.9;
  const refMax = refPrice * 1.1;

  // CDP: 過濾掉超出 ±10% 的 key
  const allCdpKeys = ["ah", "nh", "cdp", "nl", "al"] as const;
  const visibleCdpKeys = (showCdp && cdp)
    ? allCdpKeys.filter(k => cdp[k] >= refMin && cdp[k] <= refMax)
    : [];

  // Y 軸：±10% 為最小範圍，價格超出就拉大；CDP 不影響 Y 軸（隱藏的不算，可見的本來就在範圍內）
  const priceMin = Math.min(...closes, ...vwaps);
  const priceMax = Math.max(...closes, ...vwaps);
  const yMin = Math.min(refMin, priceMin) * 0.998;
  const yMax = Math.max(refMax, priceMax) * 1.002;

  const xRange = CHART_W - PAD_L - PAD_R;
  const yRange = CHART_H - PAD_T - PAD_B;
  const scaleX = (i: number) => PAD_L + (i / Math.max(candles.length - 1, 1)) * xRange;
  const scaleY = (v: number) => PAD_T + (1 - (v - yMin) / (yMax - yMin || 1)) * yRange;
  const polyClose = candles.map((c, i) => `${scaleX(i)},${scaleY(c.close)}`).join(" ");
  const polyVwap = candles.map((c, i) => `${scaleX(i)},${scaleY(c.average)}`).join(" ");
  return { yMin, yMax, scaleX, scaleY, polyClose, polyVwap, visibleCdpKeys };
}, [candles, cdp, showCdp]);
```

修改 CDP 渲染：把原本的 `(["ah","nh","cdp","nl","al"] as const).map(...)` 換成 `visibleCdpKeys.map(...)`。

### 為什麼用 `candles[0].open` 而非 `daily_ohlc.close`

- `candles[0].open` 是分時 candle 第一根的開盤價 = 今天開盤集合競價成交價
- 對台股，今日開盤跟昨日收盤理論最大差距 10%（漲跌停限制）
- 用 candles 內含資料 = 不需新 endpoint、沒有額外 API 依賴
- 副作用：開盤前（盤前/收盤後）candles[0] 是昨日資料，但這個頁面盤前不 active

替代方案（未來想精確昨日收盤）：擴充 `/api/candles/{symbol}/intraday` 回應加 `prev_close` 欄位，從 `daily_ohlc` 抓最近一筆。這次先用 open 簡化。

### 邊界

- `candles.length === 0`：跳過全部計算，維持 loading 文字
- `refPrice === 0` 或 NaN：fall back 到 `Math.min/max` 原邏輯（極端防呆）

## 8. 改動檔案清單

| 檔案 | Issue | 改什麼 |
|------|-------|-------|
| `frontend/src/components/IntradayChart.tsx` | 1, 2, 3, 5 | header 重排版、loading 文字、CDP toggle 用 localStorage、±10% Y 軸 + CDP 隱藏 |
| `frontend/src/pages/Monitor.tsx` | 1 | 移除舊小標題、傳 `name` prop 給 IntradayChart |
| `frontend/src/hooks/useIntradayCandles.ts` | 2 | symbol 變動時清空 candles |
| `frontend/src/hooks/useLocalToggle.ts` | 3 | 新增（包 localStorage 的 useState） |
| `backend/services/cdp.py` | 4 | 新增 `_tick_size`、`round_to_tick_tw`、改 `compute_cdp` 加 direction-aware rounding + smoke 補 step |

## 9. 驗證

### 手動 UAT（建議盤中）

1. **Issue 1**：開 Monitor 頁，點任一檔自選股 → 圖表卡片上方應顯示 `(名稱) · (代號)` 22px + 大字股價 44px + 漲跌百分比
2. **Issue 2**：在 watchlist 連續切換 3 檔股票 → 中間瞬間應顯示「載入中…」，舊圖不殘留
3. **Issue 3**：勾 CDP → F5 重整頁 → CDP 仍然勾著
4. **Issue 4**：對一檔 1000 元以上股票（例如 2330 若在 1000+）打開 CDP → 看 CDP 線數字應為 5 的倍數
5. **Issue 5**：對一檔 CDP 範圍很寬的股票打開 CDP → 超出 ±10% 的線應不畫；股價應穩定在圖表中央區

### 自動驗證

- `python backend/services/cdp.py`：smoke test 通過
- TypeScript build：`npm run build` 在 frontend 通過
- ESLint：無新 warning

## 10. 不做的事

- 不引入新的 CDP 觸發語意（如 `touch` operator）— 對齊到 tick 後 `eq` 可用，足夠
- 不改 Backend `/api/cdp` response schema（既有欄位仍是 `ah/nh/cdp/nl/al`，只是值對齊了）
- 不改 watchlist row 的視覺（issue 1 限縮在圖表卡片內）
- 不做骨架圖 loading animation — 文字夠用
- 不對每檔股票各自記 CDP 偏好（全局狀態夠用）
