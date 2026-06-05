# Discord 個股查詢 Bot 實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Discord 打 `p2330` → bot 回一張「與網頁同款」的分時走勢 PNG + 一則含現價/CDP/均線/委買賣五檔的 embed。

**Architecture:** 兩階段。**Phase 1** 把股票 `IntradayChart` 的「靜態畫圖」抽成共用、接 props 的 presentational 元件 `IntradayChartStatic`(+ 純函式 `computeIntradayGeometry`),顏色 inline 成 Editorial Dark hex,網頁照常用它(零外觀變化),用 snapshot test 鎖住輸出。**Phase 2** 新增一個 Node bot(discord.js),抓現有 Python REST endpoint 的資料,用 `react-dom/server` 把 `IntradayChartStatic` 渲成 SVG 字串、`@resvg/resvg-js` 轉 PNG,組 embed 回貼。富邦 / Python 後端零改動。

**Tech Stack:** 前端 React 18 + TypeScript + vitest(node env)。Bot:Node 18+、discord.js v14、@resvg/resvg-js、react-dom/server、tsx(跑 TS)、vitest。共用畫圖邏輯靠 `react-dom/server` renderToStaticMarkup 重用同一份 JSX。

**Spec:** `docs/superpowers/specs/2026-06-05-discord-stock-bot-design.md`

---

## 檔案結構

**Phase 1(前端,共用畫圖)**
- Create: `frontend/src/lib/intraday-chart-svg.tsx` — `INTRADAY_THEME`、尺寸常數、`computeIntradayGeometry()`(純)、`<IntradayChartStatic>`(presentational,所有靜態圖層,無 hover/無 toggle 按鈕)。
- Create: `frontend/src/lib/intraday-chart-svg.test.ts` — `computeIntradayGeometry` 單元測試 + `IntradayChartStatic` snapshot(防漂移)。
- Modify: `frontend/src/components/IntradayChart.tsx` — 改用 `computeIntradayGeometry` + 渲染 `<IntradayChartStatic>`;hover crosshair / toggle 按鈕 / 資料抓取 useEffect 留在本檔。

**Phase 2(bot)**
- Create: `bot/package.json`、`bot/tsconfig.json`、`bot/.gitignore`、`bot/.env.example`、`bot/vitest.config.ts`
- Create: `bot/assets/NotoSansTC-Regular.ttf`(內嵌 CJK 字型,執行者下載放入)
- Create: `bot/src/config.ts` — 讀+驗證 env
- Create: `bot/src/symbol.ts` — 解析 `p2330` → 代號
- Create: `bot/src/data.ts` — 打後端 endpoint 拿資料
- Create: `bot/src/cache.ts` — per-symbol 30s TTL 快取
- Create: `bot/src/render.ts` — SVG → PNG(resvg)
- Create: `bot/src/embed.ts` — 組 discord embed + 五檔階梯
- Create: `bot/src/index.ts` — discord client + messageCreate handler
- Create: `bot/src/{symbol,embed,cache}.test.ts`
- Modify: `start.ps1`、`install.ps1` — 加 bot 視窗 / 安裝

---

# Phase 1 — 抽出共用畫圖模組

## Task 1：建立 theme、尺寸常數、型別骨架

**Files:**
- Create: `frontend/src/lib/intraday-chart-svg.tsx`

- [ ] **Step 1：寫骨架(theme + 常數 + 型別)**

```tsx
// 股票分時圖的共用畫圖層 — 網頁(IntradayChart)與 Discord bot 共用同一份 JSX。
// 顏色一律 inline hex(取自 tailwind.config.js 的 Editorial Dark);
// 不可用 Tailwind class 或 var(--color-…) —— resvg(bot 端)不解析。
import { createElement, Fragment } from "react";
import type { IntradayCandle, CdpLevels, CamarillaLevels, MaLevels } from "./api";
import { formatTickPrice, roundToNearestTick } from "./tick";
import { resolveCollisions, type LabelInput } from "./chart-labels";
import {
  MARKET_OPEN_MIN, MARKET_CLOSE_MIN, TRADING_MINUTES, minuteOfDay,
} from "./intraday-time";

export const INTRADAY_THEME = {
  bg: "#14110c",
  bull: "#e85a4f",   // 台股漲紅
  bear: "#7fc99a",   // 台股跌綠
  ink: "#ede4d3",
  inkMuted: "#d4c8b0",
  inkDim: "#8a8273",
  line: "#2e2a22",
  accent: "#e85a4f", // CDP
  camarilla: "#3b82f6",
  ma5: "#f0b429",
  ma20: "#b794f4",
  fontFamily: '"Inter Tight", system-ui, sans-serif',
};
export type ChartTheme = typeof INTRADAY_THEME;

export const CHART_W = 820;
export const CHART_H = 460;
export const PAD_L = 56;
export const PAD_R = 56;
export const PAD_T = 12;
export const PAD_B = 28;
export const VOL_GAP = 4;
export const VOL_H = 72;
export const VOL_PAD_T = 6;
export const TOTAL_H = CHART_H + VOL_GAP + VOL_H;

export interface ChartFlags {
  vwap: boolean; cdp: boolean; camarilla: boolean; volume: boolean; ma: boolean;
}

export interface IntradayChartInput {
  candles: IntradayCandle[];
  prevClose: number | null;
  cdp: CdpLevels | null;
  camarilla: CamarillaLevels | null;
  ma: MaLevels | null;
  flags: ChartFlags;
  theme?: ChartTheme;
}
```

- [ ] **Step 2：commit**

```bash
git add frontend/src/lib/intraday-chart-svg.tsx
git commit -m "feat(chart-svg): intraday 共用畫圖模組骨架(theme/常數/型別)"
```

## Task 2：`computeIntradayGeometry` 純函式 + 測試

把 `IntradayChart.tsx:90-244` 的 useMemo 主體**原封搬成純函式**(輸入改成參數、輸出同樣那組值)。這是可測的幾何核心。

**Files:**
- Modify: `frontend/src/lib/intraday-chart-svg.tsx`
- Test: `frontend/src/lib/intraday-chart-svg.test.ts`

- [ ] **Step 1：寫 failing test**

```ts
import { describe, it, expect } from "vitest";
import { computeIntradayGeometry, CHART_W, PAD_L } from "./intraday-chart-svg";
import type { IntradayCandle } from "./api";

function candle(min: number, close: number): IntradayCandle {
  const hh = String(Math.floor(min / 60)).padStart(2, "0");
  const mm = String(min % 60).padStart(2, "0");
  return { date: `2026-06-05T${hh}:${mm}:00.000+08:00`, open: close, high: close, low: close, close, volume: 100, average: close };
}

const FLAGS = { vwap: true, cdp: true, camarilla: false, volume: true, ma: true };

describe("computeIntradayGeometry", () => {
  it("濾掉非正盤時段的 candle(只留 9:00–13:30)", () => {
    const candles = [candle(530, 100), candle(540, 101), candle(810, 102), candle(820, 103)];
    const g = computeIntradayGeometry({ candles, prevClose: 100, cdp: null, camarilla: null, ma: null, flags: FLAGS });
    expect(g.filteredCandles.map((c) => c.close)).toEqual([101, 102]);
  });

  it("scaleX:9:00 在左內緣、13:30 在右內緣", () => {
    const candles = [candle(540, 100), candle(810, 100)];
    const g = computeIntradayGeometry({ candles, prevClose: 100, cdp: null, camarilla: null, ma: null, flags: FLAGS });
    expect(g.scaleX(540)).toBeCloseTo(PAD_L, 5);
    expect(g.scaleX(810)).toBeCloseTo(CHART_W - 56, 5); // PAD_R = 56
  });

  it("CDP 超出 ±10% 的 key 被濾掉", () => {
    const candles = [candle(540, 100)];
    const cdp = { ah: 200, nh: 105, cdp: 100, nl: 95, al: 1, as_of_date: "2026-06-04" };
    const g = computeIntradayGeometry({ candles, prevClose: 100, cdp, camarilla: null, ma: null, flags: FLAGS });
    expect(g.visibleCdpKeys.sort()).toEqual(["cdp", "nh", "nl"]); // ah(200)/al(1) 出界
  });

  it("空 candles 不爆,回安全空值", () => {
    const g = computeIntradayGeometry({ candles: [], prevClose: 100, cdp: null, camarilla: null, ma: null, flags: FLAGS });
    expect(g.filteredCandles).toEqual([]);
    expect(g.polyClose).toBe("");
  });
});
```

- [ ] **Step 2：跑測試確認 fail**

Run: `cd frontend; npm test -- intraday-chart-svg`
Expected: FAIL（`computeIntradayGeometry` 尚未匯出）

- [ ] **Step 3：實作 `computeIntradayGeometry`**

把 `IntradayChart.tsx:98-244`（useMemo 的 callback body）搬進此函式。改動規則:
- 函式簽章:`export function computeIntradayGeometry(input: IntradayChartInput): IntradayGeometry`
- 內部從 `input` 解構 `candles, prevClose, cdp, camarilla, ma, flags`;原本讀 state 的 `showCdp/showCamarilla/showMa/showVwap` 改讀 `flags.cdp/.camarilla/.ma/.vwap`。
- 回傳的物件**型別**宣告為 `IntradayGeometry`(下方),欄位與原 useMemo 回傳完全相同:`yMin, yMax, scaleX, scaleY, polyClose, polyVwap, visibleCdpKeys, visibleCamKeys, visibleMaKeys, todayHigh, todayHighIdx, todayLow, todayLowIdx, maxVolume, scaleVolY, volBarW, resolvedLabels, minutesByIdx, filteredCandles`。
- 常數 `CHART_W/CHART_H/PAD_*/VOL_*/TOTAL_H` 改用本模組頂部 export 的那組(刪掉 useMemo 內對 IntradayChart 區域常數的依賴)。
- label 顏色:原本 inline hex（#e85a4f / #3b82f6 / #f0b429 / #b794f4 / #8a8273）改用 `theme`（`input.theme ?? INTRADAY_THEME`)對應欄位（accent / camarilla / ma5 / ma20 / inkDim）。

型別:

```ts
export interface IntradayGeometry {
  yMin: number; yMax: number;
  scaleX: (m: number) => number;
  scaleY: (v: number) => number;
  polyClose: string; polyVwap: string;
  visibleCdpKeys: Array<"ah" | "nh" | "cdp" | "nl" | "al">;
  visibleCamKeys: Array<"h4" | "h3" | "h2" | "h1" | "l1" | "l2" | "l3" | "l4">;
  visibleMaKeys: Array<"sma_5" | "sma_20">;
  todayHigh: number; todayHighIdx: number; todayLow: number; todayLowIdx: number;
  maxVolume: number; scaleVolY: (v: number) => number; volBarW: number;
  resolvedLabels: ReturnType<typeof resolveCollisions>;
  minutesByIdx: number[];
  filteredCandles: IntradayCandle[];
}
```

- [ ] **Step 4：跑測試確認 pass**

Run: `cd frontend; npm test -- intraday-chart-svg`
Expected: PASS（4 tests）

- [ ] **Step 5：commit**

```bash
git add frontend/src/lib/intraday-chart-svg.tsx frontend/src/lib/intraday-chart-svg.test.ts
git commit -m "feat(chart-svg): 抽 computeIntradayGeometry 純函式 + 單元測試"
```

## Task 3：`<IntradayChartStatic>` presentational 元件

把 `IntradayChart.tsx` 回傳的 SVG **靜態圖層**(不含 hover、不含外層 `<svg>`、不含 toggle 按鈕)搬成一個接 props 的元件,回傳 `<>…</>` fragment。**hover crosshair(591-640)不搬**,留在 IntradayChart。

**顏色轉換表(逐一替換,無遺漏):**

| 原本 | 換成 |
|---|---|
| `className="fill-bull"` / `stroke-bull` | `fill={t.bull}` / `stroke={t.bull}` |
| `className="fill-bear"` / `stroke-bear` | `fill={t.bear}` / `stroke={t.bear}` |
| `className="fill-ink"` | `fill={t.ink}` |
| `className="fill-ink-dim"` | `fill={t.inkDim}` |
| `stroke="var(--color-line, #2e2a22)"` | `stroke={t.line}` |
| `stroke="var(--color-accent, #e85a4f)"` | `stroke={t.accent}` |
| `stroke="var(--color-ink-dim, #8a8273)"` | `stroke={t.inkDim}` |
| `stroke="var(--color-ink, #ede4d3)"` | `stroke={t.ink}` |
| `stroke="#3b82f6"` / cam label `#3b82f6` | `{t.camarilla}` |
| `className="stroke-ma5"` | `stroke={t.ma5}` |
| `className="stroke-ma20"` | `stroke={t.ma20}` |
| `className="text-[12px] …"` | `fontSize={12}`（tabular 省略,resvg 不需要) |
| `className="text-[11px] …"` | `fontSize={11}` |
| 文字若未指定 fill | 補 `fill={t.ink}` 或對應色;並一律加 `fontFamily={t.fontFamily}` |

**搬移對照(IntradayChart.tsx 原始碼行 → 元件內的層,順序不變):**
1. 紅綠背景填色 + clipPath defs：359-387
2. Y 軸 ±2% 格線：391-415
3. CDP 5 線：418-427
4. Camarilla 8 線：430-444
5. MA5/MA20 線：448-462
6. VWAP 線：465-468
7. 右側 margin label（resolvedLabels）：471-484
8. 主價線（clip 紅綠 + fallback）：487-501
9. 今日高低 marker：506-539
10. 成交量子圖：542-574
11. X 軸時間 label：578-588

**Files:**
- Modify: `frontend/src/lib/intraday-chart-svg.tsx`

- [ ] **Step 1：寫元件簽章 + props**

```tsx
export interface IntradayChartStaticProps extends IntradayChartInput {
  geometry: IntradayGeometry;
}

export function IntradayChartStatic(props: IntradayChartStaticProps) {
  const t = props.theme ?? INTRADAY_THEME;
  const { flags, cdp, camarilla, ma } = props;
  const {
    scaleX, scaleY, scaleVolY, yMin, yMax, polyClose, polyVwap,
    visibleCdpKeys, visibleCamKeys, visibleMaKeys,
    todayHigh, todayHighIdx, todayLow, todayLowIdx,
    maxVolume, volBarW, resolvedLabels, minutesByIdx, filteredCandles,
  } = props.geometry;
  const baseline = props.prevClose ?? (filteredCandles[0]?.open ?? 0);
  if (filteredCandles.length === 0) return null;
  return (
    <>
      {/* 圖層依下方對照搬入 */}
    </>
  );
}
```

- [ ] **Step 2：搬入「紅綠背景填色 + clipPath」當作範例層(其餘層比照辦理)**

範例(對應 IntradayChart.tsx:359-387,顏色已 inline、`priceColorClass` 之類 helper 一併搬入本模組):

```tsx
{baseline > 0 && (() => {
  const baselineY = scaleY(baseline);
  const lastIdx = filteredCandles.length - 1;
  const points = [
    `${scaleX(minutesByIdx[0])},${baselineY}`,
    ...filteredCandles.map((c, i) => `${scaleX(minutesByIdx[i])},${scaleY(c.close)}`),
    `${scaleX(minutesByIdx[lastIdx])},${baselineY}`,
  ].join(" ");
  return (
    <>
      <defs>
        <clipPath id="above-baseline">
          <rect x={PAD_L} y={PAD_T} width={CHART_W - PAD_L - PAD_R} height={Math.max(0, baselineY - PAD_T)} />
        </clipPath>
        <clipPath id="below-baseline">
          <rect x={PAD_L} y={baselineY} width={CHART_W - PAD_L - PAD_R} height={Math.max(0, CHART_H - PAD_B - baselineY)} />
        </clipPath>
      </defs>
      <polygon points={points} fill={t.bull} fillOpacity="0.15" clipPath="url(#above-baseline)" />
      <polygon points={points} fill={t.bear} fillOpacity="0.15" clipPath="url(#below-baseline)" />
    </>
  );
})()}
```

- [ ] **Step 3：把對照表 2–11 的其餘 10 層逐一搬入**

逐層 read `IntradayChart.tsx` 對應行範圍,套「顏色轉換表」搬進 fragment,**順序不變**。注意:
- 主價線 / 背景填色用同樣的 `clipPath` id（`above-baseline` / `below-baseline`)。
- `priceColorClass(price, baseline)` helper(IntradayChart.tsx:43-47)搬進本模組,回傳 hex（`> baseline → t.bull`、`< baseline → t.bear`、`= → t.ink`)。
- `formatVolume`（IntradayChart.tsx:36-40)搬進本模組。
- 所有 `<text>` 補 `fontFamily={t.fontFamily}`。

- [ ] **Step 4：暫不跑（無獨立測試,Task 4 用 snapshot 驗）**

- [ ] **Step 5：commit**

```bash
git add frontend/src/lib/intraday-chart-svg.tsx
git commit -m "feat(chart-svg): IntradayChartStatic presentational 元件(顏色 inline)"
```

## Task 4：snapshot 測試(防漂移 — user 要的零漂移保證)

**Files:**
- Test: `frontend/src/lib/intraday-chart-svg.test.ts`

- [ ] **Step 1：加 snapshot 測試**(用 `createElement`,不寫 JSX,符合 `*.test.ts` 慣例)

```ts
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { IntradayChartStatic, computeIntradayGeometry } from "./intraday-chart-svg";

it("IntradayChartStatic 輸出 SVG snapshot(防漂移)", () => {
  const candles = [candle(540, 100), candle(600, 103), candle(660, 99), candle(810, 102)];
  const input = {
    candles, prevClose: 100,
    cdp: { ah: 108, nh: 104, cdp: 101, nl: 98, al: 95, as_of_date: "2026-06-04" },
    camarilla: null,
    ma: { symbol: "2330", sma_5: 100.5, sma_20: 99.2, as_of_date: "2026-06-04" },
    flags: { vwap: true, cdp: true, camarilla: false, volume: true, ma: true },
  };
  const geometry = computeIntradayGeometry(input);
  const svg = renderToStaticMarkup(createElement(IntradayChartStatic, { ...input, geometry }));
  expect(svg).toMatchSnapshot();
});
```

- [ ] **Step 2：跑測試產生 snapshot**

Run: `cd frontend; npm test -- intraday-chart-svg`
Expected: PASS（新 snapshot 寫入 `__snapshots__/`）。若報 JSX 轉譯錯,確認 `frontend/tsconfig.json` 有 `"jsx": "react-jsx"`。

- [ ] **Step 3：肉眼檢查 snapshot 合理**

開 `frontend/src/lib/__snapshots__/intraday-chart-svg.test.ts.snap`,確認含 `<polygon`、`<polyline`、`<clipPath`、CDP `<line` 等,且顏色是 hex(無 `var(`、無 Tailwind class)。

- [ ] **Step 4：commit**

```bash
git add frontend/src/lib/intraday-chart-svg.test.ts frontend/src/lib/__snapshots__/
git commit -m "test(chart-svg): IntradayChartStatic snapshot 鎖外觀防漂移"
```

## Task 5：IntradayChart 改用共用模組(網頁外觀零變化)

**Files:**
- Modify: `frontend/src/components/IntradayChart.tsx`

- [ ] **Step 1：改 import + 用 `computeIntradayGeometry`**

- 頂部刪掉本檔的 `CHART_W/CHART_H/PAD_*/VOL_*/TOTAL_H` 常數、`formatVolume`、`priceColorClass`,改 import:

```tsx
import {
  INTRADAY_THEME, CHART_W, CHART_H, PAD_L, PAD_R, PAD_T, PAD_B, TOTAL_H,
  computeIntradayGeometry, IntradayChartStatic,
} from "../lib/intraday-chart-svg";
```

- 把 line 90-244 的 useMemo 換成呼叫純函式(state→flags):

```tsx
const geometry = useMemo(
  () => computeIntradayGeometry({
    candles, prevClose, cdp, camarilla, ma,
    flags: { vwap: showVwap, cdp: showCdp, camarilla: showCamarilla, volume: showVolume, ma: showMa },
  }),
  [candles, cdp, showCdp, camarilla, showCamarilla, ma, showMa, showVwap, prevClose, showVolume],
);
const {
  scaleX, scaleY, scaleVolY, minutesByIdx, filteredCandles, /* hover 用到的 */
} = geometry;
```

- [ ] **Step 2：把 `<svg>` 裡的靜態圖層換成 `<IntradayChartStatic>`**

`<svg …>` 內,把原本 359-588 那一大段靜態圖層**整段刪除**,改成一行:

```tsx
<IntradayChartStatic
  candles={candles} prevClose={prevClose} cdp={cdp} camarilla={camarilla} ma={ma}
  flags={{ vwap: showVwap, cdp: showCdp, camarilla: showCamarilla, volume: showVolume, ma: showMa }}
  geometry={geometry}
/>
```

hover crosshair(591-640)維持原樣留在 `<svg>` 內、`<IntradayChartStatic>` 之後(畫在上層)。`<svg>` 外層、header、toggle 按鈕、useEffect 全部不動。

- [ ] **Step 3：跑前端測試 + build + 目視**

Run: `cd frontend; npm test` → 全綠（含既有測試）。
Run: `cd frontend; npm run build` → 無 TS 錯。
Run: `.\start.ps1`,開 http://localhost:5173 挑一檔,**逐一切 VWAP/CDP/CAM/VOL/MA、hover** 確認與改動前**像素一致**。

- [ ] **Step 4：commit**

```bash
git add frontend/src/components/IntradayChart.tsx
git commit -m "refactor(IntradayChart): 改用共用 IntradayChartStatic(外觀不變)"
```

---

# Phase 2 — Discord Bot

## Task 6：bot 專案骨架

**Files:**
- Create: `bot/package.json`、`bot/tsconfig.json`、`bot/.gitignore`、`bot/.env.example`、`bot/vitest.config.ts`

- [ ] **Step 1：`bot/package.json`**

```json
{
  "name": "treading-king-bot",
  "private": true,
  "type": "module",
  "scripts": {
    "start": "tsx src/index.ts",
    "test": "vitest run"
  },
  "dependencies": {
    "@resvg/resvg-js": "^2.6.2",
    "discord.js": "^14.16.3",
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  },
  "devDependencies": {
    "@types/react": "^18.3.3",
    "@types/react-dom": "^18.3.0",
    "tsx": "^4.19.2",
    "typescript": "^5.5.3",
    "vitest": "^4.1.6"
  }
}
```

- [ ] **Step 2：`bot/tsconfig.json`**（`jsx: react-jsx` 讓 render.ts 能用 createElement/JSX;`allowImportingTsExtensions` 關掉,靠 tsx/esbuild 解析跨包 import)

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "jsx": "react-jsx",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "types": ["node", "vitest/globals"]
  },
  "include": ["src", "../frontend/src/lib"]
}
```

- [ ] **Step 3：`bot/.gitignore`**

```
node_modules/
.env
```

- [ ] **Step 4：`bot/.env.example`**

```
# Discord bot token（Developer Portal → Bot → Reset Token）
DISCORD_BOT_TOKEN=
# 後端位址（start.ps1 跑在這）
BACKEND_BASE_URL=http://127.0.0.1:8000
# 後端有設 BFF_API_KEY 時必填且需一致；沒設留空
BFF_API_KEY=
# 限制回應頻道（逗號分隔 channel id）；留空 = 任何看得到的頻道都回
BOT_ALLOWED_CHANNELS=
```

- [ ] **Step 5：`bot/vitest.config.ts`**

```ts
import { defineConfig } from "vitest/config";
export default defineConfig({
  test: { globals: true, environment: "node", include: ["src/**/*.test.ts"] },
});
```

- [ ] **Step 6：安裝 + commit**

```bash
cd bot; npm install; cd ..
git add bot/package.json bot/package-lock.json bot/tsconfig.json bot/.gitignore bot/.env.example bot/vitest.config.ts
git commit -m "chore(bot): Discord bot 專案骨架(discord.js + resvg + tsx)"
```

## Task 7：代號解析 `symbol.ts`（TDD）

**Files:**
- Create: `bot/src/symbol.ts`
- Test: `bot/src/symbol.test.ts`

- [ ] **Step 1：failing test**

```ts
import { describe, it, expect } from "vitest";
import { parseSymbolCommand } from "./symbol";

describe("parseSymbolCommand", () => {
  it.each(["p2330", "P2330", "p0050", "p00878", "p2330B"])("命中 %s", (msg) => {
    expect(parseSymbolCommand(msg)).toBe(msg.slice(1).toUpperCase());
  });
  it.each(["people", "2330", "p12", "p2330 走勢", "hello p2330", ""])("不命中 %s", (msg) => {
    expect(parseSymbolCommand(msg)).toBeNull();
  });
});
```

- [ ] **Step 2：跑確認 fail** — `cd bot; npm test -- symbol`（Expected: FAIL）

- [ ] **Step 3：實作**

```ts
// 只在「整則訊息 = p + 合法台股代號」時觸發,避免讀到雜訊洗頻。
const RE = /^[pP]([0-9]{4,6}[A-Z]{0,2})$/;
export function parseSymbolCommand(content: string): string | null {
  const m = content.trim().match(RE);
  return m ? m[1].toUpperCase() : null;
}
```

- [ ] **Step 4：跑確認 pass** — `cd bot; npm test -- symbol`（Expected: PASS）

- [ ] **Step 5：commit**

```bash
git add bot/src/symbol.ts bot/src/symbol.test.ts
git commit -m "feat(bot): p<代號> 解析 + 測試"
```

## Task 8：設定載入 `config.ts`

**Files:**
- Create: `bot/src/config.ts`

- [ ] **Step 1：實作**

```ts
import "dotenv/config";

function required(name: string): string {
  const v = (process.env[name] ?? "").trim();
  if (!v) { console.error(`[bot] 缺少必要環境變數 ${name}`); process.exit(1); }
  return v;
}

export const config = {
  token: required("DISCORD_BOT_TOKEN"),
  backendBaseUrl: (process.env.BACKEND_BASE_URL ?? "http://127.0.0.1:8000").trim(),
  bffApiKey: (process.env.BFF_API_KEY ?? "").trim(),
  allowedChannels: (process.env.BOT_ALLOWED_CHANNELS ?? "")
    .split(",").map((s) => s.trim()).filter(Boolean),
};
```

> 註:`discord.js` 已相依 `dotenv`?否則 `bot/package.json` dependencies 加 `"dotenv": "^16.4.5"` 後 `npm install`。

- [ ] **Step 2：commit**

```bash
git add bot/src/config.ts bot/package.json bot/package-lock.json
git commit -m "feat(bot): env 設定載入 + 驗證"
```

## Task 9：資料 client `data.ts`

打現有 Python endpoint;型別跟前端 `api.ts` 對齊。

**Files:**
- Create: `bot/src/data.ts`

- [ ] **Step 1：實作**

```ts
import { config } from "./config";
import type { IntradayCandle, CdpLevels, CamarillaLevels, MaLevels } from "../../frontend/src/lib/api";

async function get<T>(path: string): Promise<T> {
  const headers: Record<string, string> = {};
  if (config.bffApiKey) headers["X-API-Key"] = config.bffApiKey;
  const res = await fetch(`${config.backendBaseUrl}${path}`, { headers });
  if (!res.ok) throw new Error(`${path} → ${res.status}`);
  return res.json() as Promise<T>;
}

export interface QuoteLevel { price: number; size: number; }
export interface QuoteResp {
  bids: QuoteLevel[]; asks: QuoteLevel[];
  is_limit_up_bid: boolean; is_limit_up_ask: boolean;
  is_limit_down_bid: boolean; is_limit_down_ask: boolean;
}
export interface CandlesResp {
  date: string; symbol: string; data: IntradayCandle[]; prev_close: number | null;
}

export const getQuote = (s: string) => get<QuoteResp>(`/api/quote/${encodeURIComponent(s)}`);
export const getCandles = (s: string) => get<CandlesResp>(`/api/candles/${encodeURIComponent(s)}/intraday`);
export const getCdp = (s: string) => get<CdpLevels>(`/api/cdp/${encodeURIComponent(s)}`);
export const getMa = (s: string) => get<MaLevels>(`/api/ma/${encodeURIComponent(s)}`);

interface SymbolRow { symbol: string; name: string; }
export async function getName(s: string): Promise<string | null> {
  try {
    const r = await get<{ results: SymbolRow[] }>(`/api/symbols?search=${encodeURIComponent(s)}&limit=20`);
    return r.results.find((row) => row.symbol === s)?.name ?? null;
  } catch { return null; }
}
```

- [ ] **Step 2：commit**

```bash
git add bot/src/data.ts
git commit -m "feat(bot): 後端資料 client(quote/candles/cdp/ma/name)"
```

## Task 10：快取 `cache.ts`（TDD）

per-symbol、TTL 30s。**用注入的 `now()` 讓 TTL 可測**(不依賴真實時鐘)。

**Files:**
- Create: `bot/src/cache.ts`
- Test: `bot/src/cache.test.ts`

- [ ] **Step 1：failing test**

```ts
import { describe, it, expect, vi } from "vitest";
import { TtlCache } from "./cache";

describe("TtlCache", () => {
  it("TTL 內回快取、過期後重抓", async () => {
    let t = 1000;
    const cache = new TtlCache<number>(30_000, () => t);
    const loader = vi.fn(async () => t);
    expect(await cache.get("2330", loader)).toBe(1000);
    t = 21_000; // +20s,仍在 30s TTL 內
    expect(await cache.get("2330", loader)).toBe(1000); // 命中,不重抓
    expect(loader).toHaveBeenCalledTimes(1);
    t = 32_000; // +31s 過期
    expect(await cache.get("2330", loader)).toBe(32_000);
    expect(loader).toHaveBeenCalledTimes(2);
  });
});
```

- [ ] **Step 2：跑確認 fail** — `cd bot; npm test -- cache`（Expected: FAIL）

- [ ] **Step 3：實作**

```ts
// 簡單 per-key TTL 快取;now() 可注入以便測試。
export class TtlCache<T> {
  private store = new Map<string, { at: number; val: T }>();
  constructor(private ttlMs: number, private now: () => number = () => Date.now()) {}
  async get(key: string, loader: () => Promise<T>): Promise<T> {
    const hit = this.store.get(key);
    if (hit && this.now() - hit.at < this.ttlMs) return hit.val;
    const val = await loader();
    this.store.set(key, { at: this.now(), val });
    return val;
  }
}
```

- [ ] **Step 4：跑確認 pass** — `cd bot; npm test -- cache`（Expected: PASS）

- [ ] **Step 5：commit**

```bash
git add bot/src/cache.ts bot/src/cache.test.ts
git commit -m "feat(bot): 30s TTL 快取 + 測試"
```

## Task 11：產圖 `render.ts`（resvg）

**Files:**
- Create: `bot/src/render.ts`
- 需先放入字型:`bot/assets/NotoSansTC-Regular.ttf`（從 Google Fonts 下載 Noto Sans TC Regular;授權 OFL 可內嵌）

- [ ] **Step 1：放字型檔**

下載 Noto Sans TC Regular `.ttf` 放到 `bot/assets/NotoSansTC-Regular.ttf`。

- [ ] **Step 2：實作 `render.ts`**

```tsx
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { Resvg } from "@resvg/resvg-js";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import {
  IntradayChartStatic, computeIntradayGeometry, INTRADAY_THEME,
  CHART_W, CHART_H, TOTAL_H, type IntradayChartInput,
} from "../../frontend/src/lib/intraday-chart-svg";

const FONT = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "../assets/NotoSansTC-Regular.ttf"));
const TITLE_H = 44;
const THEME = { ...INTRADAY_THEME, fontFamily: "Noto Sans TC" };

export function renderChartPng(args: IntradayChartInput & {
  symbol: string; name: string | null; lastClose: number; change: number; changePct: number;
}): Buffer {
  const input: IntradayChartInput = { ...args, theme: THEME };
  const geometry = computeIntradayGeometry(input);
  const dirColor = args.change > 0 ? THEME.bull : args.change < 0 ? THEME.bear : THEME.ink;
  const chartH = args.flags.volume ? TOTAL_H : CHART_H;
  const totalH = chartH + TITLE_H;
  const arrow = args.change > 0 ? "▲" : args.change < 0 ? "▾" : "—";

  const svg = renderToStaticMarkup(
    createElement("svg", { xmlns: "http://www.w3.org/2000/svg", viewBox: `0 0 ${CHART_W} ${totalH}`, width: CHART_W, height: totalH },
      createElement("rect", { x: 0, y: 0, width: CHART_W, height: totalH, fill: THEME.bg }),
      createElement("text", { x: 14, y: 30, fontSize: 22, fontFamily: "Noto Sans TC", fill: THEME.ink },
        `${args.symbol} ${args.name ?? ""}`),
      createElement("text", { x: CHART_W - 14, y: 30, fontSize: 22, textAnchor: "end", fontFamily: "Noto Sans TC", fill: dirColor },
        `${args.lastClose.toFixed(2)}  ${arrow}${Math.abs(args.change).toFixed(2)} (${args.changePct >= 0 ? "+" : ""}${args.changePct.toFixed(2)}%)`),
      createElement("g", { transform: `translate(0, ${TITLE_H})` },
        createElement(IntradayChartStatic, { ...input, geometry }),
      ),
    ),
  );

  const resvg = new Resvg(svg, {
    fitTo: { mode: "zoom", value: 2 },
    font: { fontBuffers: [FONT], defaultFontFamily: "Noto Sans TC", loadSystemFonts: false },
  });
  return Buffer.from(resvg.render().asPng());
}
```

- [ ] **Step 3：型別檢查**

Run: `cd bot; npx tsc --noEmit`
Expected: 無錯（若報跨包型別,確認 tsconfig `include` 含 `../frontend/src/lib`)。

- [ ] **Step 4：commit**

```bash
git add bot/src/render.ts bot/assets/NotoSansTC-Regular.ttf
git commit -m "feat(bot): resvg 產圖(重用 IntradayChartStatic + 標題帶 + CJK 字型)"
```

## Task 12：五檔階梯 + embed `embed.ts`（TDD 階梯格式)

**Files:**
- Create: `bot/src/embed.ts`
- Test: `bot/src/embed.test.ts`

- [ ] **Step 1：failing test（只測純格式化函式 `formatLadder`)**

```ts
import { describe, it, expect } from "vitest";
import { formatLadder } from "./embed";

const lvl = (price: number, size: number) => ({ price, size });

describe("formatLadder", () => {
  it("賣5→買5 排列、量單位張、不足補 —、price=0 顯示市價", () => {
    const out = formatLadder(
      [lvl(634.5, 340), lvl(635.0, 210), lvl(635.5, 88)],  // bids: 買1..買3
      [lvl(636.0, 120), lvl(0, 0)],                          // asks: 賣1..賣2(賣2 鎖停=市價)
    );
    const lines = out.split("\n");
    expect(lines[0]).toContain("賣5"); expect(lines[0]).toContain("—");      // 賣5 缺檔
    expect(lines.some((l) => l.includes("市價"))).toBe(true);                 // 賣2 price=0 → 市價
    expect(lines.some((l) => l.includes("買1") && l.includes("634.50") && l.includes("340"))).toBe(true);
    expect(lines.some((l) => l.startsWith("───"))).toBe(true);
  });
});
```

- [ ] **Step 2：跑確認 fail** — `cd bot; npm test -- embed`（Expected: FAIL）

- [ ] **Step 3：實作 `embed.ts`**

```ts
import { EmbedBuilder, AttachmentBuilder } from "discord.js";
import type { QuoteResp } from "./data";
import type { CdpLevels, MaLevels } from "../../frontend/src/lib/api";
import { INTRADAY_THEME } from "../../frontend/src/lib/intraday-chart-svg";

type Lvl = { price: number; size: number };
const cell = (p: number) => (p === 0 ? "市價".padStart(7) : p.toFixed(2).padStart(7));
const qty = (s: number) => (s > 0 ? String(s).padStart(6) : "—".padStart(6));

export function formatLadder(bids: Lvl[], asks: Lvl[]): string {
  // 缺檔(回傳不足 5 檔)→ 價量都 —;有檔但 price=0(鎖漲跌停)→ 市價。
  const row = (label: string, lv: Lvl | undefined) =>
    lv ? `${label} ${cell(lv.price)} ${qty(lv.size)}` : `${label} ${"—".padStart(7)} ${"—".padStart(6)}`;
  const lines: string[] = [];
  for (let i = 4; i >= 0; i--) lines.push(row(`賣${i + 1}`, asks[i]));
  lines.push("───────────────");
  for (let i = 0; i < 5; i++) lines.push(row(`買${i + 1}`, bids[i]));
  return lines.join("\n");
}

const sumSize = (a: Lvl[]) => a.reduce((n, x) => n + x.size, 0);

export function buildReply(args: {
  symbol: string; name: string | null; lastClose: number; change: number; changePct: number;
  open: number; high: number; low: number; vwap: number; volume: number;
  cdp: CdpLevels | null; ma: MaLevels | null; quote: QuoteResp; png: Buffer; asOf: string;
}) {
  const up = args.change > 0;
  const color = up ? 0xe85a4f : args.change < 0 ? 0x7fc99a : 0x8a8273;
  const file = new AttachmentBuilder(args.png, { name: "chart.png" });
  const ladder = formatLadder(args.quote.bids, args.quote.asks);
  const limit = args.quote.is_limit_up_bid || args.quote.is_limit_up_ask ? "　🔺鎖漲停"
    : args.quote.is_limit_down_bid || args.quote.is_limit_down_ask ? "　🔻鎖跌停" : "";
  const arrow = up ? "▲" : args.change < 0 ? "▾" : "—";
  const cdp = args.cdp ? `AH ${args.cdp.ah} ／ NH ${args.cdp.nh} ／ CDP ${args.cdp.cdp} ／ NL ${args.cdp.nl} ／ AL ${args.cdp.al}` : "—";
  const ma = args.ma ? `MA5 ${args.ma.sma_5 ?? "—"} ／ MA20 ${args.ma.sma_20 ?? "—"}` : "—";

  const embed = new EmbedBuilder()
    .setColor(color)
    .setTitle(`${args.name ?? ""} ${args.symbol}`.trim())
    .setDescription(
      `**${args.lastClose.toFixed(2)}**　${arrow}${Math.abs(args.change).toFixed(2)} (${args.changePct >= 0 ? "+" : ""}${args.changePct.toFixed(2)}%)${limit}\n` +
      "```\n" + ladder + "\n```",
    )
    .addFields(
      { name: "開 / 高 / 低", value: `${args.open} / ${args.high} / ${args.low}`, inline: true },
      { name: "均價 / 量", value: `${args.vwap.toFixed(2)} / ${args.volume}`, inline: true },
      { name: "委買 / 委賣(張)", value: `${sumSize(args.quote.bids)} / ${sumSize(args.quote.asks)}`, inline: true },
      { name: "CDP", value: cdp, inline: false },
      { name: "均線", value: ma, inline: false },
    )
    .setImage("attachment://chart.png")
    .setFooter({ text: `資料 ${args.asOf}` });
  void INTRADAY_THEME; // 顏色與圖一致(已用 hex)
  return { embeds: [embed], files: [file] };
}
```

- [ ] **Step 4：跑確認 pass** — `cd bot; npm test -- embed`（Expected: PASS）

- [ ] **Step 5：commit**

```bash
git add bot/src/embed.ts bot/src/embed.test.ts
git commit -m "feat(bot): 五檔階梯 + embed 組裝 + 測試"
```

## Task 13：bot 入口 `index.ts`（orchestration）

**Files:**
- Create: `bot/src/index.ts`

- [ ] **Step 1：實作**

```ts
import { Client, GatewayIntentBits, Events, type Message } from "discord.js";
import { config } from "./config";
import { parseSymbolCommand } from "./symbol";
import { getQuote, getCandles, getCdp, getMa, getName } from "./data";
import { TtlCache } from "./cache";
import { renderChartPng } from "./render";
import { buildReply } from "./embed";
import { MARKET_OPEN_MIN, MARKET_CLOSE_MIN, minuteOfDay } from "../../frontend/src/lib/intraday-time";

// 慢資料(分時 K / CDP / MA / 已 render PNG)30s 快取;五檔即時抓(見 handle)。
const slow = new TtlCache<Awaited<ReturnType<typeof loadSlow>>>(30_000);

async function loadSlow(symbol: string) {
  const [candlesR, cdp, ma, name] = await Promise.all([
    getCandles(symbol), getCdp(symbol).catch(() => null), getMa(symbol).catch(() => null), getName(symbol),
  ]);
  const flags = { vwap: true, cdp: true, camarilla: false, volume: true, ma: true };
  const intraday = candlesR.data.filter((c) => {
    const m = minuteOfDay(c.date); return m >= MARKET_OPEN_MIN && m <= MARKET_CLOSE_MIN;
  });
  if (intraday.length === 0) {
    return { empty: true as const, cdp, ma, name, prevClose: candlesR.prev_close };
  }
  const last = intraday[intraday.length - 1];
  const baseline = candlesR.prev_close ?? intraday[0].open;
  const change = last.close - baseline;
  const changePct = baseline ? (change / baseline) * 100 : 0;
  const high = Math.max(...intraday.map((c) => c.high));
  const low = Math.min(...intraday.map((c) => c.low));
  const png = renderChartPng({
    candles: candlesR.data, prevClose: candlesR.prev_close, cdp, camarilla: null, ma, flags,
    symbol, name, lastClose: last.close, change, changePct,
  });
  return {
    empty: false as const, name, cdp, ma, png,
    lastClose: last.close, change, changePct,
    open: intraday[0].open, high, low, vwap: last.average, volume: intraday.reduce((n, c) => n + c.volume, 0),
    asOf: last.date.slice(11, 16),
  };
}

async function handle(msg: Message, symbol: string) {
  try {
    const [s, quote] = await Promise.all([slow.get(symbol, () => loadSlow(symbol)), getQuote(symbol)]);
    if (s.empty) { await msg.reply(`\`${symbol}\` 目前無分時資料(盤前/非交易日)。CDP:${s.cdp ? `${s.cdp.cdp}` : "—"} MA5:${s.ma?.sma_5 ?? "—"}`); return; }
    const reply = buildReply({
      symbol, name: s.name, lastClose: s.lastClose, change: s.change, changePct: s.changePct,
      open: s.open, high: s.high, low: s.low, vwap: s.vwap, volume: s.volume,
      cdp: s.cdp, ma: s.ma, quote, png: s.png, asOf: s.asOf,
    });
    await msg.reply(reply);
  } catch (e) {
    await msg.reply(`\`${symbol}\` 查詢失敗(行情暫時不可用)。`);
    console.warn(`[bot] ${symbol} 失敗:`, e);
  }
}

const client = new Client({
  intents: [GatewayIntentBits.Guilds, GatewayIntentBits.GuildMessages, GatewayIntentBits.MessageContent],
});

client.once(Events.ClientReady, (c) => console.log(`[bot] 上線:${c.user.tag}`));
client.on(Events.MessageCreate, (msg) => {
  if (msg.author.bot) return;
  if (config.allowedChannels.length && !config.allowedChannels.includes(msg.channelId)) return;
  const symbol = parseSymbolCommand(msg.content);
  if (!symbol) return;
  void handle(msg, symbol);
});

client.login(config.token);
```

- [ ] **Step 2：型別檢查** — `cd bot; npx tsc --noEmit`（Expected: 無錯）

- [ ] **Step 3：commit**

```bash
git add bot/src/index.ts
git commit -m "feat(bot): discord client + messageCreate orchestration"
```

## Task 14：啟動整合（start.ps1 / install.ps1）

**Files:**
- Modify: `start.ps1`
- Modify: `install.ps1`

- [ ] **Step 1：`start.ps1` 在 frontend 視窗後加第三個視窗**

於 `start.ps1`「Launch frontend」區塊後加:

```powershell
# ---------- Launch Discord bot ----------
$botEnv = Join-Path $root "bot\.env"
if (Test-Path $botEnv) {
  Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "Set-Location '$root\bot'; npm run start"
  )
} else {
  Write-Host "WARN: bot\.env not found — Discord bot 未啟動(複製 bot\.env.example 並填 DISCORD_BOT_TOKEN)。" -ForegroundColor Yellow
}
```

- [ ] **Step 2：`install.ps1` 加 bot 安裝**

在既有 npm install 之後加(對齊現有寫法):`Set-Location "$root\bot"; npm install`。

- [ ] **Step 3：手動驗收**

1. `Copy-Item bot\.env.example bot\.env`,填入 `DISCORD_BOT_TOKEN`(+ 後端有設就填 `BFF_API_KEY`)。
2. Discord Developer Portal:Bot → 開 **Message Content Intent**;OAuth2 URL Generator 勾 `bot`,權限勾 `Send Messages`/`Embed Links`/`Attach Files`/`Read Message History`,邀請進群組。
3. `.\start.ps1` → bot 視窗顯示「上線」。
4. 頻道打 `p2330` → 收到 embed + 分時圖;打 `people` 無反應;打 `p0050` 正常。
5. 30s 內重打同檔 → 圖秒回(快取);五檔每次都是最新。

- [ ] **Step 4：commit**

```bash
git add start.ps1 install.ps1
git commit -m "chore(bot): start.ps1 / install.ps1 整合 Discord bot"
```

---

## 自我複審結果（writing-plans self-review）

- **Spec 覆蓋**:架構(Node bot 消費 REST、富邦不動)✓Task6-13;產圖 resvg 重用網頁圖 ✓Task3/11;`p2330` + Message Content Intent ✓Task7/14;embed + 五檔階梯(張/市價/漲跌停)✓Task12;30s 快取+五檔即時 ✓Task10/13;Camarilla 預設關 ✓(flags.camarilla=false);錯誤/盤前處理 ✓Task13;重構防漂移 snapshot ✓Task4;成功標準 ✓Task14 驗收。
- **無佔位符**:新邏輯(theme/geometry/bot 全檔)皆完整程式碼;唯一「依賴既有原始碼」的是 Task3 靜態圖層搬移 —— 已用「顏色轉換表 + 原始行範圍對照 + 範例層 + snapshot 把關」精確界定,非模糊指示。
- **型別一致**:`IntradayChartInput`/`IntradayGeometry`/`IntradayChartStaticProps`/`ChartFlags`/`QuoteResp` 跨 Task 一致;`formatLadder`/`buildReply`/`renderChartPng`/`parseSymbolCommand`/`TtlCache` 名稱跨 Task 一致。
- **已知風險**:① 跨包 import 靠 tsx/esbuild —— 型別 import 一律 `import type`(避免把含 `import.meta.env` 的 `api.ts` 拉進 Node);② snapshot 測試需 `tsconfig.json` `jsx: react-jsx`(Task4 Step2 已提示);③ 富邦五檔陣列順序假設 index0=最佳檔(買1/賣1),驗收時若顛倒則於 `formatLadder` 反轉。

---

## 執行交接

計畫已存到 `docs/superpowers/plans/2026-06-05-discord-stock-bot.md`。兩種執行方式:

1. **Subagent-Driven(推薦)** — 每個 task 派新 subagent、task 間我幫你 review,迭代快。
2. **Inline 執行** — 在本 session 用 executing-plans 批次跑、checkpoint review。

要哪一種?
