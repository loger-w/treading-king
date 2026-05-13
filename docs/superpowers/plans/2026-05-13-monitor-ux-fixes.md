# Monitor 頁 UX 修正 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Monitor 頁的 5 個 UX 問題修掉 — 股票標題大字化 / 切換 loading / CDP toggle 持久化 / CDP tick 對齊台股 / Y 軸鎖 ±10%。

**Architecture:**
- 後端：`cdp.py` 加 direction-aware tick rounding（阻力位向上、支撐位向下、中線最近），動到 `compute_cdp` 一個函式。
- 前端：新增 `useLocalToggle` hook、改 `useIntradayCandles` 切換清空、`IntradayChart.tsx` 大改（標題、loading、CDP 持久化、Y 軸 ±10%）、`Monitor.tsx` 傳 name prop。

**Tech Stack:** Python 3.11+ / FastAPI / React 18 / TypeScript / Tailwind CSS（Vite）

**Spec:** `docs/superpowers/specs/2026-05-13-monitor-ux-fixes-design.md`

---

## 檔案結構

| 檔案 | 角色 | 改動 |
|------|------|-----|
| `backend/services/cdp.py` | CDP 計算 + tick 對齊 | 修改 |
| `frontend/src/hooks/useLocalToggle.ts` | 包 localStorage 的 useState | 新建 |
| `frontend/src/hooks/useIntradayCandles.ts` | Intraday K 線 + WS tick 更新 | 修改 |
| `frontend/src/components/IntradayChart.tsx` | 圖表元件 — 改 5 個地方 | 修改 |
| `frontend/src/pages/Monitor.tsx` | Monitor 頁 — 移除舊小標題、傳 name | 修改 |

任務切分原則：每個 task 結束後檔案處於 compilable + commitable 狀態，scope 限縮在一個 issue。

---

## Task 1: Backend — CDP tick 對齊（Issue 4）

**Files:**
- Modify: `backend/services/cdp.py`

- [ ] **Step 1: 在 cdp.py 頂部加 import math + Literal**

在 `from __future__ import annotations` 之後、`import asyncio` 之前加：

```python
import math
```

當前 typing import 是 `from typing import Any, TypedDict`，改成：

```python
from typing import Any, Literal, TypedDict
```

（math 給 round_to_tick_tw 用 ceil/floor；Literal 給 direction 參數 narrow type。）

- [ ] **Step 2: 在 `compute_cdp` 函式之前新增 tick ladder + rounding helpers**

在 `def compute_cdp(...)` 函式上方插入：

```python
# 台股 tick ladder（價格 < upper 時用對應 tick）
_TICK_LADDER = (
    (10.0,           0.01),
    (50.0,           0.05),
    (100.0,          0.10),
    (500.0,          0.50),
    (1000.0,         1.00),
    (float("inf"),   5.00),
)


def _tick_size(price: float) -> float:
    """回傳 price 對應的台股最小升降單位。"""
    for upper, tick in _TICK_LADDER:
        if price < upper:
            return tick
    return 5.00  # unreachable


def round_to_tick_tw(price: float, direction: Literal["up", "down", "nearest"]) -> float:
    """對齊台股 tick。

    direction:
      - "up":      向上取（ceil） — 阻力位（AH/NH）
      - "down":    向下取（floor） — 支撐位（NL/AL）
      - "nearest": 四捨五入 — 中線（CDP）
    """
    tick = _tick_size(price)
    units = price / tick
    if direction == "up":
        rounded = math.ceil(units) * tick
    elif direction == "down":
        rounded = math.floor(units) * tick
    else:  # nearest
        rounded = round(units) * tick
    # 浮點誤差修正（tick 0.05 / 0.1 / 0.5 都會踩到）
    return round(rounded, 2)
```

- [ ] **Step 3: 改寫 `compute_cdp` 用 direction-aware rounding**

把現有 `compute_cdp`：

```python
def compute_cdp(o: float, h: float, l: float, c: float) -> dict[str, float]:
    """純函式 — 給 OHLC 算 5 線值。"""
    cdp = (h + l + 2 * c) / 4
    ah = cdp + (h - l)
    nh = 2 * cdp - l
    nl = 2 * cdp - h
    al = cdp - (h - l)
    return {"ah": ah, "nh": nh, "cdp": cdp, "nl": nl, "al": al}
```

換成：

```python
def compute_cdp(o: float, h: float, l: float, c: float) -> dict[str, float]:
    """純函式 — 給 OHLC 算 5 線值，並對齊台股 tick：
       AH/NH 向上、AL/NL 向下、CDP 中線取最近。"""
    cdp_raw = (h + l + 2 * c) / 4
    ah_raw = cdp_raw + (h - l)
    nh_raw = 2 * cdp_raw - l
    nl_raw = 2 * cdp_raw - h
    al_raw = cdp_raw - (h - l)
    return {
        "ah":  round_to_tick_tw(ah_raw,  "up"),
        "nh":  round_to_tick_tw(nh_raw,  "up"),
        "cdp": round_to_tick_tw(cdp_raw, "nearest"),
        "nl":  round_to_tick_tw(nl_raw,  "down"),
        "al":  round_to_tick_tw(al_raw,  "down"),
    }
```

- [ ] **Step 4: 把 `__main__` smoke 區段裡 Step 1 的 expected 註解改清楚**

現有 step 1 的預期值 `{"ah": 2335, "nh": 2310, "cdp": 2295, "nl": 2270, "al": 2255}` 對應的價格都在 1000+ band（tick 5）且都剛好在 tick grid 上，所以即使 compute_cdp 改成對齊版，預期值不變。在 step 1 的 expected 註解後加一行：

```python
    # 這些值剛好都在 tick 5 grid 上 (2335/2310/2295/2270/2255 都是 5 的倍數)，對齊版不影響預期
```

（原碼第 204 行附近）

- [ ] **Step 5: 在 smoke 區段尾端新增 Step 4 / Step 5（驗 tick 對齊）**

在 `step(3, "ordering — AH > NH > CDP > NL > AL（H>L 時）")` 區塊之後、`print("All cdp smoke tests passed")` 之前，加：

```python
    step(4, "tick rounding — 1000+ 跨越 tick 5 / tick 1 邊界")
    r = compute_cdp(1000, 1010, 990, 1002)
    # raw cdp = (1010+990+2*1002)/4 = 1001  → nearest tick 5 → 1000
    # raw ah  = 1001 + 20 = 1021             → ceil tick 5    → 1025
    # raw nh  = 2*1001 - 990 = 1012          → ceil tick 5    → 1015
    # raw nl  = 2*1001 - 1010 = 992          → floor tick 1   → 992
    # raw al  = 1001 - 20 = 981              → floor tick 1   → 981
    expected = {"ah": 1025, "nh": 1015, "cdp": 1000, "nl": 992, "al": 981}
    for k, v in expected.items():
        if abs(r[k] - v) > 0.001:
            fail(f"{k} 不對: 算到 {r[k]}, 預期 {v}")
    ok(f"tick 對齊 + 跨 band 正確: {r}")

    step(5, "tick rounding — 500-1000 band 用 tick 1")
    r = compute_cdp(580, 600, 560, 590)
    # raw cdp = (600+560+2*590)/4 = 585      → nearest tick 1 → 585
    # raw ah  = 585 + 40 = 625                → ceil tick 1    → 625
    # raw nh  = 2*585 - 560 = 610             → ceil tick 1    → 610
    # raw nl  = 2*585 - 600 = 570             → floor tick 1   → 570
    # raw al  = 585 - 40 = 545                → floor tick 1   → 545
    expected = {"ah": 625, "nh": 610, "cdp": 585, "nl": 570, "al": 545}
    for k, v in expected.items():
        if abs(r[k] - v) > 0.001:
            fail(f"{k} 不對: 算到 {r[k]}, 預期 {v}")
    ok(f"500-1000 band 對齊正確: {r}")

    step(6, "tick rounding helper — direction up/down/nearest 邊界")
    # 1004.5 在 1000+ band → tick 5
    assert round_to_tick_tw(1004.5, "up")      == 1005, "up boundary"
    assert round_to_tick_tw(1004.5, "down")    == 1000, "down boundary"
    assert round_to_tick_tw(1004.5, "nearest") == 1005, "nearest 1004.5 → 1005 (round half up via Python round)"
    # 50 邊界 — 50 < 50 false → 入 50-100 band tick 0.1
    assert round_to_tick_tw(50.05, "nearest") == 50.10 or round_to_tick_tw(50.05, "nearest") == 50.00, \
        "boundary 50 用 tick 0.1（banker round 可 50.0 或 50.1）"
    ok("rounding helper 邊界 OK")
```

注意：Python 3 內建 `round()` 是 banker's rounding（round-half-to-even），1004.5 / 5 = 200.9 → round → 201 → 1005，所以 nearest 1004.5 → 1005 確定。但 50.05 / 0.1 = 500.5 在 banker round 下可能是 500（even），所以給容錯。

- [ ] **Step 6: 跑 smoke 驗證**

Run: `python -m services.cdp` （在 backend 目錄下）

Expected: 看到 `All cdp smoke tests passed ✓`，6 個 step 全綠。

- [ ] **Step 7: Commit**

```bash
git add backend/services/cdp.py
git commit -m "feat(cdp): align CDP levels to 台股 tick ladder

direction-aware rounding:
- AH/NH (resistance) → ceil to tick
- AL/NL (support)    → floor to tick
- CDP  (mid)         → nearest tick

解 1000+ 元股票 CDP 落在 tick 5 規則外（如 1004.5）導致 close eq cdp 永
不觸發的問題。compute_cdp 邏輯改但 schema 不動。
"
```

---

## Task 2: Frontend — `useLocalToggle` hook（Issue 3 基礎）

**Files:**
- Create: `frontend/src/hooks/useLocalToggle.ts`

- [ ] **Step 1: 新建 hook 檔案**

寫入 `frontend/src/hooks/useLocalToggle.ts`：

```typescript
import { useEffect, useState } from "react";

/**
 * 包 localStorage 的 boolean useState。
 *
 * - 首次讀 localStorage[key]，沒有就用 defaultValue
 * - setValue 自動同步寫回（quota / private mode 失敗時靜默吞掉）
 *
 * Naming convention: key 用 "tk:" 前綴（trading-king）→ "tk:chart:cdp" 之類。
 */
export function useLocalToggle(
  key: string,
  defaultValue: boolean,
): [boolean, (v: boolean | ((prev: boolean) => boolean)) => void] {
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
    try {
      localStorage.setItem(key, String(value));
    } catch {
      /* quota exceeded / private mode — 靜默 */
    }
  }, [key, value]);

  return [value, setValue];
}
```

- [ ] **Step 2: TypeScript 檢查（沒實際使用前先確認語法）**

Run: `cd frontend && npx tsc --noEmit`

Expected: 不該有 error 提到這個檔案。其他既有 error 維持原狀即可（這 hook 還沒被 import）。

- [ ] **Step 3: Commit**

```bash
git add frontend/src/hooks/useLocalToggle.ts
git commit -m "feat(hooks): add useLocalToggle for persisting boolean state

包 localStorage 的 useState，SSR-safe + quota/private mode 靜默吞錯。
Key naming convention: 'tk:' 前綴。
"
```

---

## Task 3: Frontend — `useIntradayCandles` 切換清空（Issue 2 一半）

**Files:**
- Modify: `frontend/src/hooks/useIntradayCandles.ts`

- [ ] **Step 1: 切 symbol 時先清空 candles**

當前 `useEffect`（line 25–32）：

```typescript
  useEffect(() => {
    if (!symbol) { setCandles([]); return; }
    fetchOnce(symbol);
    timerRef.current = setInterval(() => fetchOnce(symbol), REFRESH_MS);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [symbol, fetchOnce]);
```

改成：

```typescript
  useEffect(() => {
    if (!symbol) { setCandles([]); return; }
    setCandles([]);  // 先清空 — 避免新 symbol 載入過程中舊資料殘留視覺奇怪
    fetchOnce(symbol);
    timerRef.current = setInterval(() => fetchOnce(symbol), REFRESH_MS);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [symbol, fetchOnce]);
```

只多一行 `setCandles([]);` 在 `fetchOnce(symbol)` 之前。

- [ ] **Step 2: TypeScript 檢查**

Run: `cd frontend && npx tsc --noEmit`

Expected: 無新 error。

- [ ] **Step 3: Commit**

```bash
git add frontend/src/hooks/useIntradayCandles.ts
git commit -m "fix(useIntradayCandles): clear candles when symbol changes

避免切換股票時舊 K 棒殘留在新圖上。loading 顯示交給 IntradayChart 內
candles.length === 0 的判斷。
"
```

---

## Task 4: Frontend — IntradayChart Y 軸 ±10%（Issue 5）

**Files:**
- Modify: `frontend/src/components/IntradayChart.tsx`

- [ ] **Step 1: 修改 useMemo 計算 — 加 refPrice / refMin / refMax / visibleCdpKeys**

當前 `useMemo`（line 31–48）：

```typescript
  const { yMin, yMax, scaleX, scaleY, polyClose, polyVwap } = useMemo(() => {
    if (candles.length === 0) {
      return { yMin: 0, yMax: 0, scaleX: () => 0, scaleY: () => 0, polyClose: "", polyVwap: "" };
    }
    const closes = candles.map((c) => c.close);
    const vwaps = candles.map((c) => c.average);
    const allY = [...closes, ...vwaps];
    if (cdp && showCdp) allY.push(cdp.ah, cdp.al);
    const yMin = Math.min(...allY) * 0.998;
    const yMax = Math.max(...allY) * 1.002;
    const xRange = CHART_W - PAD_L - PAD_R;
    const yRange = CHART_H - PAD_T - PAD_B;
    const scaleX = (i: number) => PAD_L + (i / Math.max(candles.length - 1, 1)) * xRange;
    const scaleY = (v: number) => PAD_T + (1 - (v - yMin) / (yMax - yMin || 1)) * yRange;
    const polyClose = candles.map((c, i) => `${scaleX(i)},${scaleY(c.close)}`).join(" ");
    const polyVwap = candles.map((c, i) => `${scaleX(i)},${scaleY(c.average)}`).join(" ");
    return { yMin, yMax, scaleX, scaleY, polyClose, polyVwap };
  }, [candles, cdp, showCdp]);
```

改成（return 值多一個 `visibleCdpKeys`，邏輯改為 ±10% 為預設範圍 + CDP 過濾）：

```typescript
  const { yMin, yMax, scaleX, scaleY, polyClose, polyVwap, visibleCdpKeys } = useMemo(() => {
    if (candles.length === 0) {
      return {
        yMin: 0, yMax: 0,
        scaleX: () => 0, scaleY: () => 0,
        polyClose: "", polyVwap: "",
        visibleCdpKeys: [] as Array<"ah" | "nh" | "cdp" | "nl" | "al">,
      };
    }
    const closes = candles.map((c) => c.close);
    const vwaps = candles.map((c) => c.average);

    // 基準價 = 今天開盤（≈ 昨日收盤，差距理論 ≤ 10%）
    const refPrice = candles[0].open;
    const refMin = refPrice * 0.9;
    const refMax = refPrice * 1.1;

    // CDP 5 線：過濾掉超出 ±10% 的 key
    const allCdpKeys = ["ah", "nh", "cdp", "nl", "al"] as const;
    const visibleCdpKeys: Array<typeof allCdpKeys[number]> = (showCdp && cdp)
      ? allCdpKeys.filter((k) => cdp[k] >= refMin && cdp[k] <= refMax)
      : [];

    // Y 軸：±10% 為最小範圍，價格超出就拉大（隱藏的 CDP 不算）
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

- [ ] **Step 2: 把 CDP 渲染區塊改用 visibleCdpKeys**

當前 line 79–93：

```tsx
          {/* CDP 5 線 */}
          {showCdp && cdp && (
            <>
              {(["ah", "nh", "cdp", "nl", "al"] as const).map((k) => (
                <g key={k}>
                  <line x1={PAD_L} y1={scaleY(cdp[k])} x2={CHART_W - PAD_R} y2={scaleY(cdp[k])}
                    stroke="var(--color-accent, #e85a4f)" strokeWidth="0.6"
                    strokeDasharray="4 3" opacity="0.6" />
                  <text x={CHART_W - PAD_R - 2} y={scaleY(cdp[k]) - 2} textAnchor="end"
                    className="fill-accent text-[10px] uppercase">
                    {k.toUpperCase()} {cdp[k].toFixed(1)}
                  </text>
                </g>
              ))}
            </>
          )}
```

改成：

```tsx
          {/* CDP 5 線（超出 ±10% 範圍的隱藏） */}
          {showCdp && cdp && visibleCdpKeys.length > 0 && (
            <>
              {visibleCdpKeys.map((k) => (
                <g key={k}>
                  <line x1={PAD_L} y1={scaleY(cdp[k])} x2={CHART_W - PAD_R} y2={scaleY(cdp[k])}
                    stroke="var(--color-accent, #e85a4f)" strokeWidth="0.6"
                    strokeDasharray="4 3" opacity="0.6" />
                  <text x={CHART_W - PAD_R - 2} y={scaleY(cdp[k]) - 2} textAnchor="end"
                    className="fill-accent text-[10px] uppercase">
                    {k.toUpperCase()} {cdp[k].toFixed(1)}
                  </text>
                </g>
              ))}
            </>
          )}
```

差別：`(["ah", ...] as const).map` 改成 `visibleCdpKeys.map`，外層加 `&& visibleCdpKeys.length > 0`（避免 5 條都隱藏時還畫一個空 fragment）。

- [ ] **Step 3: TypeScript 檢查**

Run: `cd frontend && npx tsc --noEmit`

Expected: 無 error。`visibleCdpKeys` type 應該被推斷成 `Array<"ah" | "nh" | "cdp" | "nl" | "al">`。

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/IntradayChart.tsx
git commit -m "feat(IntradayChart): clamp Y axis to ±10% with auto-expand

Y 軸預設用今天開盤價 ±10%（≈ 台股漲跌停範圍）。CDP 線超出範圍的不畫；
股價超出範圍時 Y 軸自動拉大包住。
"
```

---

## Task 5: Frontend — IntradayChart loading 狀態 + CDP 切換清空（Issue 2 另一半）

**Files:**
- Modify: `frontend/src/components/IntradayChart.tsx`

- [ ] **Step 1: 切 symbol 時清空 cdp state**

當前 `useEffect`（line 23–29）：

```typescript
  useEffect(() => {
    if (!showCdp) return;
    setCdpError(null);
    api.cdp(symbol).then(setCdp).catch((e) =>
      setCdpError(e instanceof Error ? e.message : String(e))
    );
  }, [symbol, showCdp]);
```

改成：

```typescript
  useEffect(() => {
    // 切 symbol 時先清舊 CDP — 避免新圖上殘留舊 CDP 線
    setCdp(null);
    setCdpError(null);
    if (!showCdp) return;
    api.cdp(symbol).then(setCdp).catch((e) =>
      setCdpError(e instanceof Error ? e.message : String(e))
    );
  }, [symbol, showCdp]);
```

注意：把 `setCdp(null)` 提到 `if (!showCdp) return` 之前，讓「showCdp=false 時切 symbol」也清掉殘留。

- [ ] **Step 2: 把 SVG 區的 loading 條件改成 candles.length === 0 顯示「載入中…」**

當前 line 58–62：

```tsx
      {loading && candles.length === 0 ? (
        <div className="h-[360px] flex items-center justify-center text-ink-dim font-serif italic">
          分時資料載入中…
        </div>
      ) : (
        <svg viewBox={`0 0 ${CHART_W} ${CHART_H}`} className="w-full h-auto">
```

改成：

```tsx
      {candles.length === 0 ? (
        <div className="h-[360px] flex items-center justify-center text-ink-dim font-serif italic">
          載入中…
        </div>
      ) : (
        <svg viewBox={`0 0 ${CHART_W} ${CHART_H}`} className="w-full h-auto">
```

差別：條件從 `loading && candles.length === 0` 改成 `candles.length === 0`，文案統一「載入中…」。配合 Task 3 useIntradayCandles 切換清空，切 symbol 瞬間就會落入這個 branch。

- [ ] **Step 3: TypeScript 檢查**

Run: `cd frontend && npx tsc --noEmit`

Expected: 無 error。`loading` prop 仍然在 interface 裡（暫時 unused，下次 cleanup 可移除，這次不動）。

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/IntradayChart.tsx
git commit -m "fix(IntradayChart): clean transition when symbol changes

切 symbol 時清舊 CDP；candles 為空時統一顯示「載入中…」（之前要 loading
flag + 空陣列 兩個都 true 才顯示，但 useIntradayCandles 已會清空）。
"
```

---

## Task 6: Frontend — IntradayChart CDP toggle 持久化（Issue 3 完成）

**Files:**
- Modify: `frontend/src/components/IntradayChart.tsx`

- [ ] **Step 1: import useLocalToggle**

當前 import 區（line 1）：

```typescript
import { useEffect, useMemo, useState } from "react";
import { api, type CdpLevels, type IntradayCandle } from "../lib/api";
```

加一行：

```typescript
import { useEffect, useMemo, useState } from "react";
import { api, type CdpLevels, type IntradayCandle } from "../lib/api";
import { useLocalToggle } from "../hooks/useLocalToggle";
```

- [ ] **Step 2: 把 showCdp 從 useState 換成 useLocalToggle**

當前 line 19：

```typescript
  const [showCdp, setShowCdp] = useState(false);
```

改成：

```typescript
  const [showCdp, setShowCdp] = useLocalToggle("tk:chart:cdp", false);
```

VWAP 那行（line 18）`const [showVwap, setShowVwap] = useState(true);` 不動。

- [ ] **Step 3: TypeScript 檢查 + 跑 dev server 手動驗**

Run: `cd frontend && npx tsc --noEmit`
Expected: 無 error。

Run: `cd frontend && npm run dev`
手動測試：
1. 開 Monitor 頁，點 watchlist 任一檔
2. 勾 CDP → 應顯示 CDP 線
3. F5 重整 → CDP toggle 應仍勾起、CDP 線仍顯示
4. 取消 CDP → F5 重整 → CDP 應仍取消
5. DevTools → Application → Local Storage → 應有 `tk:chart:cdp` key

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/IntradayChart.tsx
git commit -m "feat(IntradayChart): persist CDP toggle via localStorage

key: tk:chart:cdp. VWAP 維持預設勾、不持久化。
"
```

---

## Task 7: Frontend — IntradayChart + Monitor 股票資訊重排版（Issue 1）

**Files:**
- Modify: `frontend/src/components/IntradayChart.tsx`
- Modify: `frontend/src/pages/Monitor.tsx`

- [ ] **Step 1: IntradayChart props 加 name**

當前 line 4–8：

```typescript
interface Props {
  symbol: string;
  candles: IntradayCandle[];
  loading: boolean;
}
```

改成：

```typescript
interface Props {
  symbol: string;
  name: string | null;
  candles: IntradayCandle[];
  loading: boolean;
}
```

並把 line 17 簽名加進來：

```typescript
export function IntradayChart({ symbol, candles, loading }: Props) {
```

改成：

```typescript
export function IntradayChart({ symbol, name, candles, loading }: Props) {
```

- [ ] **Step 2: 計算漲跌百分比（之前只算絕對值）**

當前 line 50–54：

```typescript
  const latest = candles[candles.length - 1];
  const first = candles[0];
  const change = latest && first ? latest.close - first.open : 0;
  const isUp = change > 0;
  const dirCls = isUp ? "text-bull" : change < 0 ? "text-bear" : "text-ink-muted";
```

改成（加 changePct 計算）：

```typescript
  const latest = candles[candles.length - 1];
  const first = candles[0];
  const change = latest && first ? latest.close - first.open : 0;
  const changePct = latest && first && first.open ? (change / first.open) * 100 : 0;
  const isUp = change > 0;
  const dirCls = isUp ? "text-bull" : change < 0 ? "text-bear" : "text-ink-muted";
```

- [ ] **Step 3: 在 return 區塊最頂端新增 header section（標題 + 大字股價）**

當前 line 56–58：

```tsx
  return (
    <div>
      {loading && candles.length === 0 ? (
```

改成：

```tsx
  return (
    <div>
      {/* 股票資訊 header — 名稱 · 代號 + 大字股價 + 漲跌百分比 */}
      <div className="mb-4">
        <div className="font-serif text-[22px] tracking-tight text-ink leading-tight">
          {name ?? "—"} · {symbol}
        </div>
        <div className="flex items-baseline gap-4 mt-1">
          <span className={`font-serif italic text-[44px] tabular-nums leading-none ${dirCls}`}>
            {latest ? latest.close.toFixed(2) : "—"}
          </span>
          {latest && (
            <span className={`text-[18px] tabular-nums ${dirCls}`}>
              {isUp ? "▲" : change < 0 ? "▾" : "—"} {Math.abs(change).toFixed(2)} ({changePct >= 0 ? "+" : ""}{changePct.toFixed(2)}%)
            </span>
          )}
        </div>
      </div>

      {candles.length === 0 ? (
```

注意：把 `{candles.length === 0 ? (` 從原本的 `{loading && candles.length === 0 ? (` 換掉（Task 5 已改成 `candles.length === 0` 條件，這裡只是位置上跟在 header 之後）。

- [ ] **Step 4: 移除原本下方的股價 + 漲跌顯示，只保留 toggle**

當前 line 123–147（區塊「報價 + toggle」）：

```tsx
      {/* 報價 + toggle */}
      {latest && (
        <div className="mt-2 flex items-baseline justify-between border-t border-line pt-2">
          <div className="flex items-baseline gap-3">
            <span className={`font-serif italic text-xl ${dirCls} tabular-nums`}>
              {latest.close.toFixed(2)}
            </span>
            <span className={`text-sm ${dirCls} tabular-nums`}>
              {isUp ? "▲" : change < 0 ? "▾" : "—"} {Math.abs(change).toFixed(2)}
            </span>
          </div>
          <div className="flex gap-2 text-xs">
            <button
              type="button"
              onClick={() => setShowVwap((v) => !v)}
              className={`px-2 py-1 border ${showVwap ? "border-accent text-accent" : "border-line text-ink-dim"}`}
            >{showVwap ? "✓" : ""} VWAP</button>
            <button
              type="button"
              onClick={() => setShowCdp((v) => !v)}
              className={`px-2 py-1 border ${showCdp ? "border-accent text-accent" : "border-line text-ink-dim"}`}
            >{showCdp ? "✓" : ""} CDP</button>
          </div>
        </div>
      )}
```

改成（只剩 toggle，靠右；border-top 仍保留作為視覺分隔）：

```tsx
      {/* Toggle 按鈕（VWAP / CDP） */}
      <div className="mt-2 flex justify-end gap-2 border-t border-line pt-2 text-xs">
        <button
          type="button"
          onClick={() => setShowVwap((v) => !v)}
          className={`px-2 py-1 border ${showVwap ? "border-accent text-accent" : "border-line text-ink-dim"}`}
        >{showVwap ? "✓" : ""} VWAP</button>
        <button
          type="button"
          onClick={() => setShowCdp((v) => !v)}
          className={`px-2 py-1 border ${showCdp ? "border-accent text-accent" : "border-line text-ink-dim"}`}
        >{showCdp ? "✓" : ""} CDP</button>
      </div>
```

注意：原本外層 `{latest && (...)}` 條件移除 — toggle 區塊在沒資料時也要顯示（雖然按了沒效果，但 UI 一致）。

- [ ] **Step 5: Monitor.tsx — 移除舊小標題、傳 name prop**

當前 line 132–137（chart section）：

```tsx
              ) : (
                <div className="border border-line p-7">
                  <div className="text-xs text-ink-dim mb-2">{selected}</div>
                  <IntradayChart symbol={selected} candles={candles} loading={candlesLoading} />
                </div>
              )}
```

改成：

```tsx
              ) : (
                <div className="border border-line p-7">
                  <IntradayChart
                    symbol={selected}
                    name={symbolNames[selected] ?? null}
                    candles={candles}
                    loading={candlesLoading}
                  />
                </div>
              )}
```

`symbolNames` map 已存在於 Monitor.tsx（line 68–72，已用於 TriggerHistoryTable），不用新增。

- [ ] **Step 6: TypeScript 檢查**

Run: `cd frontend && npx tsc --noEmit`

Expected: 無 error。`IntradayChart` 的 name prop 應該都被滿足。

- [ ] **Step 7: 手動驗（dev server 應該還開著或重啟）**

Run: `cd frontend && npm run dev`

手動測試：
1. 開 Monitor 頁，點 watchlist 任一檔
2. 圖表卡片頂端應有 `(中文名稱) · (代號)` 22px serif + 大字股價 44px italic + 漲跌絕對值 18px + 百分比
3. 漲時整組綠（text-bull）、跌時整組紅（text-bear）— 視 tailwind config 對應顏色
4. 圖表下方 border-t 之後只有 VWAP / CDP 兩個按鈕，靠右
5. 切換另一檔 → 標題立刻更新成新股票名稱 + 代號
6. 切換瞬間圖表區顯示「載入中…」+ 標題的股價先顯示 `—` 然後新資料進來才有數字

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/IntradayChart.tsx frontend/src/pages/Monitor.tsx
git commit -m "feat(IntradayChart): redesign header — name·symbol + large price

新版上方：22px '(中文名稱) · (代號)' / 44px italic 股價 / 18px 漲跌+百
分比，全部依漲跌染色。下方原報價移除、只保留 VWAP/CDP toggle 靠右。
Monitor.tsx 移除舊的 dim 小標題，把 name 從 watchlist map 傳進來。
"
```

---

## Task 8: 端到端驗證

**Files:** 全部前述檔案

- [ ] **Step 1: 完整 build（確認沒 type / lint regression）**

Run: `cd frontend && npm run build`

Expected: build 成功，無新 warning / error。

- [ ] **Step 2: backend smoke**

Run: `cd backend && python -m services.cdp`

Expected: 6 個 step 全綠 `All cdp smoke tests passed ✓`。

- [ ] **Step 3: 重啟 backend + 前端**

Backend：先停掉現有 uvicorn，重跑 `uvicorn main:app --host 0.0.0.0 --port 8000`。
Frontend：dev server 如果還開著，Vite HMR 應該夠；保險起見瀏覽器 Hard Refresh (Ctrl+Shift+R)。

- [ ] **Step 4: UAT 清單（盤中或盤後皆可，盤中體驗較完整）**

按 spec §9 跑：

| # | 動作 | 預期 |
|---|------|------|
| 1 | 開 Monitor 頁，點 2330 | 卡片頂端顯示 `台積電 · 2330` 22px + 大字股價 44px |
| 2 | 連續切 3 檔股票 | 中間瞬間「載入中…」、舊圖不殘留、舊 CDP 線不殘留 |
| 3 | 勾 CDP → F5 | CDP 仍勾、CDP 線顯示 |
| 4 | 點 1000+ 元股票 → 開 CDP | CDP 數字應為 5 的倍數 |
| 5 | CDP 範圍超寬的股票 → 開 CDP | 超出 ±10% 的線不畫，圖表 Y 軸保持合理範圍 |
| 6 | 盤中（若可）看分時走勢 | 最後一根 K 棒每筆成交即時跳動 |

- [ ] **Step 5: 最終 commit（如果 UAT 過程有微調）**

如果有調整，commit；沒調整就跳過。

```bash
git status   # 確認沒漏 commit 的檔案
```

---

## 自審筆記（spec 對應 task 表）

| Spec § | 內容 | Task |
|--------|------|------|
| §3 | Issue 1 標題重排版 | Task 7 |
| §4 | Issue 2 切換 loading | Task 3 + Task 5 |
| §5 | Issue 3 CDP localStorage | Task 2 + Task 6 |
| §6 | Issue 4 CDP tick 對齊 | Task 1 |
| §7 | Issue 5 Y 軸 ±10% | Task 4 |
| §8 | 改動檔案清單 | 全 task |
| §9 | 驗證 | Task 8 |
| §10 | 不做的事 | 無對應 task（明示排除） |

全部 spec 章節有 task 對應 ✓
