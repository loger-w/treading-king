# 分時走勢圖 X 軸固定到 13:30 — 實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `IntradayChart` 的 X 軸從 index-based 改成 time-based,範圍固定 9:00~13:30,讓開盤一打開圖就佔好完整視覺空間。

**Architecture:** 抽出純函式 `minuteOfDay` + 交易窗常數到 `src/lib/intraday-time.ts` 供 vitest 單測;`IntradayChart.tsx` 改寫 `scaleX(idx)` → `scaleX(minuteOfDay)`,前置計算 `minutesByIdx` 陣列供所有 call site 使用。X 軸標籤從等分比例 → 固定 6 個整點(9/10/11/12/13/13:30)。Hover 進入「未來區」(>最新 candle 的分鐘)時 setHover(null) 不顯示 crosshair。Client-side filter candles 到 540 ≤ m ≤ 810 擋試撮 / 盤後。

**Tech Stack:** React 18 + Vite + Vitest;`src/components/IntradayChart.tsx`、`src/lib/intraday-time.ts`(新);測試:`src/lib/intraday-time.test.ts`(新)、手動 dev-server 跑 chart。

**Spec:** `docs/superpowers/specs/2026-05-22-intraday-fixed-time-axis-design.md`

---

## File Structure

**Create:**
- `frontend/src/lib/intraday-time.ts` — 常數 + `minuteOfDay()` 純函式
- `frontend/src/lib/intraday-time.test.ts` — vitest 測試

**Modify:**
- `frontend/src/components/IntradayChart.tsx` — 全部圖表渲染改成 time-based

**Untouched:**
- Backend(`backend/routes/candles.py` 等)
- API shape
- 其他 components
- Y 軸 / CDP / MA / VWAP / 紅綠 fill 邏輯
- 今日 High/Low marker 顯示邏輯(只改位置算法的輸入)

---

## Task 1: Lib — intraday-time 常數 + minuteOfDay helper (TDD)

**Files:**
- Create: `frontend/src/lib/intraday-time.ts`
- Create: `frontend/src/lib/intraday-time.test.ts`

- [ ] **Step 1: Write failing test for minuteOfDay**

Create `frontend/src/lib/intraday-time.test.ts`:

```typescript
import { describe, test, expect } from "vitest";
import {
  MARKET_OPEN_MIN,
  MARKET_CLOSE_MIN,
  TRADING_MINUTES,
  minuteOfDay,
} from "./intraday-time";

describe("intraday-time constants", () => {
  test("9:00 = 540 分", () => {
    expect(MARKET_OPEN_MIN).toBe(540);
  });
  test("13:30 = 810 分", () => {
    expect(MARKET_CLOSE_MIN).toBe(810);
  });
  test("交易窗 = 270 分", () => {
    expect(TRADING_MINUTES).toBe(270);
  });
});

describe("minuteOfDay", () => {
  test("9:00:00+08:00 → 540", () => {
    expect(minuteOfDay("2026-05-22T09:00:00.000+08:00")).toBe(540);
  });
  test("9:01:30+08:00 → 541 (秒/毫秒忽略)", () => {
    expect(minuteOfDay("2026-05-22T09:01:30.000+08:00")).toBe(541);
  });
  test("13:30:00+08:00 → 810", () => {
    expect(minuteOfDay("2026-05-22T13:30:00.000+08:00")).toBe(810);
  });
  test("UTC 01:00 = 台北 09:00 → 540 (跨時區 ISO 也要正確)", () => {
    expect(minuteOfDay("2026-05-22T01:00:00.000Z")).toBe(540);
  });
  test("8:30:00+08:00 → 510 (試撮時段也要算對)", () => {
    expect(minuteOfDay("2026-05-22T08:30:00.000+08:00")).toBe(510);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```powershell
cd frontend; npm test -- src/lib/intraday-time.test.ts
```

Expected: FAIL with "Failed to resolve module './intraday-time'"

- [ ] **Step 3: Write implementation**

Create `frontend/src/lib/intraday-time.ts`:

```typescript
// 台股正盤交易時段(分鐘 of day,台北時間)
// IntradayChart X 軸固定範圍 = [MARKET_OPEN_MIN, MARKET_CLOSE_MIN]
export const MARKET_OPEN_MIN = 9 * 60;            // 540
export const MARKET_CLOSE_MIN = 13 * 60 + 30;     // 810
export const TRADING_MINUTES = MARKET_CLOSE_MIN - MARKET_OPEN_MIN; // 270

// 從 ISO timestamp(可帶任何 timezone offset)抓出台北時區的分鐘 of day。
// Fubon candle.date 通常是 "2026-05-22T09:00:00.000+08:00",但保險起見
// 用 Date.getUTCHours/Minutes + 固定 +480 分鐘 offset(台北永遠 UTC+8)。
export function minuteOfDay(iso: string): number {
  const d = new Date(iso);
  const utcMinutes = d.getUTCHours() * 60 + d.getUTCMinutes();
  return (utcMinutes + 8 * 60) % (24 * 60);
}
```

- [ ] **Step 4: Run tests, verify pass**

```powershell
cd frontend; npm test -- src/lib/intraday-time.test.ts
```

Expected: All 8 tests PASS.

- [ ] **Step 5: Commit**

```powershell
git add frontend/src/lib/intraday-time.ts frontend/src/lib/intraday-time.test.ts
git commit -m "feat(lib): intraday-time 常數 + minuteOfDay helper"
```

---

## Task 2: IntradayChart — 切換 X 軸到 time-based

**Files:**
- Modify: `frontend/src/components/IntradayChart.tsx`

這 task 一次落地所有圖表渲染元素的位置算法切換,因為 `scaleX` 的型別簽章從 `(i: number)` 改成 `(minuteOfDay: number)`,所有 call site(主價線、VWAP、Today High/Low marker、Volume bar、X 軸標籤)必須一起改才能編得過 / 跑得對。

- [ ] **Step 1: 加 import**

在 `IntradayChart.tsx` 頂部 import 區(line 1-5 附近)加:

```typescript
import {
  MARKET_OPEN_MIN,
  MARKET_CLOSE_MIN,
  TRADING_MINUTES,
  minuteOfDay,
} from "../lib/intraday-time";
```

- [ ] **Step 2: 在 useMemo 內前置計算 minutesByIdx + 改 scaleX 定義**

找到 line 117-120 附近:

```typescript
    const xRange = CHART_W - PAD_L - PAD_R;
    const yRange = CHART_H - PAD_T - PAD_B;
    const scaleX = (i: number) => PAD_L + (i / Math.max(candles.length - 1, 1)) * xRange;
    const scaleY = (v: number) => PAD_T + (1 - (v - yMin) / (yMax - yMin || 1)) * yRange;
```

改成:

```typescript
    const xRange = CHART_W - PAD_L - PAD_R;
    const yRange = CHART_H - PAD_T - PAD_B;
    // X 軸固定 9:00~13:30(分鐘 540~810);scaleX 吃「分鐘 of day」非 candle index
    const minutesByIdx = candles.map((c) => minuteOfDay(c.date));
    const scaleX = (m: number) =>
      PAD_L + ((m - MARKET_OPEN_MIN) / TRADING_MINUTES) * xRange;
    const scaleY = (v: number) => PAD_T + (1 - (v - yMin) / (yMax - yMin || 1)) * yRange;
```

- [ ] **Step 3: 改主價線 polyClose / VWAP polyVwap 的 call site**

找到 line 121-122:

```typescript
    const polyClose = candles.map((c, i) => `${scaleX(i)},${scaleY(c.close)}`).join(" ");
    const polyVwap = candles.map((c, i) => `${scaleX(i)},${scaleY(c.average)}`).join(" ");
```

改成:

```typescript
    const polyClose = candles.map((c, i) => `${scaleX(minutesByIdx[i])},${scaleY(c.close)}`).join(" ");
    const polyVwap = candles.map((c, i) => `${scaleX(minutesByIdx[i])},${scaleY(c.average)}`).join(" ");
```

- [ ] **Step 4: 改 volume bar slot 寬度(從 candles.length-based 改成 TRADING_MINUTES-based)**

找到 line 131-132:

```typescript
    const slotW = xRange / Math.max(candles.length, 1);
    const volBarW = Math.max(1, slotW * 0.7);
```

改成:

```typescript
    // 固定 slot 寬 = xRange / 270 分;不隨 candle 數量伸縮
    const slotW = xRange / TRADING_MINUTES;
    const volBarW = Math.max(1, slotW * 0.7);
```

- [ ] **Step 5: 把 minutesByIdx 加進 useMemo 回傳**

找到 line 179-185 useMemo 的 return:

```typescript
    return {
      yMin, yMax, scaleX, scaleY,
      polyClose, polyVwap, visibleCdpKeys, visibleMaKeys,
      todayHigh, todayHighIdx, todayLow, todayLowIdx,
      maxVolume, scaleVolY, volBarW,
      resolvedLabels,
    };
```

加 `minutesByIdx`:

```typescript
    return {
      yMin, yMax, scaleX, scaleY,
      polyClose, polyVwap, visibleCdpKeys, visibleMaKeys,
      todayHigh, todayHighIdx, todayLow, todayLowIdx,
      maxVolume, scaleVolY, volBarW,
      resolvedLabels,
      minutesByIdx,
    };
```

也更新空 candle case(line 75-86):

```typescript
    if (candles.length === 0) {
      return {
        yMin: 0, yMax: 0,
        scaleX: () => 0, scaleY: () => 0,
        polyClose: "", polyVwap: "",
        visibleCdpKeys: [] as Array<"ah" | "nh" | "cdp" | "nl" | "al">,
        visibleMaKeys: [] as Array<"sma_5" | "sma_20">,
        todayHigh: 0, todayHighIdx: -1, todayLow: 0, todayLowIdx: -1,
        maxVolume: 0, scaleVolY: (_: number) => 0, volBarW: 0,
        resolvedLabels: [] as ReturnType<typeof resolveCollisions>,
        minutesByIdx: [] as number[],
      };
    }
```

更新 useMemo 的解構(line 68-74):

```typescript
  const {
    yMin, yMax, scaleX, scaleY,
    polyClose, polyVwap, visibleCdpKeys, visibleMaKeys,
    todayHigh, todayHighIdx, todayLow, todayLowIdx,
    maxVolume, scaleVolY, volBarW,
    resolvedLabels,
    minutesByIdx,
  } = useMemo(() => {
```

- [ ] **Step 6: 改紅綠 fill polygon 的 x 端點(line 271、274)**

找到 line 271-274:

```typescript
            const points = [
              `${scaleX(0)},${baselineY}`,
              ...candles.map((c, i) => `${scaleX(i)},${scaleY(c.close)}`),
              `${scaleX(lastIdx)},${baselineY}`,
            ].join(" ");
```

改成(從候選 index 改成查 minutesByIdx):

```typescript
            const points = [
              `${scaleX(minutesByIdx[0])},${baselineY}`,
              ...candles.map((c, i) => `${scaleX(minutesByIdx[i])},${scaleY(c.close)}`),
              `${scaleX(minutesByIdx[lastIdx])},${baselineY}`,
            ].join(" ");
```

- [ ] **Step 7: 改 Today High / Low marker 位置(line 397-413, 414-430)**

找到 line 401:

```typescript
                <circle cx={scaleX(todayHighIdx)} cy={scaleY(todayHigh)} r="2.5"
```

改成:

```typescript
                <circle cx={scaleX(minutesByIdx[todayHighIdx])} cy={scaleY(todayHigh)} r="2.5"
```

同 block 接下來 line 404 的 `x={scaleX(todayHighIdx)}` 也改:

```typescript
                <text
                  x={scaleX(minutesByIdx[todayHighIdx])}
```

Today Low 同樣處理 — line 418 的 `cx={scaleX(todayLowIdx)}` 改成 `cx={scaleX(minutesByIdx[todayLowIdx])}`,line 421 的 `x={scaleX(todayLowIdx)}` 改成 `x={scaleX(minutesByIdx[todayLowIdx])}`。

- [ ] **Step 8: 改 Volume bar x 位置(line 450-463)**

找到 line 451:

```typescript
                const x = scaleX(i) - volBarW / 2;
```

改成:

```typescript
                const x = scaleX(minutesByIdx[i]) - volBarW / 2;
```

- [ ] **Step 9: 改 X 軸時間標籤(從等分比例 → 固定 6 個整點)**

找到 line 467-479:

```typescript
          {/* X 軸時間 label */}
          {[0, 0.25, 0.5, 0.75, 1].map((p) => {
            if (candles.length === 0) return null;
            const idx = Math.floor((candles.length - 1) * p);
            const x = scaleX(idx);
            const t = new Date(candles[idx].date);
            const hh = String(t.getHours()).padStart(2, "0");
            const mm = String(t.getMinutes()).padStart(2, "0");
            return (
              <text key={p} x={x} y={CHART_H - 8} textAnchor="middle"
                className="fill-ink-dim text-[12px] tabular-nums">{hh}:{mm}</text>
            );
          })}
```

整段替換成:

```typescript
          {/* X 軸時間 label — 固定 6 個整點(9/10/11/12/13/13:30),
              不隨 candle 數量變動 */}
          {[
            { min: 540, label: "9:00" },
            { min: 600, label: "10:00" },
            { min: 660, label: "11:00" },
            { min: 720, label: "12:00" },
            { min: 780, label: "13:00" },
            { min: 810, label: "13:30" },
          ].map(({ min, label }) => (
            <text key={min} x={scaleX(min)} y={CHART_H - 8} textAnchor="middle"
              className="fill-ink-dim text-[12px] tabular-nums">{label}</text>
          ))}
```

- [ ] **Step 10: 改 hover crosshair 渲染區段的 scaleX call**

找到 line 482-530 hover 渲染區段。內部 line 484:

```typescript
            const lineX = scaleX(hover.idx);
```

改成:

```typescript
            const lineX = scaleX(minutesByIdx[hover.idx]);
```

(`hover.idx` 還是 candle index,Task 3 才會調整 `handleMouseMove` 的 idx 計算邏輯;這邊只是把 idx 翻譯到 minuteOfDay 再餵 scaleX。)

- [ ] **Step 11: Typecheck**

```powershell
cd frontend; npm run build
```

Expected: 編譯成功,無 TypeScript error。(注意:Vite build 跑 `tsc -b && vite build`,看 tsc 過即可。)

- [ ] **Step 12: Commit**

```powershell
git add frontend/src/components/IntradayChart.tsx
git commit -m "refactor(chart): X 軸改成 time-based,刻度固定 9:00-13:30"
```

---

## Task 3: IntradayChart — Hover 進未來區不顯示 crosshair

**Files:**
- Modify: `frontend/src/components/IntradayChart.tsx`(只改 `handleMouseMove` 函式)

- [ ] **Step 1: 改寫 handleMouseMove**

找到 line 191-204:

```typescript
  function handleMouseMove(e: React.MouseEvent<SVGSVGElement>) {
    if (candles.length === 0) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const svgX = ((e.clientX - rect.left) / rect.width) * CHART_W;
    const svgY = ((e.clientY - rect.top) / rect.height) * CHART_H;
    // 只在 chart area 內才有 crosshair
    if (svgX < PAD_L || svgX > CHART_W - PAD_R || svgY < PAD_T || svgY > CHART_H - PAD_B) {
      setHover(null);
      return;
    }
    const ratio = (svgX - PAD_L) / (CHART_W - PAD_L - PAD_R);
    const idx = Math.max(0, Math.min(candles.length - 1, Math.round(ratio * (candles.length - 1))));
    setHover({ idx });
  }
```

整段改成:

```typescript
  function handleMouseMove(e: React.MouseEvent<SVGSVGElement>) {
    if (candles.length === 0 || minutesByIdx.length === 0) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const svgX = ((e.clientX - rect.left) / rect.width) * CHART_W;
    const svgY = ((e.clientY - rect.top) / rect.height) * CHART_H;
    // 只在 chart area 內才有 crosshair
    if (svgX < PAD_L || svgX > CHART_W - PAD_R || svgY < PAD_T || svgY > CHART_H - PAD_B) {
      setHover(null);
      return;
    }
    // svgX → 對應的分鐘 of day
    const ratio = (svgX - PAD_L) / (CHART_W - PAD_L - PAD_R);
    const mAtCursor = MARKET_OPEN_MIN + ratio * TRADING_MINUTES;
    // 超過最新 candle 的時間(未來區) → 不顯示 crosshair
    const latestM = minutesByIdx[minutesByIdx.length - 1];
    if (mAtCursor > latestM) {
      setHover(null);
      return;
    }
    // 找時間最接近的 candle(線性 scan 270 筆 OK)
    let bestIdx = 0;
    let bestDist = Math.abs(minutesByIdx[0] - mAtCursor);
    for (let i = 1; i < minutesByIdx.length; i++) {
      const d = Math.abs(minutesByIdx[i] - mAtCursor);
      if (d < bestDist) { bestDist = d; bestIdx = i; }
    }
    setHover({ idx: bestIdx });
  }
```

- [ ] **Step 2: Typecheck**

```powershell
cd frontend; npm run build
```

Expected: 編譯成功,無 TS error。

- [ ] **Step 3: Commit**

```powershell
git add frontend/src/components/IntradayChart.tsx
git commit -m "refactor(chart): hover 進未來區不顯示 crosshair"
```

---

## Task 4: IntradayChart — Pre-market / 盤後 candle filter

**Files:**
- Modify: `frontend/src/components/IntradayChart.tsx`(只在 useMemo 入口加 filter)

擋 Fubon 可能回的試撮(8:30-9:00)或盤後 candle,client-side 過濾到 540 ≤ m ≤ 810。

- [ ] **Step 1: 在 useMemo 入口加 filter,並把後續用到的 candles 改 alias**

`candles` 是 prop,不能直接 reassign。做法:在 useMemo 開頭算出 `filteredCandles`,接著加 `const candles = filteredCandles;` 把後續所有 useMemo 內部的 `candles` 引用都自動指到 filtered 版本(不用大量 rename)。

找到 useMemo callback body 開頭(目前在 line 74 之後,從 `if (candles.length === 0) { return { ... }; }` 開始)。

改寫成下面這個結構(放在 useMemo body 的最開頭、所有現有邏輯之前):

```typescript
  } = useMemo(() => {
    // 擋試撮 / 盤後 candle:只留正盤時段(9:00 ≤ 分鐘 ≤ 13:30)
    const filteredCandles = candles.filter((c) => {
      const m = minuteOfDay(c.date);
      return m >= MARKET_OPEN_MIN && m <= MARKET_CLOSE_MIN;
    });
    // 用一個區域變數 shadow 掉 prop,後續 useMemo 內部所有 `candles`
    // 自動指到 filtered 版本(避免大量 rename)
    const candles = filteredCandles;

    if (candles.length === 0) {
      return {
        yMin: 0, yMax: 0,
        scaleX: () => 0, scaleY: () => 0,
        polyClose: "", polyVwap: "",
        visibleCdpKeys: [] as Array<"ah" | "nh" | "cdp" | "nl" | "al">,
        visibleMaKeys: [] as Array<"sma_5" | "sma_20">,
        todayHigh: 0, todayHighIdx: -1, todayLow: 0, todayLowIdx: -1,
        maxVolume: 0, scaleVolY: (_: number) => 0, volBarW: 0,
        resolvedLabels: [] as ReturnType<typeof resolveCollisions>,
        minutesByIdx: [] as number[],
      };
    }
    // ⬇ 從這邊開始是原本 useMemo body(從 `const closes = candles.map(...)` 起),
    //   不用改 — `candles` 已被上面的 const 重新綁定到 filteredCandles。
    const closes = candles.map((c) => c.close);
    // ...(原本既有的 useMemo body 全部保留)
```

要點:
- `const candles = filteredCandles;` 在 TypeScript 是合法的「shadow」— 同名區域變數覆蓋外部 prop,後續所有 `candles.xxx` 自動指到 filtered 版本
- 把原本的 `if (candles.length === 0) { return {...}; }` 那段**整段刪掉、移到 filter 後**,新的 empty 分支多回一個 `minutesByIdx: [] as number[]`(Task 2 已加,filter 這 task 也要)
- useMemo 外的 `candles` 引用(JSX 渲染用 prop;line 191、194、202、210、211、254、269、274、432、450、469、472、483)**完全不動** — 那是 React render path 拿的 prop

⚠ ESLint 若開了 `no-shadow` 規則會抱怨;trading-king 沒設這條規則(看 `frontend/.eslintrc` 或預設 vite-react config 通常不擋),Step 2 編譯能過即 OK。

- [ ] **Step 2: Typecheck**

```powershell
cd frontend; npm run build
```

Expected: 編譯成功,無 TS error。

- [ ] **Step 3: 確認 vitest 還是過**

```powershell
cd frontend; npm test
```

Expected: 全部測試 PASS(只有 chart-labels + intraday-time 兩組)。

- [ ] **Step 4: Commit**

```powershell
git add frontend/src/components/IntradayChart.tsx
git commit -m "feat(chart): 過濾試撮 / 盤後 candle(9:00 ≤ m ≤ 13:30)"
```

---

## Task 5: 手動驗收(dev server)

**Files:** 無新改動,純驗證。

- [ ] **Step 1: 開 dev server**

```powershell
.\start.ps1
```

或單獨開 frontend:

```powershell
cd frontend; npm run dev
```

- [ ] **Step 2: 在瀏覽器打開 monitor 頁,挑一支股票**

開 http://localhost:5173(或 vite 給的 port),從自選 / 搜尋挑一支股票,觀察 IntradayChart。

- [ ] **Step 3: 跑 spec 的驗收 scenario**

驗收清單(對照 spec `## 驗收標準`):

- [ ] X 軸顯示 6 個標籤:9:00 / 10:00 / 11:00 / 12:00 / 13:00 / 13:30
- [ ] X 軸範圍固定,不管現在幾點都是完整窗(交易中右邊空白是預期)
- [ ] 主價線停在最新 candle,不延伸到 13:30
- [ ] VWAP 線、紅綠 fill 區也停在最新 candle
- [ ] 今日 High / Low marker 位於正確的時間位置(肉眼對照 candle 數量大致 OK 即可)
- [ ] Hover 在已成交區 → crosshair 出現,snap 到對應 candle 時間
- [ ] Hover 在未來區(右側空白) → 無 crosshair(關鍵:cursor 跨過最新 candle 之後 crosshair 應消失)
- [ ] 切到不同 symbol → X 軸結構穩定一致

- [ ] **Step 4: 如有問題,記錄 + 修;沒問題收尾**

把任何發現的 bug 開回 Task 2/3/4 修。全綠就完成。

---

## 完成檢查

- [ ] Task 1-4 全部 commit
- [ ] `npm test` 全綠
- [ ] `npm run build` 編譯成功
- [ ] 手動驗收清單全綠

完成後可以推 PR / merge 到 main。
