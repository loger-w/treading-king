# 大盤指數 振幅 + 成交值(量能副圖)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 指數圖與 bot 加上「振幅」與「成交值」,以數字 + 分時量能副圖呈現,全部由已抓的 candles 純算,零後端改動。

**Architecture:** 改共用 SVG lib `index-intraday-svg.tsx`(網頁 + bot 同吃),量副圖比照個股 `intraday-chart-svg.tsx` §10,單位改億元、標籤改「成交值(億)」。新增兩個純函式 `fmtIndexVol` / `indexAmplitude` 供前端與 bot 共用(DRY)。

**Tech Stack:** React(`createElement`)+ react-dom/server + resvg(bot 產 PNG)、vitest(前端 + bot 同用 `vitest run`)。

**設計來源:** `docs/superpowers/specs/2026-06-09-index-volume-amplitude-design.md`

**關鍵事實:** 指數 candle 的 `volume` = 每分鐘**成交值(元)**,非張數(實測加總 = quote `total.tradeValue`)。所有量一律以億元呈現。

---

## 檔案結構

| 檔案 | 動作 | 職責 |
| --- | --- | --- |
| `frontend/src/lib/index-intraday-svg.tsx` | 改 | 加 `fmtIndexVol`、`indexAmplitude` 純函式;`computeIndexGeometry` 加量 pane 幾何;`IndexIntradayStatic` 畫量 pane |
| `frontend/src/lib/index-intraday-svg.test.ts` | 改 | `c()` helper 加 volume 參數;新增 fmtIndexVol / indexAmplitude / 量幾何測試;更新「不含量」舊測試 |
| `frontend/src/components/IndexIntradayChart.tsx` | 改 | viewBox 高度 `CHART_H`→`TOTAL_H`;header 加「振幅 / 成交值」行 |
| `bot/src/reply.ts` | 改 | `loadSlowIndex` 回傳 `amplitude`/`volume`;`composeReply` 指數分支把兩者帶進 `buildIndexReply` |
| `bot/src/reply.test.ts` | 改 | 指數 deps 量改非零;斷言 `amplitude`/`volume` |
| `bot/src/embed.ts` | 改 | `buildIndexReply` 加 `amplitude`/`volume` args + 「振幅 / 成交值」欄位 |
| `bot/src/embed.test.ts` | 建 | `buildIndexReply` 欄位測試 |
| `bot/src/render.ts` | 改 | `buildIndexChartSvg` 的 `totalH` 改含量 pane(`TOTAL_H + TITLE_H`) |

**不碰:** `intraday-chart-svg.tsx`(個股圖)、`IndexOverlayChart`(重疊%圖不加量)、後端任何檔案、`__snapshots__/intraday-chart-svg.test.ts.snap`(既有未提交改動,非本次範圍)。

---

## Task 1: `fmtIndexVol` + `indexAmplitude` 純函式

**Files:**
- Modify: `frontend/src/lib/index-intraday-svg.tsx`(在 `fmtIndex` 函式之後加兩個 export)
- Test: `frontend/src/lib/index-intraday-svg.test.ts`

- [ ] **Step 1: 寫失敗測試**

在 `index-intraday-svg.test.ts` 頂部 import 補上兩個新名字:

```ts
import { computeIndexGeometry, fmtIndex, fmtIndexVol, indexAmplitude, IndexIntradayStatic } from "./index-intraday-svg";
```

在 `describe("computeIndexGeometry", …)` 區塊之後,新增:

```ts
describe("fmtIndexVol", () => {
  it("元 → 億 + 千分位(指數量是成交值,不是張數)", () => {
    expect(fmtIndexVol(1151930775120)).toBe("11,519億"); // 全日 1.15 兆
    expect(fmtIndexVol(71496161960)).toBe("715億");        // 單分鐘最大
    expect(fmtIndexVol(0)).toBe("0億");
  });
});

describe("indexAmplitude", () => {
  it("(高−低)/昨收×100;2026-06-09 加權實值 ≈ 2.61", () => {
    expect(indexAmplitude(44821.71, 43687.62, 43502.78)).toBeCloseTo(2.61, 2);
  });
  it("無昨收 → null(振幅以昨收為分母,不硬算)", () => {
    expect(indexAmplitude(100, 90, null)).toBeNull();
  });
});
```

- [ ] **Step 2: 跑測試確認失敗**

Run(在 `frontend/`):`npx vitest run src/lib/index-intraday-svg.test.ts`
Expected: FAIL —「fmtIndexVol is not a function / indexAmplitude is not exported」。

- [ ] **Step 3: 實作**

在 `index-intraday-svg.tsx` 既有 `fmtIndex` 函式(約 line 14-16)之後加:

```tsx
/** 指數成交值格式化:元 → 億(四捨五入整數)+ 千分位。指數 candle.volume 是成交值(元)非張數。 */
export function fmtIndexVol(valueYuan: number): string {
  const yi = Math.round(valueYuan / 1e8);
  return `${String(yi).replace(/\B(?=(\d{3})+(?!\d))/g, ",")}億`;
}

/** 振幅 = (今日高 − 今日低) / 昨收 × 100。無昨收回 null。 */
export function indexAmplitude(high: number, low: number, prevClose: number | null): number | null {
  if (prevClose == null || prevClose === 0) return null;
  return ((high - low) / prevClose) * 100;
}
```

- [ ] **Step 4: 跑測試確認通過**

Run(在 `frontend/`):`npx vitest run src/lib/index-intraday-svg.test.ts`
Expected: PASS(含舊測試)。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/index-intraday-svg.tsx frontend/src/lib/index-intraday-svg.test.ts
git commit -m "feat(index): fmtIndexVol(成交值億)+ indexAmplitude 純函式"
```

---

## Task 2: `computeIndexGeometry` 量 pane 幾何

**Files:**
- Modify: `frontend/src/lib/index-intraday-svg.tsx`
- Test: `frontend/src/lib/index-intraday-svg.test.ts`

- [ ] **Step 1: 改測試 helper + 寫失敗測試**

把 `c()` helper(test 檔 line 7-11)改成可帶 volume(預設 0,既有呼叫不受影響):

```ts
function c(min: number, close: number, high = close, low = close, vol = 0): IntradayCandle {
  const hh = String(Math.floor(min / 60)).padStart(2, "0");
  const mm = String(min % 60).padStart(2, "0");
  return { date: `2026-06-09T${hh}:${mm}:00.000+08:00`, open: close, high, low, close, volume: vol, average: close };
}
```

在 `describe("computeIndexGeometry", …)` 內(`fmtIndex` 那條 it 之後、`})` 之前)加:

```ts
  it("量 pane:maxVolume / volBarW / scaleVolY 方向正確", () => {
    const candles = [c(540, 45000, 45010, 44990, 5_000_000_000), c(600, 45100, 45110, 45090, 3_000_000_000)];
    const g = computeIndexGeometry({ candles, prevClose: 45000 });
    expect(g.maxVolume).toBe(5_000_000_000);
    expect(g.volBarW).toBeGreaterThan(0);
    // y 軸向下:最大量 bar 頂端(小 y)在 0 量(大 y)之上
    expect(g.scaleVolY(5_000_000_000)).toBeLessThan(g.scaleVolY(0));
  });
  it("空 candles → 量幾何安全值", () => {
    const g = computeIndexGeometry({ candles: [], prevClose: 45000 });
    expect(g.maxVolume).toBe(0);
    expect(g.volBarW).toBe(0);
  });
```

- [ ] **Step 2: 跑測試確認失敗**

Run(在 `frontend/`):`npx vitest run src/lib/index-intraday-svg.test.ts`
Expected: FAIL —「g.maxVolume is undefined」。

- [ ] **Step 3: 實作**

(a) 改 import(line 6-8)補三個常數:

```tsx
import {
  CHART_W, CHART_H, PAD_L, PAD_R, PAD_T, PAD_B, VOL_GAP, VOL_PAD_T, TOTAL_H, INTRADAY_THEME, type ChartTheme,
} from "./intraday-chart-svg";
```

(b) `IndexGeometry` 介面(約 line 25-36)加三欄:

```tsx
  todayHigh: number; todayHighIdx: number;
  todayLow: number; todayLowIdx: number;
  maxVolume: number; scaleVolY: (v: number) => number; volBarW: number;
}
```

(c) 空資料 return(`filteredCandles.length === 0` 區塊,約 line 50-57)補三欄:

```tsx
      todayHigh: 0, todayHighIdx: -1, todayLow: 0, todayLowIdx: -1,
      maxVolume: 0, scaleVolY: () => 0, volBarW: 0,
    };
```

(d) 主計算:在 `polyClose` 算完之後(約 line 73,`const polyClose = …` 那行下面)加:

```tsx
  const maxVolume = Math.max(1, ...filteredCandles.map((cd) => cd.volume));
  const volTop = CHART_H + VOL_GAP + VOL_PAD_T;
  const scaleVolY = (v: number) => volTop + (1 - v / maxVolume) * (TOTAL_H - volTop);
  const volBarW = Math.max(1, (xRange / TRADING_MINUTES) * 0.7);
```

(e) 主 return(約 line 82-85)補三欄:

```tsx
  return {
    yMin, yMax, scaleX, scaleY, padL, padR, padT, padB, fontScale: scale,
    polyClose, minutesByIdx, filteredCandles, todayHigh, todayHighIdx, todayLow, todayLowIdx,
    maxVolume, scaleVolY, volBarW,
  };
```

- [ ] **Step 4: 跑測試確認通過**

Run(在 `frontend/`):`npx vitest run src/lib/index-intraday-svg.test.ts`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/index-intraday-svg.tsx frontend/src/lib/index-intraday-svg.test.ts
git commit -m "feat(index): computeIndexGeometry 加量 pane 幾何(maxVolume/scaleVolY/volBarW)"
```

---

## Task 3: `IndexIntradayStatic` 畫量能副圖

**Files:**
- Modify: `frontend/src/lib/index-intraday-svg.tsx`
- Test: `frontend/src/lib/index-intraday-svg.test.ts`(更新既有測試)

- [ ] **Step 1: 更新既有測試(意圖反轉:現在有量)**

把 test 檔中這條(約 line 39-48):

```ts
  it("渲染主價線 + 昨收基準線,且不含量/VWAP", () => {
    const candles = [c(540, 45000, 45010, 44990), c(600, 45200, 45210, 45190)];
    const input = { candles, prevClose: 45000 };
    const svg = renderToStaticMarkup(
      createElement(IndexIntradayStatic, { ...input, geometry: computeIndexGeometry(input) }),
    );
    expect(svg).toContain("polyline");
    expect(svg).toContain("昨收");
    expect(svg).not.toContain("Vol");
  });
```

整條改為:

```ts
  it("渲染主價線 + 昨收基準線 + 量副圖(成交值)", () => {
    const candles = [c(540, 45000, 45010, 44990, 5_000_000_000), c(600, 45200, 45210, 45190, 3_000_000_000)];
    const input = { candles, prevClose: 45000 };
    const svg = renderToStaticMarkup(
      createElement(IndexIntradayStatic, { ...input, geometry: computeIndexGeometry(input) }),
    );
    expect(svg).toContain("polyline");        // 主價線
    expect(svg).toContain("昨收");             // 昨收基準線
    expect(svg).toContain("成交值(億)");       // 量副圖標籤(指數量是成交值)
    expect(svg).not.toContain("Vol");          // 不沿用個股的 "Vol" 英文標(單位不同)
  });
```

- [ ] **Step 2: 跑測試確認失敗**

Run(在 `frontend/`):`npx vitest run src/lib/index-intraday-svg.test.ts`
Expected: FAIL —「expected svg to contain "成交值(億)"」。

- [ ] **Step 3: 實作量 pane**

(a) `IndexIntradayStatic` 的 geometry 解構(約 line 105-108)補三欄:

```tsx
  const {
    scaleX, scaleY, polyClose, minutesByIdx, filteredCandles,
    todayHigh, todayHighIdx, todayLow, todayLowIdx, padL, padR, padT, padB, fontScale,
    maxVolume, scaleVolY, volBarW,
  } = props.geometry;
```

(b) 在 return 的 Fragment 內、「// 5. X 軸時間 label」那個 `...[…].map(…)` 區塊**之前**,插入量 pane group:

```tsx
    // 量能副圖:每分鐘成交值 bar(指數的 volume = 成交值元);顏色比照個股 close vs open
    filteredCandles.length > 0 && createElement("g", null,
      createElement("line", {
        x1: padL, y1: CHART_H + VOL_GAP / 2, x2: CHART_W - padR, y2: CHART_H + VOL_GAP / 2,
        stroke: t.line, strokeWidth: sw(0.5), opacity: "0.6",
      }),
      createElement("text", {
        x: padL, y: CHART_H + VOL_GAP + VOL_PAD_T + 8, textAnchor: "start",
        fill: t.inkDim, fontSize: fs(13), fontFamily: t.fontFamily,
      }, "成交值(億)"),
      createElement("text", {
        x: CHART_W - padR - 2, y: CHART_H + VOL_GAP + VOL_PAD_T + 8, textAnchor: "end",
        fill: t.inkDim, fontSize: fs(13), fontFamily: t.fontFamily,
      }, fmtIndexVol(maxVolume)),
      ...filteredCandles.map((cd, i) => {
        const x = scaleX(minutesByIdx[i]) - volBarW / 2;
        const y = scaleVolY(cd.volume);
        const fill = cd.close > cd.open ? t.bull : cd.close < cd.open ? t.bear : t.inkDim;
        return createElement("rect", {
          key: i, x, y, width: volBarW, height: Math.max(0, TOTAL_H - y), fill, fillOpacity: "0.7",
        });
      }),
    ),
```

(`fmtIndexVol`、`CHART_W`/`CHART_H`/`VOL_GAP`/`VOL_PAD_T`/`TOTAL_H` 皆同模組已可用,無需額外 import。)

- [ ] **Step 4: 跑測試確認通過**

Run(在 `frontend/`):`npx vitest run src/lib/index-intraday-svg.test.ts`
Expected: PASS(全檔)。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/index-intraday-svg.tsx frontend/src/lib/index-intraday-svg.test.ts
git commit -m "feat(index): IndexIntradayStatic 畫成交值量能副圖"
```

---

## Task 4: 網頁 `IndexIntradayChart` — 加高 viewBox + 振幅/成交值

**Files:**
- Modify: `frontend/src/components/IndexIntradayChart.tsx`

> 無自動測試:本專案前端無 component/hook 測試環境,邏輯已在 Task 1 純函式測試覆蓋(`indexAmplitude`/`fmtIndexVol`)。本 task 僅 UI 接線,以手動執行驗證(Step 4)。

- [ ] **Step 1: 改 import**

`import { CHART_W, CHART_H } from "../lib/intraday-chart-svg";` 改為:

```tsx
import { CHART_W, CHART_H, TOTAL_H } from "../lib/intraday-chart-svg";
import { IndexIntradayStatic, computeIndexGeometry, fmtIndex, fmtIndexVol, indexAmplitude } from "../lib/index-intraday-svg";
```

(原本第 4 行 `import { IndexIntradayStatic, computeIndexGeometry, fmtIndex } …` 整行用上面那行取代。)

- [ ] **Step 2: 算振幅與成交值**

在 `const changePct = …`(約 line 16)那段後面加:

```tsx
  const amp = latest ? indexAmplitude(geometry.todayHigh, geometry.todayLow, prevClose) : null;
  const totalVal = filteredCandles.reduce((n, cd) => n + cd.volume, 0);
```

- [ ] **Step 3: header 加一行 + 加高 viewBox**

(a) 在現有漲跌幅 `{latest && ( … )}` 區塊(約 line 45-49)**之後**、`</div>` 收掉 header 之前,加:

```tsx
        {latest && (
          <div className="text-[13px] text-ink-muted tabular-nums mt-1">
            振幅 {amp != null ? `${amp.toFixed(2)}%` : "—"}　成交值 {fmtIndexVol(totalVal)}
          </div>
        )}
```

(b) svg 的 viewBox(約 line 56)`viewBox={`0 0 ${CHART_W} ${CHART_H}`}` 改:

```tsx
          viewBox={`0 0 ${CHART_W} ${TOTAL_H}`}
```

(`CHART_H` 仍用於 hover 十字線,保留 import。)

- [ ] **Step 4: 手動驗證**

Run(在 `frontend/`):`npm run dev`,瀏覽 `http://localhost:5173` → 左側「大盤指數」→「左右並排」。
Expected:加權、櫃買各圖標題下多一行「振幅 X.XX%　成交值 N,NNN億」;每張圖下方有成交值量條(紅綠),X 軸時間在量條下方未被截。盤前/無資料時振幅顯「—」。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/IndexIntradayChart.tsx
git commit -m "feat(index): 指數單圖加振幅/成交值數字 + 量副圖高度"
```

---

## Task 5: Bot `loadSlowIndex` — 回傳 amplitude + volume

**Files:**
- Modify: `bot/src/reply.ts`
- Test: `bot/src/reply.test.ts`

- [ ] **Step 1: 改測試 deps(量改非零)+ 斷言**

`reply.test.ts` 的 `indexDeps.getCandles` 兩根 candle 的 `volume: 0` 改為非零(約 line 109-112):

```ts
      data: [
        { date: "2026-06-09T09:00:00.000+08:00", open: 45000, high: 45050, low: 44980, close: 45010, volume: 50_000_000_000, average: 0 },
        { date: "2026-06-09T13:30:00.000+08:00", open: 45200, high: 45260, low: 45100, close: 45231, volume: 60_000_000_000, average: 0 },
      ],
```

在「走精簡路徑」測試(約 line 122-130)的 `if (!s.empty && s.isIndex)` 內補斷言:

```ts
    if (!s.empty && s.isIndex) {
      expect(s.isIndex).toBe(true);
      expect(s.lastClose).toBe(45231);
      expect(s.volume).toBe(110_000_000_000);          // 兩分鐘成交值加總
      expect(s.amplitude).toBeCloseTo(0.62, 2);          // (45260−44980)/45000×100
      expect(s.png).not.toBeNull();
    }
```

- [ ] **Step 2: 跑測試確認失敗**

Run(在 `bot/`):`npx vitest run src/reply.test.ts`
Expected: FAIL —「s.volume / s.amplitude 不存在 / undefined」。

- [ ] **Step 3: 實作**

(a) `reply.ts` import 區(約 line 6 之後)加:

```ts
import { indexAmplitude } from "../../frontend/src/lib/index-intraday-svg";
```

(b) `loadSlowIndex` 的非空 return(約 line 83-87)加兩欄:

```ts
  return {
    empty: false as const, isIndex: true as const, name, png,
    lastClose: last.close, change, changePct,
    open: intraday[0].open, high, low, asOf: last.date.slice(11, 16),
    amplitude: indexAmplitude(high, low, candlesR.prev_close),
    volume: intraday.reduce((n, c) => n + c.volume, 0),
  };
```

- [ ] **Step 4: 跑測試確認通過**

Run(在 `bot/`):`npx vitest run src/reply.test.ts`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add bot/src/reply.ts bot/src/reply.test.ts
git commit -m "feat(index/bot): loadSlowIndex 回傳振幅與成交值"
```

---

## Task 6: Bot `buildIndexReply` 欄位 + composeReply 接線

**Files:**
- Modify: `bot/src/embed.ts`、`bot/src/reply.ts`
- Test: `bot/src/embed.test.ts`(新建)

- [ ] **Step 1: 寫失敗測試(新檔)**

建 `bot/src/embed.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import { buildIndexReply } from "./embed";

const base = {
  symbol: "IX0001", name: "加權指數", lastClose: 44704.44, change: 1201.66, changePct: 2.76,
  open: 43687.62, high: 44821.71, low: 43687.62, asOf: "13:30",
};
const fieldsOf = (m: ReturnType<typeof buildIndexReply>) => m.embeds[0].data.fields ?? [];

describe("buildIndexReply 振幅/成交值欄位", () => {
  it("含振幅% 與成交值(億)", () => {
    const f = fieldsOf(buildIndexReply({ ...base, amplitude: 2.61, volume: 1151930775120 }));
    const v = f.find((x) => x.name === "振幅 / 成交值")?.value ?? "";
    expect(v).toContain("2.61%");
    expect(v).toContain("11,519億");
  });
  it("無昨收 → 振幅顯 —", () => {
    const f = fieldsOf(buildIndexReply({ ...base, amplitude: null, volume: 0 }));
    const v = f.find((x) => x.name === "振幅 / 成交值")?.value ?? "";
    expect(v).toContain("—");
    expect(v).toContain("0億");
  });
});
```

- [ ] **Step 2: 跑測試確認失敗**

Run(在 `bot/`):`npx vitest run src/embed.test.ts`
Expected: FAIL —`buildIndexReply` 型別不含 `amplitude`/`volume`,或找不到該欄位。

- [ ] **Step 3: 實作 embed.ts**

(a) import 區(約 line 4)加 `fmtIndexVol`:

```ts
import { fmtIndexVol } from "../../frontend/src/lib/index-intraday-svg";
```

(b) `buildIndexReply` 的 args 型別(約 line 44-47)加兩欄:

```ts
export function buildIndexReply(args: {
  symbol: string; name: string | null; lastClose: number; change: number; changePct: number;
  open: number; high: number; low: number; asOf: string;
  amplitude: number | null; volume: number;
}) {
```

(c) `.addFields(…)`(約 line 58)由單欄改為兩欄:

```ts
    .addFields(
      { name: "開 / 高 / 低", value: `${fmt(args.open)} / ${fmt(args.high)} / ${fmt(args.low)}`, inline: true },
      { name: "振幅 / 成交值", value: `${args.amplitude != null ? args.amplitude.toFixed(2) + "%" : "—"} / ${fmtIndexVol(args.volume)}`, inline: true },
    )
```

- [ ] **Step 4: 接線 composeReply**

`reply.ts` 的 `composeReply` 指數分支(約 line 103-106)的 `buildIndexReply({…})` 加兩個參數:

```ts
    const idxMsgs: BaseMessageOptions[] = [buildIndexReply({
      symbol, name: s.name, lastClose: s.lastClose, change: s.change, changePct: s.changePct,
      open: s.open, high: s.high, low: s.low, asOf: s.asOf,
      amplitude: s.amplitude, volume: s.volume,
    })];
```

- [ ] **Step 5: 跑測試確認通過**

Run(在 `bot/`):`npx vitest run src/embed.test.ts src/reply.test.ts`
Expected: PASS(embed 新測 + reply 既有測試仍綠,證明 composeReply 接線型別相容)。

- [ ] **Step 6: Commit**

```bash
git add bot/src/embed.ts bot/src/embed.test.ts bot/src/reply.ts
git commit -m "feat(index/bot): buildIndexReply 加振幅/成交值欄位"
```

---

## Task 7: Bot 指數 PNG 加高(含量 pane)

**Files:**
- Modify: `bot/src/render.ts`

> 無自動測試:`render.ts` 經 resvg 產 PNG,無測試掛點。SVG 內容已由 Task 3 覆蓋;本 task 僅改畫布高度,以手動產圖驗證。

- [ ] **Step 1: 改 `buildIndexChartSvg` 高度**

`render.ts` 約 line 101:

```ts
  const totalH = CHART_H + TITLE_H; // 指數無量子圖
```

改為:

```ts
  const totalH = TOTAL_H + TITLE_H; // 含成交值量子圖
```

(`TOTAL_H` 已在 line 9 import,無需新增。)

- [ ] **Step 2: 型別/編譯檢查**

Run(在 `bot/`):`npx tsc --noEmit`
Expected: 無錯誤。

- [ ] **Step 3: 手動驗證(實機)**

重啟 bot(或 `npm run start`),在 Discord 輸入 `p加權`。
Expected:回傳的走勢圖 PNG **下方含成交值量條**,未被裁切;標題帶現價/漲跌正常。

- [ ] **Step 4: Commit**

```bash
git add bot/src/render.ts
git commit -m "feat(index/bot): 指數走勢圖 PNG 含成交值量子圖(加高畫布)"
```

---

## 最終驗證

- [ ] 前端全測:Run(在 `frontend/`)`npm test` → 全綠。
- [ ] bot 全測:Run(在 `bot/`)`npm test` → 全綠。
- [ ] 網頁手動:`大盤指數`→`左右並排`,兩圖皆有振幅/成交值行 + 量條;切到`重疊 %`確認**未**被改動(無量條)。
- [ ] bot 手動:`p加權`/`p櫃買` 文字含「振幅 / 成交值」欄、圖含量條。
- [ ] `git status` 確認沒誤動 `intraday-chart-svg.tsx` 或其 snapshot。

---

## Self-Review(對 spec 逐項核對)

- **振幅(數字)**:Task 1(純函式)+ Task 4(網頁)+ Task 5/6(bot)。✓
- **成交值(數字)**:Task 1(`fmtIndexVol`)+ Task 4 + Task 6。✓
- **量能副圖**:Task 2(幾何)+ Task 3(渲染)+ Task 4(網頁加高)+ Task 7(bot PNG 加高)。✓
- **單位億元 / 標籤「成交值(億)」**:Task 1 + Task 3。✓
- **bar 顏色 close vs open**:Task 3。✓
- **零後端改動**:無任一 task 觸及 `backend/`。✓
- **重疊%不加量、不做委買賣力道**:範圍排除,無對應 task。✓
- **邊界(無昨收→振幅—、空盤→量空)**:Task 1(null)、Task 2(空安全值)、Task 4(`latest` guard)。✓
- **型別一致**:`indexAmplitude(high, low, prevClose|null)→number|null`、`fmtIndexVol(number)→string`、`loadSlowIndex` 回傳含 `amplitude:number|null; volume:number`、`buildIndexReply` args 同名同型,全鏈一致。✓
