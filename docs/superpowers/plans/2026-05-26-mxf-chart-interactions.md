# MXF Chart Interactions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add scroll-wheel zoom + drag-pan + adaptive time axis + 44px price header to MXF intraday chart, with viewRange driven by Approach A (slice candles by index).

**Architecture:** All SVG render functions stay stateless and receive a sliced subset of candles. New `viewRange: { startIdx, endIdx }` state lives in `MXFIntradayChart`. WS push auto-shifts viewRange iff user is anchored to the right. Three pure helpers (`dayOpenBaseline`, `computeNewViewRange`, `pickInterval`) extracted to `lib/mxf-chart.ts` with unit tests.

**Tech Stack:** React 18, TypeScript 5.5, vitest 4 (test runner), Tailwind 3.4, existing `frontend/src/lib/chart-svg.tsx` SVG helpers.

**Spec:** `docs/superpowers/specs/2026-05-26-mxf-chart-interactions-design.md` (committed `abf5cf2`)

**Branch:** Continue on `feat/mxf-intraday-chart` (current working branch with prior chart work).

---

## File Structure

- **Create**: `frontend/src/lib/mxf-chart.ts` — pure helpers (`dayOpenBaseline`, `computeNewViewRange`, `pickInterval`)
- **Create**: `frontend/src/lib/mxf-chart.test.ts` — unit tests for above
- **Modify**: `frontend/src/components/MXFIntradayChart.tsx` — header JSX, viewRange state, slicing, wheel/drag handlers, time axis render, session markers

No new components. All UI lives in `MXFIntradayChart.tsx` — its responsibility is the whole MXF chart panel.

---

## Phase 1 — Header (independent, ships alone)

### Task 1: `dayOpenBaseline` helper (TDD)

**Files:**
- Create: `frontend/src/lib/mxf-chart.ts`
- Test: `frontend/src/lib/mxf-chart.test.ts`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/lib/mxf-chart.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import { dayOpenBaseline } from "./mxf-chart";
import type { MXFCandle } from "../lib/api";

function makeCandle(date: string, open: number): MXFCandle {
  return { date, open, high: open + 1, low: open - 1, close: open, volume: 100, average: open };
}

describe("dayOpenBaseline", () => {
  const now = new Date("2026-05-26T10:00:00+08:00");

  it("returns null for empty candles", () => {
    expect(dayOpenBaseline([], now)).toBeNull();
  });

  it("returns null when no candle at or after 08:45 today", () => {
    const candles = [makeCandle("2026-05-26T03:00:00+08:00", 100)];
    expect(dayOpenBaseline(candles, now)).toBeNull();
  });

  it("returns first 08:45+ candle.open for today", () => {
    const candles = [
      makeCandle("2026-05-26T03:00:00+08:00", 100),
      makeCandle("2026-05-26T08:45:00+08:00", 105),
      makeCandle("2026-05-26T09:00:00+08:00", 106),
    ];
    expect(dayOpenBaseline(candles, now)).toBe(105);
  });

  it("returns today's day open, ignores yesterday's", () => {
    const candles = [
      makeCandle("2026-05-25T08:45:00+08:00", 95),
      makeCandle("2026-05-26T08:45:00+08:00", 105),
    ];
    expect(dayOpenBaseline(candles, now)).toBe(105);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- src/lib/mxf-chart.test.ts`
Expected: FAIL — module `./mxf-chart` cannot be resolved.

- [ ] **Step 3: Implement to pass**

Create `frontend/src/lib/mxf-chart.ts`:

```ts
import type { MXFCandle } from "./api";

const DAY_OPEN_MIN = 8 * 60 + 45;  // 08:45 = 525

/**
 * 找今日日盤開盤價 — 用 `now` 判定「今天」（本地時區），
 * 然後找第一根 minuteOfDay >= 08:45 且日期等於 now.toDateString() 的 candle.open。
 * 凌晨夜盤中（今日日盤未開）時回傳 null。
 */
export function dayOpenBaseline(candles: MXFCandle[], now: Date): number | null {
  const today = now.toDateString();
  for (const c of candles) {
    const d = new Date(c.date);
    if (d.toDateString() !== today) continue;
    const m = d.getHours() * 60 + d.getMinutes();
    if (m >= DAY_OPEN_MIN) return c.open;
  }
  return null;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm test -- src/lib/mxf-chart.test.ts`
Expected: 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/mxf-chart.ts frontend/src/lib/mxf-chart.test.ts
git commit -m "$(cat <<'EOF'
feat(mxf): add dayOpenBaseline helper for change% baseline

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Header JSX (symbol + 44px price + change/pct)

**Files:**
- Modify: `frontend/src/components/MXFIntradayChart.tsx`

- [ ] **Step 1: Import the helper**

Add to imports at top of `frontend/src/components/MXFIntradayChart.tsx`:

```ts
import { dayOpenBaseline } from "../lib/mxf-chart";
```

- [ ] **Step 2: Compute baseline + change inside component**

Inside `MXFIntradayChart` function, after the existing `useMemo` block (around the line where `innerW` is computed), add:

```ts
const baselineOpen = dayOpenBaseline(candles, new Date());
const latest = candles.length > 0 ? candles[candles.length - 1] : null;
const change = latest && baselineOpen ? latest.close - baselineOpen : 0;
const changePct = latest && baselineOpen ? (change / baselineOpen) * 100 : 0;
const isUp = change > 0;
const dirCls = change > 0 ? "text-bull" : change < 0 ? "text-bear" : "text-ink-muted";
```

- [ ] **Step 3: Add header block to JSX**

In the main return JSX of `MXFIntradayChart`, **before** the existing `{/* Toolbar */}` block, add:

```tsx
{/* Top-left header — symbol + 大字現價 + 漲跌% */}
<div className="mb-4">
  <div className="font-serif text-[22px] tracking-tight text-ink leading-tight font-medium">
    {symbol ?? "—"}
  </div>
  <div className="flex items-baseline gap-4 mt-1">
    <span className={`font-serif italic text-[44px] tabular-nums leading-none ${dirCls}`}>
      {latest ? latest.close : "—"}
    </span>
    {latest && baselineOpen && (
      <span className={`text-[18px] tabular-nums ${dirCls}`}>
        {isUp ? "▲" : change < 0 ? "▾" : "—"} {Math.abs(change)} ({changePct >= 0 ? "+" : ""}{changePct.toFixed(2)}%)
      </span>
    )}
  </div>
</div>
```

- [ ] **Step 4: Run typecheck + manual visual verify**

Run `cd frontend && npx tsc --noEmit`
Expected: no output (no errors).

Open browser → `http://localhost:5173`, click MXF page.
Expected: large italic price visible top-left of chart panel; change/pct in 上漲紅 / 下跌綠 / 平盤 ink-muted. 凌晨夜盤中只看到現價、沒有 change/pct。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/MXFIntradayChart.tsx
git commit -m "$(cat <<'EOF'
feat(mxf): top-left price header (22px symbol + 44px italic price + change%)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 2 — Zoom data model (slice by index)

### Task 3: `computeNewViewRange` helper (TDD)

**Files:**
- Modify: `frontend/src/lib/mxf-chart.ts`
- Modify: `frontend/src/lib/mxf-chart.test.ts`

- [ ] **Step 1: Write the failing tests**

Append to `frontend/src/lib/mxf-chart.test.ts`:

```ts
import { computeNewViewRange } from "./mxf-chart";

describe("computeNewViewRange", () => {
  it("zooms in keeping anchor at center", () => {
    const result = computeNewViewRange({
      prevRange: { startIdx: 25, endIdx: 74 },  // 50 visible
      mouseRatio: 0.5,
      deltaY: -100,  // wheel up = zoom in
      candlesLen: 100,
      innerW: 600,
      minCandlePx: 6,
    });
    const newVisible = result.endIdx - result.startIdx + 1;
    expect(newVisible).toBeLessThan(50);
    // anchor candle (idx 50) should remain near center
    const anchorPos = (50 - result.startIdx) / (newVisible - 1);
    expect(anchorPos).toBeCloseTo(0.5, 1);
  });

  it("zooms out keeping anchor at right edge", () => {
    const result = computeNewViewRange({
      prevRange: { startIdx: 25, endIdx: 74 },  // 50 visible
      mouseRatio: 1.0,  // mouse at right edge
      deltaY: 100,  // wheel down = zoom out
      candlesLen: 100,
      innerW: 600,
      minCandlePx: 6,
    });
    expect(result.endIdx).toBe(74);  // right anchor preserved
    expect(result.endIdx - result.startIdx + 1).toBeGreaterThan(50);
  });

  it("clamps min visible to 5", () => {
    const result = computeNewViewRange({
      prevRange: { startIdx: 47, endIdx: 52 },  // 6 visible
      mouseRatio: 0.5,
      deltaY: -100,  // zoom in further
      candlesLen: 100,
      innerW: 600,
      minCandlePx: 6,
    });
    expect(result.endIdx - result.startIdx + 1).toBeGreaterThanOrEqual(5);
  });

  it("clamps max visible by minCandlePx", () => {
    const result = computeNewViewRange({
      prevRange: { startIdx: 0, endIdx: 99 },  // 100 visible already at max
      mouseRatio: 0.5,
      deltaY: 100,  // zoom out
      candlesLen: 1000,
      innerW: 600,
      minCandlePx: 6,
    });
    const maxVisible = Math.floor(600 / 6);  // 100
    expect(result.endIdx - result.startIdx + 1).toBeLessThanOrEqual(maxVisible);
  });

  it("clamps when newStart would go negative", () => {
    const result = computeNewViewRange({
      prevRange: { startIdx: 0, endIdx: 9 },  // 10 visible at left edge
      mouseRatio: 0.0,  // mouse at left
      deltaY: 100,  // zoom out
      candlesLen: 100,
      innerW: 600,
      minCandlePx: 6,
    });
    expect(result.startIdx).toBeGreaterThanOrEqual(0);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- src/lib/mxf-chart.test.ts`
Expected: 5 new tests FAIL — `computeNewViewRange` not exported.

- [ ] **Step 3: Implement to pass**

Append to `frontend/src/lib/mxf-chart.ts`:

```ts
export interface ViewRange {
  startIdx: number;
  endIdx: number;
}

export interface ComputeNewViewRangeArgs {
  prevRange: ViewRange;
  mouseRatio: number;     // 0 = left edge, 1 = right edge
  deltaY: number;          // wheel deltaY: positive = zoom out, negative = zoom in
  candlesLen: number;
  innerW: number;
  minCandlePx: number;
}

const ZOOM_FACTOR = 1.15;
const MIN_VISIBLE = 5;

export function computeNewViewRange(args: ComputeNewViewRangeArgs): ViewRange {
  const { prevRange, mouseRatio, deltaY, candlesLen, innerW, minCandlePx } = args;
  const visible = prevRange.endIdx - prevRange.startIdx + 1;
  const factor = deltaY > 0 ? ZOOM_FACTOR : 1 / ZOOM_FACTOR;
  let newVisible = Math.round(visible * factor);
  // Clamp visible to [MIN_VISIBLE, maxByPx, candlesLen]
  const maxByPx = Math.floor(innerW / minCandlePx);
  newVisible = Math.max(MIN_VISIBLE, Math.min(maxByPx, candlesLen, newVisible));

  // Anchor: keep candle under cursor at same pixel
  const anchorIdx = prevRange.startIdx + Math.round(mouseRatio * (visible - 1));
  let newStart = Math.round(anchorIdx - mouseRatio * (newVisible - 1));
  // Clamp newStart to [0, candlesLen - newVisible]
  newStart = Math.max(0, Math.min(candlesLen - newVisible, newStart));

  return { startIdx: newStart, endIdx: newStart + newVisible - 1 };
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npm test -- src/lib/mxf-chart.test.ts`
Expected: all 9 tests (4 prior + 5 new) pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/mxf-chart.ts frontend/src/lib/mxf-chart.test.ts
git commit -m "$(cat <<'EOF'
feat(mxf): add computeNewViewRange helper for zoom math

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Add viewRange state + init/reset effect

**Files:**
- Modify: `frontend/src/components/MXFIntradayChart.tsx`

- [ ] **Step 1: Add MIN_CANDLE_PX constant + state**

Below the existing `const PAD_B = 28;` line (around line 22), add:

```ts
const MIN_CANDLE_PX = 6;
```

Update imports at top of file to add `useEffect`:

```ts
import { useEffect, useMemo, useState } from "react";
```

Update the imports from `../lib/mxf-chart` (already has `dayOpenBaseline`):

```ts
import { dayOpenBaseline, computeNewViewRange, type ViewRange } from "../lib/mxf-chart";
```

Inside `MXFIntradayChart`, below the existing hover state declaration, add:

```ts
const [viewRange, setViewRange] = useState<ViewRange | null>(null);
```

- [ ] **Step 2: Add init/reset effect**

After all `useState` calls in the component, add:

```ts
// Init / reset viewRange when candles arrive or are cleared
useEffect(() => {
  if (candles.length === 0) {
    setViewRange(null);
    return;
  }
  if (viewRange === null) {
    const maxVisible = Math.floor(innerW / MIN_CANDLE_PX);
    const startIdx = Math.max(0, candles.length - maxVisible);
    setViewRange({ startIdx, endIdx: candles.length - 1 });
  }
  // viewRange not in deps to avoid re-run when zoom/pan updates it
  // eslint-disable-next-line react-hooks/exhaustive-deps
}, [candles.length, innerW]);
```

Note: `innerW` is computed inside component from `CHART_W - PAD_L - PAD_R` and is constant — but listed in deps to satisfy lint and survive any future refactor that makes it dynamic.

- [ ] **Step 3: Verify typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 4: Commit (no behavior change yet)**

```bash
git add frontend/src/components/MXFIntradayChart.tsx
git commit -m "$(cat <<'EOF'
feat(mxf): viewRange state + init/reset effect (no rendering change yet)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Wire slicing through all render paths

**Files:**
- Modify: `frontend/src/components/MXFIntradayChart.tsx`

- [ ] **Step 1: Compute visibleCandles + adjust dependent values**

Inside the component, after the init/reset effect, add a `useMemo` that derives all view-dependent values:

```ts
const {
  visibleCandles, visibleSessions, visibleMa5, visibleMa20,
  yMinView, yMaxView, todayHighInView, todayLowInView, maxVolumeInView,
} = useMemo(() => {
  if (!viewRange || candles.length === 0) {
    return {
      visibleCandles: [] as typeof candles,
      visibleSessions: [] as ChartSession[],
      visibleMa5: [] as number[],
      visibleMa20: [] as number[],
      yMinView: 0, yMaxView: 0,
      todayHighInView: { value: 0, idx: -1 },
      todayLowInView: { value: 0, idx: -1 },
      maxVolumeInView: 1,
    };
  }
  const start = viewRange.startIdx;
  const end = viewRange.endIdx + 1;
  const visibleCandles = candles.slice(start, end);
  const visibleSessions = inferSessions(visibleCandles);
  const visibleMa5 = ma5.slice(start, end);
  const visibleMa20 = ma20.slice(start, end);

  const lows = visibleCandles.map((c) => c.low);
  const highs = visibleCandles.map((c) => c.high);
  const yMinView = Math.min(...lows) * 0.998;
  const yMaxView = Math.max(...highs) * 1.002;

  // 今日高低 marker — 從完整 candles 算，但只在 idx 落在 viewRange 內時才顯示
  let todayHighIdx = 0;
  let todayLowIdx = 0;
  for (let i = 1; i < candles.length; i++) {
    if (candles[i].high > candles[todayHighIdx].high) todayHighIdx = i;
    if (candles[i].low < candles[todayLowIdx].low) todayLowIdx = i;
  }
  const todayHighInView = todayHighIdx >= start && todayHighIdx < end
    ? { value: candles[todayHighIdx].high, idx: todayHighIdx - start }
    : { value: 0, idx: -1 };
  const todayLowInView = todayLowIdx >= start && todayLowIdx < end
    ? { value: candles[todayLowIdx].low, idx: todayLowIdx - start }
    : { value: 0, idx: -1 };

  const maxVolumeInView = Math.max(1, ...visibleCandles.map((c) => c.volume));

  return {
    visibleCandles, visibleSessions, visibleMa5, visibleMa20,
    yMinView, yMaxView, todayHighInView, todayLowInView, maxVolumeInView,
  };
}, [candles, viewRange, ma5, ma20]);
```

- [ ] **Step 2: Update scale functions to use view values**

Replace the existing `sx` / `sy` definitions:

```ts
const sx = (iso: string) => PAD_L + scaleX_compressed(iso, visibleSessions, innerW);
const sy = (v: number) => PAD_T + scaleY_clamped(v, yMinView, yMaxView, innerH);
```

- [ ] **Step 3: Replace `candles` with `visibleCandles` in render**

In JSX, replace these usages (search the SVG block):

- `<CandlestickSeries candles={candles}` → `<CandlestickSeries candles={visibleCandles}`
- `<LineSeries candles={candles}` (VWAP) → `<LineSeries candles={visibleCandles}`
- `<MALine candles={candles} maValues={ma5}` → `<MALine candles={visibleCandles} maValues={visibleMa5}`
- `<MALine candles={candles} maValues={ma20}` → `<MALine candles={visibleCandles} maValues={visibleMa20}`
- `<VolumeSubChart candles={candles}` → `<VolumeSubChart candles={visibleCandles}`
- `sessionBoundaries(sessions, innerW)` → `sessionBoundaries(visibleSessions, innerW)`

- [ ] **Step 4: Update today high/low marker render**

Replace existing `{showHighLow && candles.length > 0 && (...)}` block with:

```tsx
{showHighLow && todayHighInView.idx >= 0 && (
  <g>
    <line x1={PAD_L} x2={CHART_W - PAD_R} y1={sy(todayHighInView.value)} y2={sy(todayHighInView.value)}
      stroke="#d9534f" strokeWidth={0.5} strokeDasharray="1 3" />
    <text x={CHART_W - PAD_R + 4} y={sy(todayHighInView.value) + 3} fontSize={10} fill="#d9534f">
      H {todayHighInView.value}
    </text>
  </g>
)}
{showHighLow && todayLowInView.idx >= 0 && (
  <g>
    <line x1={PAD_L} x2={CHART_W - PAD_R} y1={sy(todayLowInView.value)} y2={sy(todayLowInView.value)}
      stroke="#2e7d32" strokeWidth={0.5} strokeDasharray="1 3" />
    <text x={CHART_W - PAD_R + 4} y={sy(todayLowInView.value) + 3} fontSize={10} fill="#2e7d32">
      L {todayLowInView.value}
    </text>
  </g>
)}
```

- [ ] **Step 5: Adjust the existing useMemo block (ma5/ma20 stay full-length, drop unused)**

The original `useMemo` already returns `ma5`, `ma20` over full `candles` — keep those. Other values from that memo (`yMin`/`yMax`/`todayHigh`/`todayLow`/`maxVolume`) are now superseded by the new memo. Remove them from the old memo's return + destructuring to avoid confusion. The old memo becomes:

```ts
const { sessions, ma5, ma20 } = useMemo(() => {
  if (candles.length === 0) {
    return {
      sessions: [] as ChartSession[],
      ma5: [] as number[], ma20: [] as number[],
    };
  }
  const sess: ChartSession[] = inferSessions(candles);
  const closes = candles.map((c) => c.close);
  const ma5 = computeMA(closes, 5);
  const ma20 = computeMA(closes, 20);
  return { sessions: sess, ma5, ma20 };
}, [candles]);
```

(`sessions` is still computed from full candles for any external use — but render uses `visibleSessions` instead. We can drop the unused `sessions` from destructuring if not referenced elsewhere; check `sx`/`sy` definitions.)

After this task `sessions` is unused — drop it:

```ts
const { ma5, ma20 } = useMemo(...);
```

- [ ] **Step 6: Verify typecheck + visual**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

Open browser, click MXF page.
Expected: default view shows last ~148 candles (1m TF) instead of all ~1500. Other TFs (60m) may show all if total < 148. Other features (VWAP/MA/VOL/Crosshair/高低) still work — crosshair will misalign because hit-test still indexes full candles; that's fixed in Task 6.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/MXFIntradayChart.tsx
git commit -m "$(cat <<'EOF'
feat(mxf): slice candles by viewRange in all render paths

Default view = last 148 candles (innerW 888 / 6px). Crosshair still
indexes full candles — fixed next task.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Crosshair slice-relative indexing

**Files:**
- Modify: `frontend/src/components/MXFIntradayChart.tsx`

- [ ] **Step 1: Update handleMouseMove to scan visibleCandles**

Replace the existing `handleMouseMove` function with:

```ts
function handleMouseMove(e: React.MouseEvent<SVGSVGElement>) {
  if (visibleCandles.length === 0) return;
  const rect = e.currentTarget.getBoundingClientRect();
  const svgX = ((e.clientX - rect.left) / rect.width) * CHART_W;
  const svgY = ((e.clientY - rect.top) / rect.height) * CHART_H;
  const chartBottomY = PAD_T + innerH;
  if (svgX < PAD_L || svgX > CHART_W - PAD_R || svgY < PAD_T || svgY > chartBottomY) {
    setHover(null);
    return;
  }
  // Pixel-based nearest candle within visibleCandles
  let bestIdx = 0;
  let bestDist = Math.abs(sx(visibleCandles[0].date) - svgX);
  for (let i = 1; i < visibleCandles.length; i++) {
    const cx = sx(visibleCandles[i].date);
    if (Number.isNaN(cx)) continue;
    const d = Math.abs(cx - svgX);
    if (d < bestDist) { bestDist = d; bestIdx = i; }
  }
  setHover({ idx: bestIdx });
}
```

- [ ] **Step 2: Update crosshair render to use visibleCandles + maxVolumeInView**

Replace the `{hover && candles[hover.idx] && (() => { ... })()}` block with:

```tsx
{hover && visibleCandles[hover.idx] && (() => {
  const c = visibleCandles[hover.idx];
  const lineX = sx(c.date);
  const lineY = sy(c.close);
  if (Number.isNaN(lineX)) return null;
  const verticalEndY = showVolume
    ? PAD_T + innerH + 8 + VOL_H
    : PAD_T + innerH;
  const d = new Date(c.date);
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  const volBarY = PAD_T + innerH + 8 + VOL_H - (c.volume / maxVolumeInView) * VOL_H;
  return (
    <g pointerEvents="none">
      <line x1={lineX} y1={PAD_T} x2={lineX} y2={verticalEndY}
        stroke="#8a8273" strokeWidth="0.5" strokeDasharray="3 3" opacity="0.7" />
      <line x1={PAD_L} y1={lineY} x2={CHART_W - PAD_R} y2={lineY}
        stroke="#8a8273" strokeWidth="0.5" strokeDasharray="3 3" opacity="0.7" />
      <circle cx={lineX} cy={lineY} r="2.5" fill="#ede4d3" />
      <rect x={2} y={lineY - 7} width={PAD_L - 6} height="14" rx="1.5"
        fill="#2e2a22" stroke="#8a8273" strokeWidth="0.5" />
      <text x={PAD_L - 4} y={lineY + 3} textAnchor="end"
        fill="#ede4d3" fontSize="12" className="tabular-nums font-medium">
        {c.close}
      </text>
      <rect x={lineX - 18} y={CHART_H - PAD_B + 2} width="36" height="14" rx="1.5"
        fill="#2e2a22" stroke="#8a8273" strokeWidth="0.5" />
      <text x={lineX} y={CHART_H - PAD_B + 12} textAnchor="middle"
        fill="#ede4d3" fontSize="12" className="tabular-nums font-medium">
        {hh}:{mm}
      </text>
      {showVolume && (
        <text x={CHART_W - PAD_R + 4} y={volBarY + 3} textAnchor="start"
          fill="#ede4d3" fontSize="11" className="tabular-nums">
          {c.volume}
        </text>
      )}
    </g>
  );
})()}
```

- [ ] **Step 3: Verify typecheck + visual**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

Open browser. Hover over MXF chart. Expected: crosshair tracks closest visible candle correctly (no offset).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/MXFIntradayChart.tsx
git commit -m "$(cat <<'EOF'
fix(mxf): crosshair indexes visibleCandles (slice-relative)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 3 — Zoom interactions

### Task 7: Scroll wheel zoom

**Files:**
- Modify: `frontend/src/components/MXFIntradayChart.tsx`

- [ ] **Step 1: Add handleWheel function**

Inside the component, after `handleMouseLeave`, add:

```ts
function handleWheel(e: React.WheelEvent<SVGSVGElement>) {
  e.preventDefault();  // always block page scroll on chart
  if (!viewRange) return;
  const rect = e.currentTarget.getBoundingClientRect();
  const svgX = ((e.clientX - rect.left) / rect.width) * CHART_W;
  if (svgX < PAD_L || svgX > CHART_W - PAD_R) return;
  const mouseRatio = (svgX - PAD_L) / innerW;
  const newRange = computeNewViewRange({
    prevRange: viewRange,
    mouseRatio,
    deltaY: e.deltaY,
    candlesLen: candles.length,
    innerW,
    minCandlePx: MIN_CANDLE_PX,
  });
  setViewRange(newRange);
}
```

- [ ] **Step 2: Wire onWheel to SVG**

In the JSX `<svg>` element (search for `viewBox={\`0 0 ${CHART_W} ${CHART_H}\`}`), add `onWheel={handleWheel}` to the existing props.

- [ ] **Step 3: Verify typecheck + visual**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

Open browser, scroll wheel on MXF chart:
- Wheel up → fewer, wider candles
- Wheel down → more, narrower candles (stops at min 6px wide)
- Mouse position should remain the anchor — the candle under cursor stays at the same x

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/MXFIntradayChart.tsx
git commit -m "$(cat <<'EOF'
feat(mxf): scroll-wheel zoom with mouse-position anchor

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Drag-to-pan + cursor state

**Files:**
- Modify: `frontend/src/components/MXFIntradayChart.tsx`

- [ ] **Step 1: Add drag state + refs**

Add `useRef` to imports:

```ts
import { useEffect, useMemo, useRef, useState } from "react";
```

Inside component, after viewRange state:

```ts
const [isDragging, setIsDragging] = useState(false);
const dragStartX = useRef(0);
const dragStartRange = useRef<ViewRange | null>(null);
```

- [ ] **Step 2: Add handleMouseDown / extend handleMouseMove / handleMouseUp**

Add new handler:

```ts
function handleMouseDown(e: React.MouseEvent<SVGSVGElement>) {
  if (!viewRange) return;
  const rect = e.currentTarget.getBoundingClientRect();
  const svgX = ((e.clientX - rect.left) / rect.width) * CHART_W;
  if (svgX < PAD_L || svgX > CHART_W - PAD_R) return;
  setIsDragging(true);
  setHover(null);
  dragStartX.current = e.clientX;
  dragStartRange.current = viewRange;
}

function handleMouseUp() {
  setIsDragging(false);
}
```

Modify `handleMouseMove` to handle drag mode first:

```ts
function handleMouseMove(e: React.MouseEvent<SVGSVGElement>) {
  if (isDragging && dragStartRange.current) {
    const rect = e.currentTarget.getBoundingClientRect();
    const dx = e.clientX - dragStartX.current;
    const size = dragStartRange.current.endIdx - dragStartRange.current.startIdx + 1;
    // pxPerCandle in actual viewport pixels
    const viewportPxPerCandle = (rect.width * (innerW / CHART_W)) / size;
    const deltaIdx = -Math.round(dx / viewportPxPerCandle);
    let newStart = dragStartRange.current.startIdx + deltaIdx;
    newStart = Math.max(0, Math.min(candles.length - size, newStart));
    setViewRange({ startIdx: newStart, endIdx: newStart + size - 1 });
    return;
  }
  // Existing crosshair hit-test (Task 6 version)
  if (visibleCandles.length === 0) return;
  const rect = e.currentTarget.getBoundingClientRect();
  const svgX = ((e.clientX - rect.left) / rect.width) * CHART_W;
  const svgY = ((e.clientY - rect.top) / rect.height) * CHART_H;
  const chartBottomY = PAD_T + innerH;
  if (svgX < PAD_L || svgX > CHART_W - PAD_R || svgY < PAD_T || svgY > chartBottomY) {
    setHover(null);
    return;
  }
  let bestIdx = 0;
  let bestDist = Math.abs(sx(visibleCandles[0].date) - svgX);
  for (let i = 1; i < visibleCandles.length; i++) {
    const cx = sx(visibleCandles[i].date);
    if (Number.isNaN(cx)) continue;
    const d = Math.abs(cx - svgX);
    if (d < bestDist) { bestDist = d; bestIdx = i; }
  }
  setHover({ idx: bestIdx });
}
```

Update `handleMouseLeave` to also clear drag:

```ts
function handleMouseLeave() {
  setHover(null);
  setIsDragging(false);
}
```

- [ ] **Step 3: Wire SVG props + cursor className**

In the `<svg>` element, replace the `className="cursor-crosshair"` and `onMouseMove`/`onMouseLeave` lines with:

```tsx
className={isDragging ? "cursor-grabbing" : "cursor-crosshair"}
onMouseDown={handleMouseDown}
onMouseMove={handleMouseMove}
onMouseUp={handleMouseUp}
onMouseLeave={handleMouseLeave}
onWheel={handleWheel}
```

- [ ] **Step 4: Verify typecheck + visual**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

Open browser. Click+drag on MXF chart:
- Cursor changes to `grabbing`
- Drag left → view shifts right (newer candles)
- Drag right → view shifts left (older candles)
- Drag stops at edges (no scrolling past first / last candle)
- Crosshair hidden during drag
- Mouse up → crosshair restored on next hover

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/MXFIntradayChart.tsx
git commit -m "$(cat <<'EOF'
feat(mxf): drag-to-pan with grabbing cursor + crosshair hidden mid-drag

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: WS anchored-right shift

**Files:**
- Modify: `frontend/src/components/MXFIntradayChart.tsx`

- [ ] **Step 1: Add prevLenRef + effect**

Inside component, near the other refs, add:

```ts
const prevLenRef = useRef(0);
```

Add a new useEffect (after the init/reset effect):

```ts
// WS push 新 candle: 貼右就跟、否則凍結
useEffect(() => {
  const prevLen = prevLenRef.current;
  prevLenRef.current = candles.length;
  if (!viewRange) return;
  if (candles.length <= prevLen) return;  // not a push (or reset)
  const anchoredRight = viewRange.endIdx === prevLen - 1;
  if (!anchoredRight) return;
  const shift = candles.length - prevLen;
  setViewRange({ startIdx: viewRange.startIdx + shift, endIdx: viewRange.endIdx + shift });
  // viewRange not in deps to avoid loop — we only react to length growth
  // eslint-disable-next-line react-hooks/exhaustive-deps
}, [candles.length]);
```

- [ ] **Step 2: Verify typecheck + visual**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

Open browser. Watch chart during live trading hours (or wait for next WS push):
- If you haven't panned: new candle slides in from the right, view stays anchored to latest
- If you panned to history: new candles arrive but view does NOT auto-shift

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/MXFIntradayChart.tsx
git commit -m "$(cat <<'EOF'
feat(mxf): WS push auto-shifts viewRange only when anchored-right

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 4 — Time axis

### Task 10: `pickInterval` helper (TDD)

**Files:**
- Modify: `frontend/src/lib/mxf-chart.ts`
- Modify: `frontend/src/lib/mxf-chart.test.ts`

- [ ] **Step 1: Write the failing tests**

Append to `frontend/src/lib/mxf-chart.test.ts`:

```ts
import { pickInterval } from "./mxf-chart";

describe("pickInterval", () => {
  it("picks 5m for short spans (<= 35min)", () => {
    expect(pickInterval(30)).toBe(5);
  });
  it("picks 15m for spans 35-105min", () => {
    expect(pickInterval(60)).toBe(15);
    expect(pickInterval(100)).toBe(15);
  });
  it("picks 30m for spans 105-210min", () => {
    expect(pickInterval(180)).toBe(30);
  });
  it("picks 60m for spans 210-420min", () => {
    expect(pickInterval(300)).toBe(60);
  });
  it("picks 120m for spans 420-840min", () => {
    expect(pickInterval(600)).toBe(120);
  });
  it("picks 240m for spans > 840min", () => {
    expect(pickInterval(2000)).toBe(240);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- src/lib/mxf-chart.test.ts`
Expected: 6 new tests FAIL — `pickInterval` not exported.

- [ ] **Step 3: Implement to pass**

Append to `frontend/src/lib/mxf-chart.ts`:

```ts
const INTERVALS_MIN = [5, 15, 30, 60, 120, 240];

/**
 * 依「視窗內所有 session 時長加總」自動選 label interval (分鐘)。
 * Target: 不超過 ~7 個 label / 視窗。
 */
export function pickInterval(visibleMinutesSum: number, targetLabelCount = 7): number {
  for (const iv of INTERVALS_MIN) {
    if (visibleMinutesSum / iv <= targetLabelCount) return iv;
  }
  return 240;
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npm test -- src/lib/mxf-chart.test.ts`
Expected: all 15 tests pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/mxf-chart.ts frontend/src/lib/mxf-chart.test.ts
git commit -m "$(cat <<'EOF'
feat(mxf): add pickInterval helper for adaptive time-axis labels

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: Time axis labels (adaptive interval + collision avoidance)

**Files:**
- Modify: `frontend/src/components/MXFIntradayChart.tsx`

- [ ] **Step 1: Import pickInterval**

Update the import line:

```ts
import { dayOpenBaseline, computeNewViewRange, pickInterval, type ViewRange } from "../lib/mxf-chart";
```

- [ ] **Step 2: Compute label points + interval inside the existing view-dependent useMemo**

Locate the existing `useMemo` that returns `visibleCandles` etc. Add label computation to its body and return:

```ts
// Inside the same useMemo, after `maxVolumeInView` is computed:

// 1. visibleMinutesSum = sum of each visible session's duration in minutes
const MIN_MS = 60 * 1000;
const visibleMinutesSum = visibleSessions.reduce((acc, s) => {
  return acc + (new Date(s.endIso).getTime() - new Date(s.startIso).getTime()) / MIN_MS;
}, 0);
const interval = pickInterval(visibleMinutesSum);

// 2. For each visible session, generate HH:MM points at interval marks
//    A point at HH:MM means: (hour * 60 + min) % interval === 0
interface TimeLabel { iso: string; label: string; }
const labelPoints: TimeLabel[] = [];
for (const s of visibleSessions) {
  const sStart = new Date(s.startIso);
  const sEnd = new Date(s.endIso);
  // round sStart up to next interval boundary
  const startMin = sStart.getHours() * 60 + sStart.getMinutes();
  const remainder = startMin % interval;
  const firstAddMin = remainder === 0 ? 0 : interval - remainder;
  const cursor = new Date(sStart.getTime() + firstAddMin * MIN_MS);
  while (cursor <= sEnd) {
    const hh = String(cursor.getHours()).padStart(2, "0");
    const mm = String(cursor.getMinutes()).padStart(2, "0");
    labelPoints.push({ iso: cursor.toISOString(), label: `${hh}:${mm}` });
    cursor.setTime(cursor.getTime() + interval * MIN_MS);
  }
}

return {
  // existing values...
  visibleCandles, visibleSessions, visibleMa5, visibleMa20,
  yMinView, yMaxView, todayHighInView, todayLowInView, maxVolumeInView,
  // new:
  labelPoints,
};
```

Don't forget to add `labelPoints` to the destructured names where the memo result is consumed.

- [ ] **Step 3: Render time axis labels with collision filter**

In the JSX SVG block, before the closing `</svg>` (and before the hover crosshair so it draws on top), add:

```tsx
{/* Time axis labels — adaptive interval, drop overlaps < 40px */}
{(() => {
  if (labelPoints.length === 0) return null;
  const rendered: { x: number; label: string }[] = [];
  for (const lp of labelPoints) {
    const x = sx(lp.iso);
    if (Number.isNaN(x)) continue;
    if (rendered.length > 0 && Math.abs(x - rendered[rendered.length - 1].x) < 40) continue;
    rendered.push({ x, label: lp.label });
  }
  return rendered.map((r, i) => (
    <text key={i} x={r.x} y={CHART_H - PAD_B + 14} textAnchor="middle"
      className="fill-ink-dim text-[11px] tabular-nums">
      {r.label}
    </text>
  ));
})()}
```

Note: `scaleX_compressed` will return NaN if the iso is in a session gap — `Number.isNaN(x)` filters those.

- [ ] **Step 4: Verify typecheck + visual**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

Open browser. Verify:
- At default zoom (1m, ~148 candles ≈ 2.5h), labels every 30min (e.g., 09:00, 09:30, 10:00, ...)
- Zoom in tight (e.g., 30 candles), labels every 5min or 15min
- Zoom out wide (60m TF, all candles), labels every 60min or 120min
- Labels never overlap (40px minimum spacing)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/MXFIntradayChart.tsx
git commit -m "$(cat <<'EOF'
feat(mxf): adaptive HH:MM time-axis labels with collision avoidance

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 12: Session boundary markers

**Files:**
- Modify: `frontend/src/components/MXFIntradayChart.tsx`

- [ ] **Step 1: Add session marker rendering**

In the JSX SVG block, right after the existing `session gap 虛線` block (search for `{/* session gap 虛線 */}`), add:

```tsx
{/* Session boundary markers: 第一個 session + 每個 gap 之後的新 session */}
{visibleSessions.map((s, i) => {
  // 第 0 個 session 的 marker 畫在最左邊 candle 上方
  // 第 1+ 個畫在前一個 session 的 gap 之後 (即該 session 的 pxStart)
  const startX = i === 0
    ? sx(visibleCandles[0]?.date ?? s.startIso)
    : PAD_L + sessionBoundaries(visibleSessions, innerW)[i - 1].gapEndPx;
  if (Number.isNaN(startX)) return null;
  const d = new Date(s.startIso);
  const mm = String(d.getMonth() + 1).padStart(1, "0");  // M (no leading zero, 1-12)
  const dd = String(d.getDate()).padStart(1, "0");        // D
  const hour = d.getHours();
  const sessionType = (hour < 8 || hour >= 14) ? "夜盤" : "日盤";
  return (
    <text key={`sm-${i}`} x={startX + 4} y={PAD_T - 4} textAnchor="start"
      className="fill-ink-dim text-[10px]">
      {`${d.getMonth() + 1}/${d.getDate()} ${sessionType}`}
    </text>
  );
})}
```

Note: the inner `mm`/`dd` assigned-but-unused vars in the snippet above are redundant — drop them, the template literal does the formatting inline. Final clean version:

```tsx
{visibleSessions.map((s, i) => {
  const startX = i === 0
    ? sx(visibleCandles[0]?.date ?? s.startIso)
    : PAD_L + sessionBoundaries(visibleSessions, innerW)[i - 1].gapEndPx;
  if (Number.isNaN(startX)) return null;
  const d = new Date(s.startIso);
  const hour = d.getHours();
  const sessionType = (hour < 8 || hour >= 14) ? "夜盤" : "日盤";
  return (
    <text key={`sm-${i}`} x={startX + 4} y={PAD_T - 4} textAnchor="start"
      className="fill-ink-dim text-[10px]">
      {`${d.getMonth() + 1}/${d.getDate()} ${sessionType}`}
    </text>
  );
})}
```

- [ ] **Step 2: Verify typecheck + visual**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

Open browser. Verify:
- Visible day-session view: small label `M/D 日盤` upper-left
- Visible night-session view: `M/D 夜盤` (with the date being the session's start date, may not match midnight-crossed dates)
- View spanning both sessions: two labels at appropriate positions

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/MXFIntradayChart.tsx
git commit -m "$(cat <<'EOF'
feat(mxf): session boundary markers (M/D 日盤/夜盤) above chart

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 5 — Final verification

### Task 13: End-to-end visual checklist

**Files:** none (manual)

- [ ] **Step 1: Run all tests**

Run: `cd frontend && npm test`
Expected: all tests pass (15+ mxf-chart tests + any prior chart-svg tests).

- [ ] **Step 2: Typecheck full project**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Manual checklist in browser**

Open `http://localhost:5173`, MXF page. Verify each:

- [ ] Header: 22px symbol + 44px italic price visible top-left
- [ ] Change/pct shown in 紅 (up) / 綠 (down) / dim (flat or no baseline)
- [ ] Default 1m TF shows ~148 most-recent candles (not all 1500+)
- [ ] Scroll wheel up → zoom in (candles widen)
- [ ] Scroll wheel down → zoom out (candles narrow, stop at 6px min)
- [ ] Mouse-position anchor: candle under cursor stays at same x while zooming
- [ ] Click+drag pans the view; cursor = `grabbing` during drag
- [ ] Drag clamps at edges (no scrolling past first/last)
- [ ] Crosshair tracks closest visible candle correctly
- [ ] Crosshair hidden during drag, restored on hover
- [ ] Time-axis labels appear below chart, adapt density to zoom
- [ ] Session markers (`M/D 日盤/夜盤`) above chart
- [ ] Toolbar (timeframe tabs + chip toggles) still works
- [ ] VWAP / MA / VOL / 高低 toggles still work
- [ ] Switching TF (1m → 5m) resets view to last ~148 candles
- [ ] WS push: new candle appears at right when anchored; ignored when panned

- [ ] **Step 4: If all green, no commit needed (verification only)**

If anything fails, return to the relevant task and fix.

---

## Out of scope (explicitly NOT in this plan)

Per spec section 11 — these stay for future PRs:

- Backend `prev_settle` endpoint for futures-standard change baseline
- Touch / pinch zoom for mobile
- Keyboard shortcuts (+/-/0)
- Mini-map range slider
- Paginated historical loading
