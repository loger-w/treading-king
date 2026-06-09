# 大盤指數(加權 + 櫃買)分時走勢 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 app 加入「大盤指數」頁(加權 IX0001 / 櫃買 IX0043,左右並排 ⇄ 重疊% 切換),並讓 Discord bot 支援 `p加權`/`p大盤`/`p櫃買` 回精簡指數分時圖。

**Architecture:** 新寫精簡的指數 SVG 渲染 lib(autofit Y、不碰 `average`、不依賴股票 tick),前端頁面與 bot 共用(沿用既有 `intraday-chart-svg` 被 bot 重用的 pattern)。後端不改 — `/api/candles/{IX0001|IX0043}/intraday` 已驗證可用。

**Tech Stack:** React + TypeScript + Vite(前端)、vitest(測試)、Node + discord.js + @resvg/resvg-js(bot)。

**Spec:** `docs/superpowers/specs/2026-06-09-index-intraday-design.md`

**測試慣例:** 前端無 hook 測試環境 → 邏輯抽純函式測;UI 渲染用 `renderToStaticMarkup` 結構斷言。bot 有 vitest。所有指令在 worktree 根目錄對應子目錄執行(`cd frontend` / `cd bot`)。

---

## 檔案結構

**前端新增**
- `frontend/src/lib/index-symbols.ts` — 指數常數 + helper(前端 + bot 共用)
- `frontend/src/lib/index-intraday-svg.tsx` — 單圖 geometry(autofit)+ `IndexIntradayStatic` 渲染 + `fmtIndex`
- `frontend/src/lib/index-overlay-svg.tsx` — 重疊% geometry + `IndexOverlayStatic` 渲染
- `frontend/src/components/IndexIntradayChart.tsx` — 單一指數圖元件(資料 + hover)
- `frontend/src/components/IndexOverlayChart.tsx` — 重疊%圖元件
- `frontend/src/pages/IndexBoard.tsx` — 頁面(版面切換)

**前端修改**
- `frontend/src/components/Sidebar.tsx` — 加 `'index_board'` 導航項
- `frontend/src/App.tsx` — 掛 `IndexBoard`

**bot 修改**
- `bot/src/symbol.ts` — `p` + 中文別名解析
- `bot/src/data.ts` — `getName` 指數走常數
- `bot/src/embed.ts` — 加 `buildIndexReply`(精簡)
- `bot/src/render.ts` — 加 `renderIndexChartPng`
- `bot/src/reply.ts` — 指數精簡路徑 + composeReply 分支

---

## Phase A — 共用 lib(前端,bot 也用)

### Task 1: 指數常數 `index-symbols.ts`

**Files:**
- Create: `frontend/src/lib/index-symbols.ts`
- Test: `frontend/src/lib/index-symbols.test.ts`

- [ ] **Step 1: 寫失敗測試**

```typescript
// frontend/src/lib/index-symbols.test.ts
import { describe, it, expect } from "vitest";
import { resolveIndexAlias, isIndexCode, indexName, indexMeta, INDEX_SYMBOLS } from "./index-symbols";

describe("index-symbols", () => {
  it("加權/大盤 → IX0001", () => {
    expect(resolveIndexAlias("加權")).toBe("IX0001");
    expect(resolveIndexAlias("大盤")).toBe("IX0001");
  });
  it("櫃買/上櫃 → IX0043", () => {
    expect(resolveIndexAlias("櫃買")).toBe("IX0043");
    expect(resolveIndexAlias("上櫃")).toBe("IX0043");
  });
  it("去前後空白", () => expect(resolveIndexAlias(" 加權 ")).toBe("IX0001"));
  it("未知 → null", () => {
    expect(resolveIndexAlias("台積電")).toBeNull();
    expect(resolveIndexAlias("2330")).toBeNull();
  });
  it("isIndexCode", () => {
    expect(isIndexCode("IX0001")).toBe(true);
    expect(isIndexCode("2330")).toBe(false);
  });
  it("indexName / indexMeta", () => {
    expect(indexName("IX0043")).toBe("櫃買指數");
    expect(indexName("2330")).toBeNull();
    expect(indexMeta("IX0001")?.color).toBe("#f0b429");
  });
  it("INDEX_SYMBOLS 含兩檔", () => expect(INDEX_SYMBOLS).toHaveLength(2));
});
```

- [ ] **Step 2: 跑測試確認失敗** — `cd frontend && npx vitest run src/lib/index-symbols.test.ts` → FAIL(模組不存在)

- [ ] **Step 3: 實作**

```typescript
// frontend/src/lib/index-symbols.ts
// 台股大盤指數常數 — 前端頁面與 Discord bot 共用。
// 用價格指數(IX),非報酬指數(IR)。代碼經 backend /api/candles 實測。
export interface IndexSymbol {
  code: string;       // 富邦行情代碼
  name: string;       // 顯示全名
  short: string;      // 短名(圖例)
  color: string;      // 重疊圖識別色 hex
  aliases: string[];  // bot p 指令中文別名
}

export const INDEX_SYMBOLS: IndexSymbol[] = [
  { code: "IX0001", name: "加權指數", short: "加權", color: "#f0b429", aliases: ["加權", "大盤"] },
  { code: "IX0043", name: "櫃買指數", short: "櫃買", color: "#3b82f6", aliases: ["櫃買", "上櫃"] },
];

const BY_CODE = new Map(INDEX_SYMBOLS.map((s) => [s.code, s]));
const BY_ALIAS = new Map(
  INDEX_SYMBOLS.flatMap((s) => s.aliases.map((a) => [a, s.code] as const)),
);

export function isIndexCode(code: string): boolean {
  return BY_CODE.has(code);
}
export function indexName(code: string): string | null {
  return BY_CODE.get(code)?.name ?? null;
}
export function indexMeta(code: string): IndexSymbol | null {
  return BY_CODE.get(code) ?? null;
}
/** bot p 指令:中文別名 → 代碼。未知回 null。 */
export function resolveIndexAlias(input: string): string | null {
  return BY_ALIAS.get(input.trim()) ?? null;
}
```

- [ ] **Step 4: 跑測試確認通過** — `npx vitest run src/lib/index-symbols.test.ts` → PASS
- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/index-symbols.ts frontend/src/lib/index-symbols.test.ts
git commit -m "feat(index): 加權/櫃買指數常數 + 別名解析"
```

---

### Task 2: 單圖 geometry `computeIndexGeometry`(autofit)

**Files:**
- Create: `frontend/src/lib/index-intraday-svg.tsx`(本 task 只放 geometry + `fmtIndex`,渲染在 Task 3 補)
- Test: `frontend/src/lib/index-intraday-svg.test.ts`

- [ ] **Step 1: 寫失敗測試**

```typescript
// frontend/src/lib/index-intraday-svg.test.ts
import { describe, it, expect } from "vitest";
import { computeIndexGeometry, fmtIndex } from "./index-intraday-svg";
import type { IntradayCandle } from "./api";

function c(min: number, close: number, high = close, low = close): IntradayCandle {
  const hh = String(Math.floor(min / 60)).padStart(2, "0");
  const mm = String(min % 60).padStart(2, "0");
  return { date: `2026-06-09T${hh}:${mm}:00.000+08:00`, open: close, high, low, close, volume: 0, average: close };
}

describe("computeIndexGeometry", () => {
  it("autofit:波動小不被撐到 ±10%", () => {
    const candles = [c(540, 45000, 45010, 44990), c(600, 45135, 45140, 45120)];
    const g = computeIndexGeometry({ candles, prevClose: 45000 });
    expect(g.yMax - g.yMin).toBeLessThan(45000 * 0.02); // 遠小於 ±10%
    expect(g.yMax).toBeGreaterThanOrEqual(45140);
    expect(g.yMin).toBeLessThanOrEqual(44990);
  });
  it("prevClose 一定在 Y 範圍內(基準線可見)", () => {
    const candles = [c(540, 45100, 45110, 45090), c(600, 45200, 45210, 45190)];
    const g = computeIndexGeometry({ candles, prevClose: 45000 });
    expect(g.yMin).toBeLessThanOrEqual(45000);
    expect(g.yMax).toBeGreaterThanOrEqual(45000);
  });
  it("空 candles → 安全 empty", () => {
    const g = computeIndexGeometry({ candles: [], prevClose: 45000 });
    expect(g.filteredCandles).toEqual([]);
    expect(g.polyClose).toBe("");
  });
  it("fmtIndex 千分位 2 位(不套股票 tick)", () => {
    expect(fmtIndex(45231.5)).toBe("45,231.50");
    expect(fmtIndex(428.3)).toBe("428.30");
  });
});
```

- [ ] **Step 2: 跑測試確認失敗** — `npx vitest run src/lib/index-intraday-svg.test.ts` → FAIL

- [ ] **Step 3: 實作 geometry + fmtIndex**

```tsx
// frontend/src/lib/index-intraday-svg.tsx
// 指數分時圖共用畫圖層 — 網頁與 bot 共用。精簡版:autofit Y、不碰 average、
// 不套股票 tick(指數非個股 tick ladder)。顏色 inline hex(resvg 不解析 var/Tailwind)。
import { createElement, Fragment } from "react";
import type { IntradayCandle } from "./api";
import {
  CHART_W, CHART_H, PAD_L, PAD_R, PAD_T, PAD_B, INTRADAY_THEME, type ChartTheme,
} from "./intraday-chart-svg";
import { MARKET_OPEN_MIN, MARKET_CLOSE_MIN, TRADING_MINUTES, minuteOfDay } from "./intraday-time";

const Y_BUFFER = 0.0015; // autofit 上下各留 0.15%

/** 指數價格格式化:千分位 + 2 位小數,不依賴 ICU、不套股票 tick。 */
export function fmtIndex(v: number): string {
  return v.toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

export interface IndexChartInput {
  candles: IntradayCandle[];
  prevClose: number | null;
  theme?: ChartTheme;
  scale?: number; // 內容放大倍率:網頁 1、bot 1.6
}

export interface IndexGeometry {
  yMin: number; yMax: number;
  scaleX: (m: number) => number;
  scaleY: (v: number) => number;
  padL: number; padR: number; padT: number; padB: number;
  fontScale: number;
  polyClose: string;
  minutesByIdx: number[];
  filteredCandles: IntradayCandle[];
  todayHigh: number; todayHighIdx: number;
  todayLow: number; todayLowIdx: number;
}

export function computeIndexGeometry(input: IndexChartInput): IndexGeometry {
  const scale = input.scale ?? 1;
  const padL = Math.round(PAD_L * scale);
  const padR = Math.round(PAD_R * scale);
  const padT = Math.round(PAD_T * scale);
  const padB = Math.round(PAD_B * scale);

  const filteredCandles = input.candles.filter((cd) => {
    const m = minuteOfDay(cd.date);
    return m >= MARKET_OPEN_MIN && m <= MARKET_CLOSE_MIN;
  });

  if (filteredCandles.length === 0) {
    return {
      yMin: 0, yMax: 0, scaleX: () => 0, scaleY: () => 0,
      padL, padR, padT, padB, fontScale: scale, polyClose: "",
      minutesByIdx: [], filteredCandles: [],
      todayHigh: 0, todayHighIdx: -1, todayLow: 0, todayLowIdx: -1,
    };
  }

  const highs = filteredCandles.map((cd) => cd.high);
  const lows = filteredCandles.map((cd) => cd.low);
  const ref = input.prevClose;
  const rawMax = Math.max(...highs, ...(ref != null ? [ref] : []));
  const rawMin = Math.min(...lows, ...(ref != null ? [ref] : []));
  const yMax = rawMax * (1 + Y_BUFFER);
  const yMin = rawMin * (1 - Y_BUFFER);

  const xRange = CHART_W - padL - padR;
  const yRange = CHART_H - padT - padB;
  const minutesByIdx = filteredCandles.map((cd) => minuteOfDay(cd.date));
  const scaleX = (m: number) => padL + ((m - MARKET_OPEN_MIN) / TRADING_MINUTES) * xRange;
  const scaleY = (v: number) => padT + (1 - (v - yMin) / (yMax - yMin || 1)) * yRange;
  const polyClose = filteredCandles
    .map((cd, i) => `${scaleX(minutesByIdx[i])},${scaleY(cd.close)}`).join(" ");

  let todayHigh = filteredCandles[0].high, todayHighIdx = 0;
  let todayLow = filteredCandles[0].low, todayLowIdx = 0;
  for (let i = 1; i < filteredCandles.length; i++) {
    if (filteredCandles[i].high > todayHigh) { todayHigh = filteredCandles[i].high; todayHighIdx = i; }
    if (filteredCandles[i].low < todayLow) { todayLow = filteredCandles[i].low; todayLowIdx = i; }
  }

  return {
    yMin, yMax, scaleX, scaleY, padL, padR, padT, padB, fontScale: scale,
    polyClose, minutesByIdx, filteredCandles, todayHigh, todayHighIdx, todayLow, todayLowIdx,
  };
}

// 重新匯出畫布常數,讓元件/bot 不必同時 import 兩個 svg 檔
export { CHART_W, CHART_H, INTRADAY_THEME, type ChartTheme };
```

- [ ] **Step 4: 跑測試確認通過** — `npx vitest run src/lib/index-intraday-svg.test.ts` → PASS
- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/index-intraday-svg.tsx frontend/src/lib/index-intraday-svg.test.ts
git commit -m "feat(index): 指數單圖 geometry(autofit Y、不碰 average)"
```

---

### Task 3: 單圖渲染 `IndexIntradayStatic`

**Files:**
- Modify: `frontend/src/lib/index-intraday-svg.tsx`(加渲染元件)
- Test: `frontend/src/lib/index-intraday-svg.test.ts`(加渲染斷言)

- [ ] **Step 1: 加失敗測試**

```typescript
// 追加到 index-intraday-svg.test.ts
import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";
import { IndexIntradayStatic, computeIndexGeometry as cg } from "./index-intraday-svg";

it("IndexIntradayStatic 渲染主價線 + 昨收基準線,且不含量/VWAP", () => {
  const candles = [c(540, 45000, 45010, 44990), c(600, 45200, 45210, 45190)];
  const input = { candles, prevClose: 45000 };
  const geometry = cg(input);
  const svg = renderToStaticMarkup(createElement(IndexIntradayStatic, { ...input, geometry }));
  expect(svg).toContain("polyline");        // 主價線
  expect(svg).toContain("昨收");            // 基準線標籤
  expect(svg).not.toContain("Vol");         // 無量子圖
});
it("空 candles 渲染回 null(不炸)", () => {
  const input = { candles: [] as IntradayCandle[], prevClose: 45000 };
  const svg = renderToStaticMarkup(createElement(IndexIntradayStatic, { ...input, geometry: cg(input) }));
  expect(svg).toBe("");
});
```

- [ ] **Step 2: 跑測試確認失敗** — FAIL(`IndexIntradayStatic` 未匯出)

- [ ] **Step 3: 實作渲染元件**(追加到 `index-intraday-svg.tsx`)

```tsx
function priceColor(price: number, baseline: number, t: ChartTheme): string {
  if (price > baseline) return t.bull;
  if (price < baseline) return t.bear;
  return t.ink;
}

export interface IndexIntradayStaticProps extends IndexChartInput {
  geometry: IndexGeometry;
}

// presentational — 靜態圖層,不含 hover/外層 <svg>。網頁與 bot resvg 共用。
export function IndexIntradayStatic(props: IndexIntradayStaticProps) {
  const t = props.theme ?? INTRADAY_THEME;
  const {
    scaleX, scaleY, polyClose, minutesByIdx, filteredCandles,
    todayHigh, todayHighIdx, todayLow, todayLowIdx, padL, padR, padT, padB, fontScale,
  } = props.geometry;
  const fs = (base: number) => Math.round(base * fontScale);
  const sw = (base: number) => base * fontScale;
  const baseline = props.prevClose ?? (filteredCandles[0]?.open ?? 0);
  if (filteredCandles.length === 0) return null;

  const baselineY = scaleY(baseline);
  const lastIdx = filteredCandles.length - 1;
  const fillPoints = [
    `${scaleX(minutesByIdx[0])},${baselineY}`,
    ...filteredCandles.map((cd, i) => `${scaleX(minutesByIdx[i])},${scaleY(cd.close)}`),
    `${scaleX(minutesByIdx[lastIdx])},${baselineY}`,
  ].join(" ");

  return createElement(Fragment, null,
    // 1. 紅綠填色(走勢↔昨收),clipPath 切上下
    baseline > 0 && createElement(Fragment, null,
      createElement("defs", null,
        createElement("clipPath", { id: "idx-above" },
          createElement("rect", { x: padL, y: padT, width: CHART_W - padL - padR, height: Math.max(0, baselineY - padT) })),
        createElement("clipPath", { id: "idx-below" },
          createElement("rect", { x: padL, y: baselineY, width: CHART_W - padL - padR, height: Math.max(0, CHART_H - padB - baselineY) })),
      ),
      createElement("polygon", { points: fillPoints, fill: t.bull, fillOpacity: "0.15", clipPath: "url(#idx-above)" }),
      createElement("polygon", { points: fillPoints, fill: t.bear, fillOpacity: "0.15", clipPath: "url(#idx-below)" }),
    ),
    // 2. 昨收基準線 + 標籤
    baseline > 0 && createElement("g", null,
      createElement("line", { x1: padL, y1: baselineY, x2: CHART_W - padR, y2: baselineY, stroke: t.inkDim, strokeWidth: sw(0.6), strokeDasharray: "4 3", opacity: "0.7" }),
      createElement("text", { x: padL - 4, y: baselineY + 3, textAnchor: "end", fill: t.inkDim, fontSize: fs(13), fontFamily: t.fontFamily }, "昨收"),
    ),
    // 3. 主價線(紅綠 clip)
    polyClose && baseline > 0 && createElement(Fragment, null,
      createElement("polyline", { points: polyClose, fill: "none", stroke: t.bull, strokeWidth: sw(1.2), clipPath: "url(#idx-above)" }),
      createElement("polyline", { points: polyClose, fill: "none", stroke: t.bear, strokeWidth: sw(1.2), clipPath: "url(#idx-below)" }),
    ),
    polyClose && !(baseline > 0) && createElement("polyline", { points: polyClose, fill: "none", stroke: t.ink, strokeWidth: sw(1.2) }),
    // 4. 今日高低 marker
    todayHighIdx >= 0 && createElement("g", null,
      createElement("circle", { cx: scaleX(minutesByIdx[todayHighIdx]), cy: scaleY(todayHigh), r: sw(2.5), fill: priceColor(todayHigh, baseline, t) }),
      createElement("text", { x: scaleX(minutesByIdx[todayHighIdx]), y: scaleY(todayHigh) - 6, textAnchor: "middle", fill: priceColor(todayHigh, baseline, t), fontSize: fs(14), fontFamily: t.fontFamily }, fmtIndex(todayHigh)),
    ),
    todayLowIdx >= 0 && createElement("g", null,
      createElement("circle", { cx: scaleX(minutesByIdx[todayLowIdx]), cy: scaleY(todayLow), r: sw(2.5), fill: priceColor(todayLow, baseline, t) }),
      createElement("text", { x: scaleX(minutesByIdx[todayLowIdx]), y: scaleY(todayLow) + 13, textAnchor: "middle", fill: priceColor(todayLow, baseline, t), fontSize: fs(14), fontFamily: t.fontFamily }, fmtIndex(todayLow)),
    ),
    // 5. X 軸時間 label(固定 6 點)
    ...[
      { min: 540, label: "9:00" }, { min: 600, label: "10:00" }, { min: 660, label: "11:00" },
      { min: 720, label: "12:00" }, { min: 780, label: "13:00" }, { min: 810, label: "13:30" },
    ].map(({ min, label }) => createElement("text", {
      key: min, x: scaleX(min), y: CHART_H - 8, textAnchor: "middle",
      fill: t.inkDim, fontSize: fs(14), fontFamily: t.fontFamily,
    }, label)),
  );
}
```

- [ ] **Step 4: 跑測試確認通過** — PASS
- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/index-intraday-svg.tsx frontend/src/lib/index-intraday-svg.test.ts
git commit -m "feat(index): 指數單圖渲染(紅綠填色+昨收線+高低+時間軸)"
```

---

### Task 4: 重疊% lib `index-overlay-svg.tsx`

**Files:**
- Create: `frontend/src/lib/index-overlay-svg.tsx`
- Test: `frontend/src/lib/index-overlay-svg.test.ts`

- [ ] **Step 1: 寫失敗測試**

```typescript
// frontend/src/lib/index-overlay-svg.test.ts
import { describe, it, expect } from "vitest";
import { computeOverlayGeometry, type OverlaySeries } from "./index-overlay-svg";
import type { IntradayCandle } from "./api";

function c(min: number, close: number): IntradayCandle {
  const hh = String(Math.floor(min / 60)).padStart(2, "0");
  const mm = String(min % 60).padStart(2, "0");
  return { date: `2026-06-09T${hh}:${mm}:00.000+08:00`, open: close, high: close, low: close, close, volume: 0, average: close };
}
const A: OverlaySeries = { code: "IX0001", short: "加權", color: "#f0b429", candles: [c(540, 45000), c(600, 45450)], prevClose: 45000 };
const B: OverlaySeries = { code: "IX0043", short: "櫃買", color: "#3b82f6", candles: [c(540, 430), c(600, 428)], prevClose: 430 };

describe("computeOverlayGeometry", () => {
  it("各算漲跌%(加權 +1%、櫃買約 -0.47%)", () => {
    const g = computeOverlayGeometry(A, B);
    expect(g.lines[0].lastPct).toBeCloseTo(1.0, 5);
    expect(g.lines[1].lastPct).toBeCloseTo(-0.4651, 3);
  });
  it("Y 範圍涵蓋 0% 與兩線極值", () => {
    const g = computeOverlayGeometry(A, B);
    expect(g.yMin).toBeLessThanOrEqual(-0.4651);
    expect(g.yMax).toBeGreaterThanOrEqual(1.0);
  });
  it("缺 prevClose 的 series → 該線 lastPct null、另一線仍算", () => {
    const g = computeOverlayGeometry(A, { ...B, prevClose: null });
    expect(g.lines[0].lastPct).toBeCloseTo(1.0, 5);
    expect(g.lines[1].lastPct).toBeNull();
    expect(g.lines[1].poly).toBe("");
  });
});
```

- [ ] **Step 2: 跑測試確認失敗** — FAIL

- [ ] **Step 3: 實作**

```tsx
// frontend/src/lib/index-overlay-svg.tsx
// 重疊%圖共用畫圖層 — 兩指數各自從昨收 0% 起算,共用 % 軸。
import { createElement, Fragment } from "react";
import type { IntradayCandle } from "./api";
import {
  CHART_W, CHART_H, PAD_L, PAD_R, PAD_T, PAD_B, INTRADAY_THEME, type ChartTheme,
} from "./intraday-chart-svg";
import { MARKET_OPEN_MIN, MARKET_CLOSE_MIN, TRADING_MINUTES, minuteOfDay } from "./intraday-time";

export interface OverlaySeries {
  code: string; short: string; color: string;
  candles: IntradayCandle[]; prevClose: number | null;
}

export interface OverlayLine {
  code: string; short: string; color: string;
  poly: string;          // SVG points("" 表無資料)
  lastPct: number | null;
  lastY: number | null;  // 線尾 y(放 % 標籤)
}

export interface OverlayInput { scale?: number; theme?: ChartTheme; }

export interface OverlayGeometry {
  yMin: number; yMax: number;          // 單位:%
  scaleX: (m: number) => number;
  scaleY: (pct: number) => number;
  padL: number; padR: number; padT: number; padB: number;
  fontScale: number;
  lines: OverlayLine[];
  zeroY: number;
  pctByCodeAtMinute: (code: string, m: number) => number | null; // hover 用
}

const PCT_BUFFER = 0.1; // 上下各留 0.1 百分點

function seriesPct(s: OverlaySeries) {
  if (s.prevClose == null || s.prevClose === 0) return [] as Array<{ m: number; pct: number }>;
  return s.candles
    .filter((cd) => { const m = minuteOfDay(cd.date); return m >= MARKET_OPEN_MIN && m <= MARKET_CLOSE_MIN; })
    .map((cd) => ({ m: minuteOfDay(cd.date), pct: ((cd.close - s.prevClose!) / s.prevClose!) * 100 }));
}

export function computeOverlayGeometry(a: OverlaySeries, b: OverlaySeries, input: OverlayInput = {}): OverlayGeometry {
  const scale = input.scale ?? 1;
  const padL = Math.round(PAD_L * scale), padR = Math.round(PAD_R * scale);
  const padT = Math.round(PAD_T * scale), padB = Math.round(PAD_B * scale);

  const ptsA = seriesPct(a), ptsB = seriesPct(b);
  const allPct = [...ptsA, ...ptsB].map((p) => p.pct);
  const lo = Math.min(0, ...allPct) - PCT_BUFFER;
  const hi = Math.max(0, ...allPct) + PCT_BUFFER;
  const yMin = allPct.length ? lo : -1;
  const yMax = allPct.length ? hi : 1;

  const xRange = CHART_W - padL - padR;
  const yRange = CHART_H - padT - padB;
  const scaleX = (m: number) => padL + ((m - MARKET_OPEN_MIN) / TRADING_MINUTES) * xRange;
  const scaleY = (pct: number) => padT + (1 - (pct - yMin) / (yMax - yMin || 1)) * yRange;

  const mkLine = (s: OverlaySeries, pts: Array<{ m: number; pct: number }>): OverlayLine => {
    if (pts.length === 0) return { code: s.code, short: s.short, color: s.color, poly: "", lastPct: null, lastY: null };
    const poly = pts.map((p) => `${scaleX(p.m)},${scaleY(p.pct)}`).join(" ");
    const last = pts[pts.length - 1];
    return { code: s.code, short: s.short, color: s.color, poly, lastPct: last.pct, lastY: scaleY(last.pct) };
  };

  const lines = [mkLine(a, ptsA), mkLine(b, ptsB)];
  const byCode: Record<string, Array<{ m: number; pct: number }>> = { [a.code]: ptsA, [b.code]: ptsB };
  const pctByCodeAtMinute = (code: string, m: number): number | null => {
    const pts = byCode[code]; if (!pts || pts.length === 0) return null;
    let best = pts[0]; for (const p of pts) if (Math.abs(p.m - m) < Math.abs(best.m - m)) best = p;
    return best.pct;
  };

  return { yMin, yMax, scaleX, scaleY, padL, padR, padT, padB, fontScale: scale, lines, zeroY: scaleY(0), pctByCodeAtMinute };
}

function fmtPct(p: number): string { return `${p >= 0 ? "+" : ""}${p.toFixed(2)}%`; }

export interface IndexOverlayStaticProps extends OverlayInput { geometry: OverlayGeometry; }

export function IndexOverlayStatic(props: IndexOverlayStaticProps) {
  const t = props.theme ?? INTRADAY_THEME;
  const { scaleX, scaleY, yMin, yMax, padL, padR, zeroY, lines, fontScale } = props.geometry;
  const fs = (base: number) => Math.round(base * fontScale);
  const sw = (base: number) => base * fontScale;

  // Y 軸:0% + 上下各兩條等距格線
  const ticks = [yMin, yMin + (yMax - yMin) / 4, 0, yMax - (yMax - yMin) / 4, yMax]
    .filter((v, i, arr) => arr.indexOf(v) === i);

  return createElement(Fragment, null,
    // Y 軸格線 + %
    ...ticks.map((pct) => {
      const y = scaleY(pct); const isZero = Math.abs(pct) < 1e-9;
      return createElement("g", { key: pct },
        createElement("line", { x1: padL, y1: y, x2: CHART_W - padR, y2: y, stroke: isZero ? t.inkDim : t.line, strokeWidth: isZero ? sw(0.8) : sw(0.5), strokeDasharray: isZero ? "5 3" : undefined, opacity: isZero ? "0.8" : "0.5" }),
        createElement("text", { x: padL - 4, y: y + 3, textAnchor: "end", fill: isZero ? t.ink : t.inkDim, fontSize: fs(13), fontFamily: t.fontFamily }, fmtPct(pct)),
      );
    }),
    // X 軸時間
    ...[
      { min: 540, label: "9:00" }, { min: 600, label: "10:00" }, { min: 660, label: "11:00" },
      { min: 720, label: "12:00" }, { min: 780, label: "13:00" }, { min: 810, label: "13:30" },
    ].map(({ min, label }) => createElement("text", { key: min, x: scaleX(min), y: CHART_H - 8, textAnchor: "middle", fill: t.inkDim, fontSize: fs(14), fontFamily: t.fontFamily }, label)),
    // 兩條線 + 線尾 %
    ...lines.map((ln) => ln.poly && createElement(Fragment, { key: ln.code },
      createElement("polyline", { points: ln.poly, fill: "none", stroke: ln.color, strokeWidth: sw(1.6) }),
      ln.lastY != null && ln.lastPct != null && createElement("text", { x: CHART_W - padR + 4, y: ln.lastY + 3, textAnchor: "start", fill: ln.color, fontSize: fs(13), fontFamily: t.fontFamily }, fmtPct(ln.lastPct)),
    )),
  );
}
```

- [ ] **Step 4: 跑測試確認通過** — PASS
- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/index-overlay-svg.tsx frontend/src/lib/index-overlay-svg.test.ts
git commit -m "feat(index): 重疊%圖 lib(雙線共軸、0%基準)"
```

---

## Phase B — 前端元件 / 頁面

> 元件層無 hook 測試環境(專案慣例);驗證靠 `npx tsc --noEmit` + 後續 dev server 手動驗。

### Task 5: 單一指數圖元件 `IndexIntradayChart.tsx`

**Files:** Create `frontend/src/components/IndexIntradayChart.tsx`

- [ ] **Step 1: 實作元件**

```tsx
// frontend/src/components/IndexIntradayChart.tsx
import { useMemo, useState } from "react";
import { useIntradayCandles } from "../hooks/useIntradayCandles";
import { CHART_W, CHART_H } from "../lib/intraday-chart-svg";
import {
  IndexIntradayStatic, computeIndexGeometry, fmtIndex,
} from "../lib/index-intraday-svg";
import { MARKET_OPEN_MIN, TRADING_MINUTES } from "../lib/intraday-time";

export function IndexIntradayChart({ code, name }: { code: string; name: string }) {
  const { candles, prevClose } = useIntradayCandles(code);
  const geometry = useMemo(() => computeIndexGeometry({ candles, prevClose }), [candles, prevClose]);
  const { scaleX, scaleY, minutesByIdx, filteredCandles } = geometry;
  const [hover, setHover] = useState<{ idx: number } | null>(null);

  const latest = filteredCandles[filteredCandles.length - 1];
  const baseline = prevClose ?? (filteredCandles[0]?.open ?? 0);
  const change = latest && baseline ? latest.close - baseline : 0;
  const changePct = latest && baseline ? (change / baseline) * 100 : 0;
  const isUp = change > 0;
  const dirCls = isUp ? "text-bull" : change < 0 ? "text-bear" : "text-ink-muted";

  function handleMove(e: React.MouseEvent<SVGSVGElement>) {
    if (minutesByIdx.length === 0) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const svgX = ((e.clientX - rect.left) / rect.width) * CHART_W;
    const ratio = (svgX - geometry.padL) / (CHART_W - geometry.padL - geometry.padR);
    const mAtCursor = MARKET_OPEN_MIN + ratio * TRADING_MINUTES;
    const latestM = minutesByIdx[minutesByIdx.length - 1];
    if (mAtCursor > latestM || ratio < 0) { setHover(null); return; }
    let best = 0, bestDist = Math.abs(minutesByIdx[0] - mAtCursor);
    for (let i = 1; i < minutesByIdx.length; i++) {
      const d = Math.abs(minutesByIdx[i] - mAtCursor);
      if (d < bestDist) { bestDist = d; best = i; }
    }
    setHover({ idx: best });
  }

  return (
    <div>
      <div className="mb-3">
        <div className="text-sm text-ink-muted">
          {code} <span className="ml-1">{name}</span>
        </div>
        <div className={`font-semibold text-[34px] tabular-nums leading-none mt-1 ${dirCls}`}>
          {latest ? fmtIndex(latest.close) : "—"}
        </div>
        {latest && (
          <div className={`text-[17px] font-medium tabular-nums mt-1.5 ${dirCls}`}>
            {isUp ? "▲" : change < 0 ? "▾" : "—"} {Math.abs(change).toFixed(2)}　{changePct >= 0 ? "+" : ""}{changePct.toFixed(2)}%
          </div>
        )}
      </div>

      {filteredCandles.length === 0 ? (
        <div className="h-[300px] flex items-center justify-center text-ink-dim font-serif italic">無資料</div>
      ) : (
        <svg viewBox={`0 0 ${CHART_W} ${CHART_H}`} className="w-full h-auto cursor-crosshair"
          onMouseMove={handleMove} onMouseLeave={() => setHover(null)}>
          <IndexIntradayStatic candles={candles} prevClose={prevClose} geometry={geometry} />
          {hover && filteredCandles[hover.idx] && (() => {
            const cd = filteredCandles[hover.idx];
            const x = scaleX(minutesByIdx[hover.idx]), y = scaleY(cd.close);
            const m = minutesByIdx[hover.idx];
            const hh = String(Math.floor(m / 60)).padStart(2, "0");
            const mm = String(m % 60).padStart(2, "0");
            return (
              <g pointerEvents="none">
                <line x1={x} y1={geometry.padT} x2={x} y2={CHART_H - geometry.padB} stroke="#8a8273" strokeWidth="0.5" strokeDasharray="3 3" opacity="0.7" />
                <line x1={geometry.padL} y1={y} x2={CHART_W - geometry.padR} y2={y} stroke="#8a8273" strokeWidth="0.5" strokeDasharray="3 3" opacity="0.7" />
                <circle cx={x} cy={y} r="2.5" className="fill-ink" />
                <text x={geometry.padL + 2} y={y - 4} className="fill-ink text-[12px] tabular-nums">{fmtIndex(cd.close)}　{hh}:{mm}</text>
              </g>
            );
          })()}
        </svg>
      )}
    </div>
  );
}
```

- [ ] **Step 2: 型別檢查** — `cd frontend && npx tsc --noEmit` → 無錯
- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/IndexIntradayChart.tsx
git commit -m "feat(index): 單一指數分時圖元件(資料+hover)"
```

---

### Task 6: 重疊%圖元件 `IndexOverlayChart.tsx`

**Files:** Create `frontend/src/components/IndexOverlayChart.tsx`

- [ ] **Step 1: 實作元件**

```tsx
// frontend/src/components/IndexOverlayChart.tsx
import { useMemo, useState } from "react";
import { useIntradayCandles } from "../hooks/useIntradayCandles";
import { CHART_W, CHART_H } from "../lib/intraday-chart-svg";
import { computeOverlayGeometry, IndexOverlayStatic, type OverlaySeries } from "../lib/index-overlay-svg";
import { INDEX_SYMBOLS, indexMeta } from "../lib/index-symbols";
import { MARKET_OPEN_MIN, TRADING_MINUTES } from "../lib/intraday-time";

export function IndexOverlayChart() {
  const a = INDEX_SYMBOLS[0], b = INDEX_SYMBOLS[1];
  const ca = useIntradayCandles(a.code);
  const cb = useIntradayCandles(b.code);
  const [hover, setHover] = useState<number | null>(null); // minute of day

  const seriesA: OverlaySeries = { code: a.code, short: a.short, color: a.color, candles: ca.candles, prevClose: ca.prevClose };
  const seriesB: OverlaySeries = { code: b.code, short: b.short, color: b.color, candles: cb.candles, prevClose: cb.prevClose };
  const geometry = useMemo(() => computeOverlayGeometry(seriesA, seriesB),
    [ca.candles, ca.prevClose, cb.candles, cb.prevClose]); // eslint-disable-line react-hooks/exhaustive-deps

  const hasData = geometry.lines.some((l) => l.poly !== "");

  function handleMove(e: React.MouseEvent<SVGSVGElement>) {
    const rect = e.currentTarget.getBoundingClientRect();
    const svgX = ((e.clientX - rect.left) / rect.width) * CHART_W;
    const ratio = (svgX - geometry.padL) / (CHART_W - geometry.padL - geometry.padR);
    if (ratio < 0 || ratio > 1) { setHover(null); return; }
    setHover(MARKET_OPEN_MIN + ratio * TRADING_MINUTES);
  }

  return (
    <div>
      <div className="flex items-center gap-5 mb-3 text-sm">
        {INDEX_SYMBOLS.map((s) => {
          const meta = indexMeta(s.code)!;
          const line = geometry.lines.find((l) => l.code === s.code);
          const pct = hover != null ? geometry.pctByCodeAtMinute(s.code, hover) : line?.lastPct ?? null;
          return (
            <span key={s.code} style={{ color: meta.color }} className="tabular-nums">
              ● {s.short} {pct == null ? "—" : `${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%`}
            </span>
          );
        })}
      </div>
      {!hasData ? (
        <div className="h-[300px] flex items-center justify-center text-ink-dim font-serif italic">無資料</div>
      ) : (
        <svg viewBox={`0 0 ${CHART_W} ${CHART_H}`} className="w-full h-auto cursor-crosshair"
          onMouseMove={handleMove} onMouseLeave={() => setHover(null)}>
          <IndexOverlayStatic geometry={geometry} />
          {hover != null && (
            <line x1={geometry.scaleX(hover)} y1={geometry.padT} x2={geometry.scaleX(hover)} y2={CHART_H - geometry.padB}
              stroke="#8a8273" strokeWidth="0.5" strokeDasharray="3 3" opacity="0.7" pointerEvents="none" />
          )}
        </svg>
      )}
    </div>
  );
}
```

- [ ] **Step 2: 型別檢查** — `npx tsc --noEmit` → 無錯
- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/IndexOverlayChart.tsx
git commit -m "feat(index): 重疊%圖元件(雙指數+圖例+hover)"
```

---

### Task 7: 頁面 `IndexBoard.tsx`(版面切換)

**Files:** Create `frontend/src/pages/IndexBoard.tsx`

- [ ] **Step 1: 實作頁面**

```tsx
// frontend/src/pages/IndexBoard.tsx
import { useLocalToggle } from "../hooks/useLocalToggle";
import { IndexIntradayChart } from "../components/IndexIntradayChart";
import { IndexOverlayChart } from "../components/IndexOverlayChart";
import { INDEX_SYMBOLS } from "../lib/index-symbols";

export function IndexBoard() {
  const [overlay, setOverlay] = useLocalToggle("tk:index:overlay", false);

  return (
    <div className="h-full overflow-y-auto">
      <div className="flex flex-col gap-6 px-8 py-6 max-w-[1400px]">
        <header className="flex items-center justify-between">
          <div>
            <span className="label-tiny mb-1">Module · 大盤</span>
            <h1 className="h-display text-2xl text-ink">大盤指數</h1>
          </div>
          <div className="flex border border-line rounded-md overflow-hidden text-sm">
            <button type="button" onClick={() => setOverlay(false)}
              className={`px-4 py-1.5 transition-colors ${!overlay ? "bg-accent/[0.12] text-accent font-medium" : "text-ink-dim hover:text-ink"}`}>左右並排</button>
            <button type="button" onClick={() => setOverlay(true)}
              className={`px-4 py-1.5 transition-colors ${overlay ? "bg-accent/[0.12] text-accent font-medium" : "text-ink-dim hover:text-ink"}`}>重疊 %</button>
          </div>
        </header>

        {overlay ? (
          <section className="rounded-lg border border-line p-4">
            <div className="label mb-3">今日漲跌 % 對比</div>
            <IndexOverlayChart />
          </section>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {INDEX_SYMBOLS.map((s) => (
              <section key={s.code} className="rounded-lg border border-line p-4">
                <IndexIntradayChart code={s.code} name={s.name} />
              </section>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: 型別檢查** — `npx tsc --noEmit` → 無錯
- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/IndexBoard.tsx
git commit -m "feat(index): 大盤指數頁(並排⇄重疊% 切換)"
```

---

### Task 8: 導航接入 `Sidebar.tsx` + `App.tsx`

**Files:**
- Modify: `frontend/src/components/Sidebar.tsx`
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Sidebar 加導航項**

`Page` 型別(`Sidebar.tsx:3`)改為:
```typescript
export type Page = 'monitor' | 'mxf_backtest' | 'index_board';
```
`NAV_ITEMS` 陣列(`Sidebar.tsx:11`)在 `mxf_backtest` 後加一筆:
```typescript
  {
    id: 'index_board',
    label: '大盤指數',
    iconPath: 'M3 17l5-6 4 4 5-8 4 5 M3 17v4h18',
  },
```

- [ ] **Step 2: App 掛頁面**

`App.tsx:4` 後加 import:
```typescript
import { IndexBoard } from './pages/IndexBoard';
```
在 `mxf_backtest` 的 `<div hidden>` 區塊後(`App.tsx:22` 前)加:
```tsx
        <div hidden={page !== 'index_board'} className="h-full">
          <IndexBoard />
        </div>
```

- [ ] **Step 3: 型別檢查 + build** — `cd frontend && npx tsc --noEmit && npm run build` → 成功
- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/Sidebar.tsx frontend/src/App.tsx
git commit -m "feat(index): sidebar 加大盤指數入口 + 掛頁面"
```

---

## Phase C — Discord bot

### Task 9: 別名解析 `bot/src/symbol.ts`

**Files:**
- Modify: `bot/src/symbol.ts`
- Test: `bot/src/symbol.test.ts`(若不存在則建立)

- [ ] **Step 1: 加失敗測試**

```typescript
// bot/src/symbol.test.ts(追加或新建)
import { describe, it, expect } from "vitest";
import { parseSymbolCommand } from "./symbol";

describe("parseSymbolCommand 指數別名", () => {
  it("數字代號照舊", () => {
    expect(parseSymbolCommand("p2330")).toBe("2330");
    expect(parseSymbolCommand("P0050")).toBe("0050");
  });
  it("中文別名 → 指數代碼", () => {
    expect(parseSymbolCommand("p加權")).toBe("IX0001");
    expect(parseSymbolCommand("p大盤")).toBe("IX0001");
    expect(parseSymbolCommand("p櫃買")).toBe("IX0043");
    expect(parseSymbolCommand("p上櫃")).toBe("IX0043");
  });
  it("非指令 / 未知 → null", () => {
    expect(parseSymbolCommand("p亂碼")).toBeNull();
    expect(parseSymbolCommand("hello")).toBeNull();
    expect(parseSymbolCommand("加權")).toBeNull(); // 沒 p 前綴
  });
});
```

- [ ] **Step 2: 跑測試確認失敗** — `cd bot && npx vitest run src/symbol.test.ts` → FAIL

- [ ] **Step 3: 實作**(改寫 `bot/src/symbol.ts`)

```typescript
import { resolveIndexAlias } from "../../frontend/src/lib/index-symbols";

// 只在「整則訊息 = p + 合法台股代號 或 指數別名」時觸發,避免讀到雜訊洗頻。
const RE = /^[pP]([0-9]{4,6}[A-Z]{0,2})$/;
export function parseSymbolCommand(content: string): string | null {
  const t = content.trim();
  const m = t.match(RE);
  if (m) return m[1].toUpperCase();
  // p + 中文別名(加權/大盤/櫃買/上櫃)
  if (/^[pP]./.test(t)) {
    return resolveIndexAlias(t.slice(1));
  }
  return null;
}
```

- [ ] **Step 4: 跑測試確認通過** — PASS
- [ ] **Step 5: Commit**

```bash
git add bot/src/symbol.ts bot/src/symbol.test.ts
git commit -m "feat(bot): p 指令支援加權/大盤/櫃買 中文別名"
```

---

### Task 10: bot 資料 + 渲染 + 精簡 embed

**Files:**
- Modify: `bot/src/data.ts`
- Modify: `bot/src/embed.ts`
- Modify: `bot/src/render.ts`

- [ ] **Step 1: `data.ts` — getName 指數走常數**

`bot/src/data.ts` 頂部加 import:
```typescript
import { isIndexCode, indexName } from "../../frontend/src/lib/index-symbols";
```
改 `getName`(`data.ts:29`):
```typescript
export async function getName(s: string): Promise<string | null> {
  if (isIndexCode(s)) return indexName(s);
  try {
    const r = await get<{ results: SymbolRow[] }>(`/api/symbols?search=${encodeURIComponent(s)}&limit=20`);
    return r.results.find((row) => row.symbol === s)?.name ?? null;
  } catch { return null; }
}
```

- [ ] **Step 2: `embed.ts` — 加 `buildIndexReply`**(精簡,無 CDP/MA/VWAP/量/五檔)

追加到 `bot/src/embed.ts`:
```typescript
export function buildIndexReply(args: {
  symbol: string; name: string | null; lastClose: number; change: number; changePct: number;
  open: number; high: number; low: number; asOf: string;
}) {
  const up = args.change > 0;
  const color = up ? 0xe85a4f : args.change < 0 ? 0x7fc99a : 0x8a8273;
  const arrow = up ? "▲" : args.change < 0 ? "▾" : "—";
  const fmt = (v: number) => v.toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const embed = new EmbedBuilder()
    .setColor(color)
    .setTitle(`${args.name ?? ""} ${args.symbol}`.trim())
    .setDescription(
      `**${fmt(args.lastClose)}**　${arrow}${Math.abs(args.change).toFixed(2)} (${args.changePct >= 0 ? "+" : ""}${args.changePct.toFixed(2)}%)`,
    )
    .addFields({ name: "開 / 高 / 低", value: `${fmt(args.open)} / ${fmt(args.high)} / ${fmt(args.low)}`, inline: true })
    .setFooter({ text: `資料 ${args.asOf}` });
  return { embeds: [embed] };
}
```

- [ ] **Step 3: `render.ts` — 加 `renderIndexChartPng`**

`bot/src/render.ts` 頂部 import 加:
```typescript
import { IndexIntradayStatic, computeIndexGeometry, fmtIndex, type IndexChartInput } from "../../frontend/src/lib/index-intraday-svg";
```
追加(沿用既有 `THEME` / `FONT_FAMILY` / `TITLE_H` / `CHART_W` / `CHART_H` / `svgToPng`):
```typescript
export function buildIndexChartSvg(args: IndexChartInput & {
  symbol: string; name: string | null; lastClose: number; change: number; changePct: number;
}): string {
  const input: IndexChartInput = { ...args, theme: THEME, scale: 1.6 };
  const geometry = computeIndexGeometry(input);
  const dirColor = args.change > 0 ? THEME.bull : args.change < 0 ? THEME.bear : THEME.ink;
  const totalH = CHART_H + TITLE_H; // 指數無量子圖
  const arrow = args.change > 0 ? "▲" : args.change < 0 ? "▾" : "—";
  return renderToStaticMarkup(
    createElement("svg", { xmlns: "http://www.w3.org/2000/svg", viewBox: `0 0 ${CHART_W} ${totalH}`, width: CHART_W, height: totalH },
      createElement("rect", { x: 0, y: 0, width: CHART_W, height: totalH, fill: THEME.bg }),
      createElement("text", { x: 14, y: 40, fontSize: 33, fontFamily: FONT_FAMILY, fill: THEME.ink }, fitTitle(args.symbol, args.name)),
      createElement("text", { x: CHART_W - 14, y: 40, fontSize: 33, textAnchor: "end", fontFamily: FONT_FAMILY, fill: dirColor },
        `${fmtIndex(args.lastClose)}  ${arrow}${Math.abs(args.change).toFixed(2)} (${args.changePct >= 0 ? "+" : ""}${args.changePct.toFixed(2)}%)`),
      createElement("g", { transform: `translate(0, ${TITLE_H})` },
        createElement(IndexIntradayStatic, { ...input, geometry }),
      ),
    ),
  );
}

export function renderIndexChartPng(args: IndexChartInput & {
  symbol: string; name: string | null; lastClose: number; change: number; changePct: number;
}): Buffer {
  return svgToPng(buildIndexChartSvg(args));
}
```
> 注意:`render.ts` 現有 import 只取了 `CHART_W, CHART_H, TOTAL_H`;需確認 `CHART_H` 已 import(已有)。`fitTitle` / `svgToPng` / `THEME` / `FONT_FAMILY` / `TITLE_H` 均為本檔既有符號。

- [ ] **Step 4: 型別檢查** — `cd bot && npx tsc --noEmit` → 無錯
- [ ] **Step 5: Commit**

```bash
git add bot/src/data.ts bot/src/embed.ts bot/src/render.ts
git commit -m "feat(bot): 指數名稱常數 + 精簡 embed + 指數圖渲染"
```

---

### Task 11: bot 指數精簡回覆路徑 `reply.ts`

**Files:**
- Modify: `bot/src/reply.ts`
- Test: `bot/src/reply.test.ts`

- [ ] **Step 1: 加失敗測試**(指數走精簡路徑:回 2 則、不含五檔/CDP)

```typescript
// 追加到 bot/src/reply.test.ts
import { loadSlow, composeReply } from "./reply";
import type { ReplyDeps } from "./reply";

const indexDeps: ReplyDeps = {
  getCandles: async () => ({
    date: "2026-06-09", symbol: "IX0001",
    data: [
      { date: "2026-06-09T09:00:00.000+08:00", open: 45000, high: 45050, low: 44980, close: 45010, volume: 0, average: 0 },
      { date: "2026-06-09T13:30:00.000+08:00", open: 45200, high: 45260, low: 45100, close: 45231, volume: 0, average: 0 },
    ],
    prev_close: 45000,
  }),
  getCdp: async () => { throw new Error("不該被呼叫"); },
  getMa: async () => { throw new Error("不該被呼叫"); },
  getName: async () => "加權指數",
  render: () => { throw new Error("指數應走 renderIndex"); },
  renderIndex: () => Buffer.from("PNG"),
};

it("指數走精簡路徑:isIndex、有現價、有圖", async () => {
  const s = await loadSlow("IX0001", indexDeps);
  expect(s.empty).toBe(false);
  if (!s.empty) {
    expect(s.isIndex).toBe(true);
    expect(s.lastClose).toBe(45231);
    expect(s.png).not.toBeNull();
  }
});

it("composeReply 指數:2 則(文字+圖),不含五檔", async () => {
  const s = await loadSlow("IX0001", indexDeps);
  const msgs = composeReply("IX0001", s, null, null);
  expect(msgs).toHaveLength(2); // 文字 + 走勢圖,無五檔
});
```

- [ ] **Step 2: 跑測試確認失敗** — `cd bot && npx vitest run src/reply.test.ts` → FAIL

- [ ] **Step 3: 實作**(改 `bot/src/reply.ts`)

頂部 import 加:
```typescript
import { renderIndexChartPng } from "./render";
import { buildIndexReply } from "./embed";
import { isIndexCode } from "../../frontend/src/lib/index-symbols";
```
`ReplyDeps` interface 加一欄:
```typescript
  renderIndex: typeof renderIndexChartPng;
```
`realDeps` 加:
```typescript
  renderIndex: renderIndexChartPng,
```
`loadSlow` 開頭分流(在現有 body 第一行前):
```typescript
export async function loadSlow(symbol: string, deps: ReplyDeps = realDeps) {
  if (isIndexCode(symbol)) return loadSlowIndex(symbol, deps);
  // ...（原有個股邏輯不變）
}
```
新增 `loadSlowIndex`:
```typescript
async function loadSlowIndex(symbol: string, deps: ReplyDeps) {
  const [candlesR, name] = await Promise.all([deps.getCandles(symbol), deps.getName(symbol)]);
  const intraday = candlesR.data.filter((c) => {
    const m = minuteOfDay(c.date);
    return m >= MARKET_OPEN_MIN && m <= MARKET_CLOSE_MIN;
  });
  if (intraday.length === 0) {
    return { empty: true as const, isIndex: true as const, name, prevClose: candlesR.prev_close };
  }
  const last = intraday[intraday.length - 1];
  const baseline = candlesR.prev_close ?? intraday[0].open;
  const change = last.close - baseline;
  const changePct = baseline ? (change / baseline) * 100 : 0;
  const high = Math.max(...intraday.map((c) => c.high));
  const low = Math.min(...intraday.map((c) => c.low));
  const png = safeRender(() => deps.renderIndex({
    candles: candlesR.data, prevClose: candlesR.prev_close,
    symbol, name, lastClose: last.close, change, changePct,
  }));
  return {
    empty: false as const, isIndex: true as const, name, png,
    lastClose: last.close, change, changePct,
    open: intraday[0].open, high, low, asOf: last.date.slice(11, 16),
  };
}
```
`composeReply` 開頭加指數分支(在現有 `if (s.empty)` 之後):
```typescript
  if (!s.empty && "isIndex" in s && s.isIndex) {
    const msgs: BaseMessageOptions[] = [buildIndexReply({
      symbol, name: s.name, lastClose: s.lastClose, change: s.change, changePct: s.changePct,
      open: s.open, high: s.high, low: s.low, asOf: s.asOf,
    })];
    if (s.png) msgs.push(imageMessage(s.png, "chart.png"));
    return msgs;
  }
```
> 空盤前的指數:`s.empty===true` 時現有分支已回單則純文字(顯示 CDP/MA「—」)。可接受;若要更精簡可另判 `isIndex`,非必要。

- [ ] **Step 4: caller 不抓五檔** — 讀 `bot/src/index.ts`(訊息 handler),找呼叫 `getQuote` / `renderQuotePng` 處;對 `isIndexCode(symbol)` 的 symbol **跳過** quote 抓取(傳 `null, null` 給 `composeReply`)。範例:
```typescript
const quote = isIndexCode(symbol) ? null : await getQuote(symbol).catch(() => null);
const quotePng = quote ? safeRender(() => renderQuotePng(quote)) : null;
```
(`composeReply` 的指數分支已先 return,quote 參數不會被用到,但跳過抓取省一次 API。)

- [ ] **Step 5: 跑測試確認通過** — `npx vitest run src/reply.test.ts` → PASS
- [ ] **Step 6: Commit**

```bash
git add bot/src/reply.ts bot/src/reply.test.ts bot/src/index.ts
git commit -m "feat(bot): 指數精簡回覆(現價文字+走勢圖,跳過 CDP/五檔)"
```

---

## Phase D — 整合驗證

### Task 12: 全套驗證

- [ ] **Step 1: 前端測試全綠** — `cd frontend && npx vitest run` → 0 fail(含既有快照)
- [ ] **Step 2: 前端型別 + build** — `npx tsc --noEmit && npm run build` → 成功
- [ ] **Step 3: bot 測試 + 型別** — `cd bot && npx vitest run && npx tsc --noEmit` → 通過
- [ ] **Step 4: 手動驗證(前端)** — 在 worktree 跑 `cd frontend && npm run dev`(注意:不是主 repo 的 dev server),開頁面:
  - sidebar 出現「大盤指數」、點進去
  - 並排:加權/櫃買兩張圖、autofit 看得出起伏、紅綠正確、現價大字
  - 切「重疊 %」:兩線從 0% 出發、加權金/櫃買藍、圖例 %、hover 準星
  - 重新整理後版面選擇保留(localStorage)
- [ ] **Step 5: 手動驗證(bot,需 DISCORD_BOT_TOKEN)** — 盤中於頻道打 `p加權` / `p大盤` / `p櫃買`,確認回「現價文字 + 走勢圖」兩則、無五檔/CDP;`p2330` 仍正常。
- [ ] **Step 6: 收尾** — `git status` 確認乾淨;若驗證有改,補 commit。準備 merge 回 main / 開 PR(見 finishing-a-development-branch)。

---

## Self-Review

**Spec coverage:**
- §2.1 單一入口 → Task 8 ✓
- §2.2 版面切換(並排/重疊)→ Task 7 ✓
- §2.3 重疊配色金/藍 → Task 1(color)+ Task 4 ✓
- §2.4 標題大字現價 → Task 5 ✓
- §2.5 bot p加權/大盤/櫃買 → Task 9、11 ✓
- §3 共用 lib + 新寫不複用 → Task 2-4 ✓
- §5.1 單圖 autofit/昨收線/高低/時間軸 → Task 2-3 ✓
- §5.2 重疊% → Task 4 ✓
- §6 bot 精簡回覆 → Task 10-11 ✓
- §8 錯誤邊界(空 candles、缺值、產圖失敗 safeRender)→ Task 2/4/11 ✓
- §9 測試(純函式 + bot vitest)→ Task 1/2/4/9/11 ✓

**型別一致性:** `IndexChartInput`/`IndexGeometry`/`computeIndexGeometry`/`IndexIntradayStatic`/`fmtIndex`(Task 2-3)、`OverlaySeries`/`OverlayGeometry`/`computeOverlayGeometry`/`IndexOverlayStatic`(Task 4)、`resolveIndexAlias`/`isIndexCode`/`indexName`/`indexMeta`(Task 1)、bot `renderIndexChartPng`/`buildIndexReply`/`loadSlowIndex`/`ReplyDeps.renderIndex`(Task 10-11)— 前後引用一致。

**已知整合點(執行時對照既有 code):** Task 8(Sidebar `Page`/`NAV_ITEMS`、App hidden div)、Task 11 Step 4(`index.ts` message handler 跳過 quote)。
