# Discord Bot 五檔改圖 + CDP 全標米字號 + 圖片 feed 放大可讀 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓 `p代號` 查詢 bot 的回覆把五檔從文字改成獨立視覺化 PNG、CDP 5 條全標 `*`、並讓兩張圖在 Discord feed 免點開可讀。

**Architecture:** 五檔圖走跟分時圖**同款管線** —— `frontend/src/lib/` 放 inline-hex SVG 共用元件 → bot 端 `react-dom/server` + `resvg` 產 PNG。五檔圖**跟即時 quote 同產、不走 30s 快取**。CDP label 與分時圖字級調整在共用層(網頁分時圖會同步受影響,已接受)。

**Tech Stack:** TypeScript、React(`createElement` / `react-dom/server`)、`@resvg/resvg-js`、Vitest、discord.js。

**設計來源:** `docs/superpowers/specs/2026-06-05-discord-quote-image-and-cdp-asterisk-design.md`

---

## File Structure

**新建:**
- `frontend/src/lib/quote-book-svg.tsx` — 五檔視覺化 SVG 共用元件(inline-hex,landscape 大字)
- `frontend/src/lib/quote-book-svg.test.ts` — 元件單元測試

**修改:**
- `frontend/src/lib/intraday-chart-svg.tsx` — CDP 5 條全標 `*`(~L185)+ 字級加大 / 格線降密度(§3.3）
- `frontend/src/lib/intraday-chart-svg.test.ts` — CDP 全標斷言 + snapshot 更新
- `frontend/src/lib/__snapshots__/intraday-chart-svg.test.ts.snap` — `-u` 重生
- `bot/src/embed.ts` — CDP 文字全標(~L42)、移除文字五檔 + 委買/委賣欄、`buildReply` 收 `quotePng`、移除 `formatLadder`/`sumSize`
- `bot/src/embed.test.ts` — CDP 文字斷言、移除 ladder 測試、`buildReply` 兩張圖
- `bot/src/render.ts` — 抽 `svgToPng` helper + 新增 `renderQuotePng`
- `bot/src/render.test.ts` — `renderQuotePng` smoke test
- `bot/src/reply.ts` — `composeReply` 加 `quotePng` 參數
- `bot/src/reply.test.ts` — `composeReply` 簽名 + 斷言更新
- `bot/src/index.ts` — `handle()` 抓 quote 後產 `quotePng`、傳給 `composeReply`

**測試指令:** 前端在 `frontend/` 跑 `npx vitest run <file>`;bot 在 `bot/` 跑 `npx vitest run <file>`。

---

## Task 1: CDP 5 條全標 `*`(分時圖)

**Files:**
- Modify: `frontend/src/lib/intraday-chart-svg.tsx:183-188`
- Test: `frontend/src/lib/intraday-chart-svg.test.ts:37-45`

- [ ] **Step 1: 改既有測試 —— 5 條 CDP label 全帶 `*`**

把 `intraday-chart-svg.test.ts` 第 37–45 行那條 `it("CDP 樞紐 label 帶 *...")` 整段換成:

```ts
  it("CDP 5 條 label 全帶 *(不再只標中樞)", () => {
    const candles = [candle(540, 100)];
    const cdp = { ah: 108, nh: 104, cdp: 101, nl: 98, al: 95, as_of_date: "2026-06-04" };
    const g = computeIntradayGeometry({ candles, prevClose: 100, cdp, camarilla: null, ma: null, flags: FLAGS });
    const cdpLabels = g.resolvedLabels.filter((l) => l.color === "#e85a4f"); // theme.accent
    expect(cdpLabels).toHaveLength(5);                          // 5 條都在 ±10% 內
    expect(cdpLabels.every((l) => l.text.endsWith("*"))).toBe(true);  // 全帶 *
  });
```

- [ ] **Step 2: 跑測試,確認失敗**

Run: `npx vitest run src/lib/intraday-chart-svg.test.ts -t "CDP 5 條"`(在 `frontend/`)
Expected: FAIL —— 目前只有中樞帶 `*`,`every(...endsWith("*"))` 為 false。

- [ ] **Step 3: 改實作 —— 移除 `k === "cdp"` 特例**

`intraday-chart-svg.tsx` 第 183-186 行(`for (const k of visibleCdpKeys)` 內的 `labelInputs.push`):

```diff
       labelInputs.push({
         originalY: scaleY(cdp[k]),
-        // 中央 CDP 樞紐價標 *,在 5 條同色 CDP 線裡標出真正的樞紐
-        text: k === "cdp" ? `${formatTickPrice(cdp[k])}*` : formatTickPrice(cdp[k]),
+        // 5 條 CDP label 全標 *(一眼分出哪些是 CDP 線,不再只標中樞)
+        text: `${formatTickPrice(cdp[k])}*`,
         color: theme.accent,
       });
```

- [ ] **Step 4: 跑測試,確認通過**

Run: `npx vitest run src/lib/intraday-chart-svg.test.ts -t "CDP 5 條"`(在 `frontend/`)
Expected: PASS

- [ ] **Step 5: 更新 snapshot(SVG 內 label 文字變了)**

Run: `npx vitest run src/lib/intraday-chart-svg.test.ts -u`(在 `frontend/`)
Expected: 全綠,snapshot 檔更新。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/intraday-chart-svg.tsx frontend/src/lib/intraday-chart-svg.test.ts frontend/src/lib/__snapshots__/intraday-chart-svg.test.ts.snap
git commit -m "feat(chart): CDP 5 條 label 全標 *(不再只標中樞)"
```

---

## Task 2: CDP 5 條全標 `*`(embed 文字)

**Files:**
- Modify: `bot/src/embed.ts:41-43`
- Test: `bot/src/embed.test.ts:57-58`

- [ ] **Step 1: 改既有測試 —— CDP 欄位每條都帶 `*`**

`embed.test.ts` 第 57-58 行原本只斷言 `cdpField?.value` 含 `"CDP*"`。改成同時驗 5 條:

```ts
    const cdpField = (embed.data.fields ?? []).find((f) => f.name === "CDP");
    expect(cdpField?.value).toContain("AH*");   // 5 條全標 *(功能 2)
    expect(cdpField?.value).toContain("CDP*");
    expect(cdpField?.value).toContain("AL*");
```

- [ ] **Step 2: 跑測試,確認失敗**

Run: `npx vitest run src/embed.test.ts -t "產圖失敗"`(在 `bot/`)
Expected: FAIL —— 目前只有 `CDP*`,沒有 `AH*` / `AL*`。

- [ ] **Step 3: 改實作 —— embed CDP 文字全標**

`embed.ts` 第 42 行:

```diff
   const cdp = args.cdp
-    ? `AH ${args.cdp.ah} ／ NH ${args.cdp.nh} ／ CDP* ${args.cdp.cdp} ／ NL ${args.cdp.nl} ／ AL ${args.cdp.al}`
+    ? `AH* ${args.cdp.ah} ／ NH* ${args.cdp.nh} ／ CDP* ${args.cdp.cdp} ／ NL* ${args.cdp.nl} ／ AL* ${args.cdp.al}`
     : "—";
```

- [ ] **Step 4: 跑測試,確認通過**

Run: `npx vitest run src/embed.test.ts -t "產圖失敗"`(在 `bot/`)
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bot/src/embed.ts bot/src/embed.test.ts
git commit -m "feat(bot): embed CDP 文字 5 條全標 *"
```

---

## Task 3: 分時圖加大字級 + 降格線密度(feed 可讀)

**Files:**
- Modify: `frontend/src/lib/intraday-chart-svg.tsx`(多處 `fontSize` + 格線 pct + 碰撞間距)
- Snapshot: `frontend/src/lib/__snapshots__/intraday-chart-svg.test.ts.snap`

> 字級值是 feed ~450px 可讀的起點;Task 9 視覺驗證後可微調(預期迭代,非 placeholder)。**不動** `CHART_W`/`CHART_H`(避免大規模 geometry 改動,820×580 已是 landscape)。

- [ ] **Step 1: 降 Y 軸格線密度 —— ±2% 改 ±5%**

`intraday-chart-svg.tsx` 第 293 行(`linePrices` 的 pct 陣列):

```diff
-        [-10, -8, -6, -4, -2, 0, 2, 4, 6, 8, 10].map((pct) =>
+        [-10, -5, 0, 5, 10].map((pct) =>
```

- [ ] **Step 2: 加大 label 碰撞最小間距(配合字級)**

第 217-221 行 `resolveCollisions(labelInputs, 16, ...)`:

```diff
   const resolvedLabels = resolveCollisions(
     labelInputs,
-    16,
+    20,
     [PAD_T, CHART_H - PAD_B],
   );
```

- [ ] **Step 3: 加大各處字級**

逐處把分時圖內的字級調大(`fontSize: 12 → 15`、`11 → 13`)。改這 6 處:

```diff
# 第 308-313 行 Y 軸 price label
       createElement("text", {
         x: PAD_L - 4, y: y + 3, textAnchor: "end",
         fill: isBaseline ? t.ink : t.inkDim,
-        fontSize: 12, fontFamily: t.fontFamily,
+        fontSize: 15, fontFamily: t.fontFamily,
```
```diff
# 第 376-380 行 右側 margin label(resolvedLabels)
       createElement("text", {
         x: CHART_W - PAD_R + 6, y: lbl.y + 3, textAnchor: "start",
-        fill: lbl.color, fontSize: 12, fontFamily: t.fontFamily,
+        fill: lbl.color, fontSize: 15, fontFamily: t.fontFamily,
```
```diff
# 第 409-415 行 今日高 label
       createElement("text", {
         x: scaleX(minutesByIdx[todayHighIdx]),
         y: scaleY(todayHigh) - 6,
         textAnchor: "middle",
         fill: priceColor(todayHigh, baseline, t),
-        fontSize: 12, fontFamily: t.fontFamily,
+        fontSize: 15, fontFamily: t.fontFamily,
```
```diff
# 第 423-429 行 今日低 label
       createElement("text", {
         x: scaleX(minutesByIdx[todayLowIdx]),
         y: scaleY(todayLow) + 13,
         textAnchor: "middle",
         fill: priceColor(todayLow, baseline, t),
-        fontSize: 12, fontFamily: t.fontFamily,
+        fontSize: 15, fontFamily: t.fontFamily,
```
```diff
# 第 443-447 行 成交量最大值 label
       createElement("text", {
         x: CHART_W - PAD_R - 2, y: CHART_H + VOL_GAP + VOL_PAD_T + 8,
-        textAnchor: "end", fill: t.inkDim, fontSize: 11, fontFamily: t.fontFamily,
+        textAnchor: "end", fill: t.inkDim, fontSize: 13, fontFamily: t.fontFamily,
```
```diff
# 第 449-453 行 "Vol" label
       createElement("text", {
         x: PAD_L - 4, y: CHART_H + VOL_GAP + VOL_PAD_T + 8,
-        textAnchor: "end", fill: t.inkDim, fontSize: 11, fontFamily: t.fontFamily,
+        textAnchor: "end", fill: t.inkDim, fontSize: 13, fontFamily: t.fontFamily,
```
```diff
# 第 476-480 行 X 軸時間 label
     ].map(({ min, label }) => createElement("text", {
       key: min, x: scaleX(min), y: CHART_H - 8, textAnchor: "middle",
-      fill: t.inkDim, fontSize: 12, fontFamily: t.fontFamily,
+      fill: t.inkDim, fontSize: 15, fontFamily: t.fontFamily,
```

- [ ] **Step 4: 更新 snapshot**

Run: `npx vitest run src/lib/intraday-chart-svg.test.ts -u`(在 `frontend/`)
Expected: 全綠。既有「tabular-nums / font-weight:500 / uppercase」斷言**仍須通過**(只改字級數值,沒動這些 style)。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/intraday-chart-svg.tsx frontend/src/lib/__snapshots__/intraday-chart-svg.test.ts.snap
git commit -m "feat(chart): 分時圖加大字級 + 降格線密度(Discord feed 可讀)"
```

---

## Task 4: 五檔視覺化 SVG 元件

**Files:**
- Create: `frontend/src/lib/quote-book-svg.tsx`
- Test: `frontend/src/lib/quote-book-svg.test.ts`

- [ ] **Step 1: 寫失敗測試**

建立 `frontend/src/lib/quote-book-svg.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { QuoteBookSvg } from "./quote-book-svg";
import { formatTickPrice } from "./tick";

const render = (input: Parameters<typeof QuoteBookSvg>[0]) =>
  renderToStaticMarkup(createElement(QuoteBookSvg, input));

const base = { isLimitUp: false, isLimitDown: false };

describe("QuoteBookSvg", () => {
  it("含委買/委賣總量(加總)與五檔價量", () => {
    const svg = render({
      ...base,
      bids: [{ price: 384, size: 5 }, { price: 383.5, size: 1 }],
      asks: [{ price: 385, size: 13 }, { price: 386, size: 1 }],
    });
    expect(svg).toContain("6 張");                 // 委買總量 5+1
    expect(svg).toContain("14 張");                // 委賣總量 13+1
    expect(svg).toContain(formatTickPrice(384));   // 買1 價(="384.0")
    expect(svg).toContain(formatTickPrice(385));   // 賣1 價(="385.0")
  });

  it("price=0 顯示「市價」、缺檔補「—」", () => {
    const svg = render({
      ...base,
      bids: [{ price: 0, size: 0 }],               // 鎖停的市價單
      asks: [{ price: 385, size: 13 }],
    });
    expect(svg).toContain("市價");                  // price=0
    expect(svg).toContain("—");                     // 買2..買5、賣2..賣5 缺檔
  });

  it("鎖漲停 → 顯示 badge", () => {
    const svg = render({
      ...base, isLimitUp: true,
      bids: [{ price: 384, size: 5 }], asks: [{ price: 385, size: 13 }],
    });
    expect(svg).toContain("鎖漲停");
  });
});
```

- [ ] **Step 2: 跑測試,確認失敗**

Run: `npx vitest run src/lib/quote-book-svg.test.ts`(在 `frontend/`)
Expected: FAIL —— `quote-book-svg` 模組不存在。

- [ ] **Step 3: 寫元件**

建立 `frontend/src/lib/quote-book-svg.tsx`:

```tsx
// 委買賣五檔的共用畫圖層 — bot 產圖用(網頁 QuoteBook.tsx 維持 Tailwind 版不動)。
// 顏色一律 inline hex(resvg 不解析 Tailwind / CSS var);landscape 大字,
// 讓 Discord feed 把圖縮到欄寬後仍免點開可讀。
import { createElement, Fragment } from "react";
import { formatTickPrice } from "./tick";
import { INTRADAY_THEME, type ChartTheme } from "./intraday-chart-svg";

export const QUOTE_W = 720;
export const QUOTE_H = 300;

export interface QuoteBookLevel { price: number; size: number; }
export interface QuoteBookSvgInput {
  bids: QuoteBookLevel[];
  asks: QuoteBookLevel[];
  isLimitUp: boolean;
  isLimitDown: boolean;
  theme?: ChartTheme;
}

const PAD = 24;
const MID_X = QUOTE_W / 2;          // 360 — 買賣分隔中線
const COL_GAP = 12;                 // 中線兩側留白
const LEFT_R = MID_X - COL_GAP;     // 348 左欄右界(價靠中)
const RIGHT_L = MID_X + COL_GAP;    // 372 右欄左界
const COL_W = LEFT_R - PAD;         // 324 單欄寬(量條 normalize 用)
const ROWS_TOP = 104;
const ROW_H = 36;
const BAR_H = 24;

export function QuoteBookSvg(input: QuoteBookSvgInput) {
  const t = input.theme ?? INTRADAY_THEME;
  const bids = input.bids.slice(0, 5);
  const asks = input.asks.slice(0, 5);
  const maxQty = Math.max(1, ...bids.map((b) => b.size), ...asks.map((a) => a.size));
  const bidTotal = bids.reduce((s, b) => s + b.size, 0);
  const askTotal = asks.reduce((s, a) => s + a.size, 0);

  // price=0 是鎖漲跌停的市價單;缺檔(undefined)補 —
  const priceCell = (lv: QuoteBookLevel | undefined) =>
    lv ? (lv.price === 0 ? "市價" : formatTickPrice(lv.price)) : "—";
  const qtyCell = (lv: QuoteBookLevel | undefined) =>
    lv && lv.size > 0 ? String(lv.size) : "—";

  const num = { fontVariantNumeric: "tabular-nums" as const };

  return createElement("svg", {
      xmlns: "http://www.w3.org/2000/svg",
      viewBox: `0 0 ${QUOTE_W} ${QUOTE_H}`, width: QUOTE_W, height: QUOTE_H,
    },
    createElement("rect", { x: 0, y: 0, width: QUOTE_W, height: QUOTE_H, fill: t.bg }),

    // ── 抬頭 + 鎖漲跌停 badge ──
    createElement("text", {
      x: PAD, y: 34, fontSize: 22, fontFamily: t.fontFamily, fill: t.ink,
      style: { fontWeight: 700 },
    }, "委買賣 五檔"),
    input.isLimitUp && createElement("text", {
      x: QUOTE_W - PAD, y: 34, fontSize: 18, textAnchor: "end",
      fontFamily: t.fontFamily, fill: t.bull,
    }, "🔺 鎖漲停"),
    input.isLimitDown && createElement("text", {
      x: QUOTE_W - PAD, y: 34, fontSize: 18, textAnchor: "end",
      fontFamily: t.fontFamily, fill: t.bear,
    }, "🔻 鎖跌停"),
    createElement("line", { x1: PAD, y1: 46, x2: QUOTE_W - PAD, y2: 46, stroke: t.line, strokeWidth: 1 }),

    // ── 委買總量(左紅)/ 委賣總量(右綠)大字 ──
    createElement("text", {
      x: PAD, y: 84, fontSize: 28, fontFamily: t.fontFamily, fill: t.bull,
      style: { ...num, fontWeight: 700 },
    }, `${bidTotal} 張`),
    createElement("text", {
      x: QUOTE_W - PAD, y: 84, fontSize: 28, textAnchor: "end", fontFamily: t.fontFamily, fill: t.bear,
      style: { ...num, fontWeight: 700 },
    }, `${askTotal} 張`),

    // ── 五檔列 × 5(左買右賣,最佳價在最上,量條由中線往外) ──
    ...Array.from({ length: 5 }).map((_, i) => {
      const bid = bids[i];
      const ask = asks[i];
      const y = ROWS_TOP + i * ROW_H;
      const barY = y + (ROW_H - BAR_H) / 2;
      const textY = y + ROW_H / 2 + 6;
      const bidW = bid && bid.size > 0 ? (bid.size / maxQty) * COL_W : 0;
      const askW = ask && ask.size > 0 ? (ask.size / maxQty) * COL_W : 0;
      return createElement(Fragment, { key: i },
        // 買量條:靠中線(LEFT_R)往左長
        bidW > 0 && createElement("rect", {
          x: LEFT_R - bidW, y: barY, width: bidW, height: BAR_H,
          fill: t.bull, fillOpacity: 0.15,
        }),
        // 買量(左)/ 買價(靠中,紅)
        createElement("text", { x: PAD, y: textY, fontSize: 18, fill: t.inkMuted, fontFamily: t.fontFamily, style: num }, qtyCell(bid)),
        createElement("text", { x: LEFT_R, y: textY, fontSize: 18, textAnchor: "end", fill: t.bull, fontFamily: t.fontFamily, style: { ...num, fontWeight: 500 } }, priceCell(bid)),
        // 賣量條:靠中線(RIGHT_L)往右長
        askW > 0 && createElement("rect", {
          x: RIGHT_L, y: barY, width: askW, height: BAR_H,
          fill: t.bear, fillOpacity: 0.15,
        }),
        // 賣價(靠中,綠)/ 賣量(右)
        createElement("text", { x: RIGHT_L, y: textY, fontSize: 18, fill: t.bear, fontFamily: t.fontFamily, style: { ...num, fontWeight: 500 } }, priceCell(ask)),
        createElement("text", { x: QUOTE_W - PAD, y: textY, fontSize: 18, textAnchor: "end", fill: t.inkMuted, fontFamily: t.fontFamily, style: num }, qtyCell(ask)),
      );
    }),
  );
}
```

- [ ] **Step 4: 跑測試,確認通過**

Run: `npx vitest run src/lib/quote-book-svg.test.ts`(在 `frontend/`)
Expected: PASS（3 條）

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/quote-book-svg.tsx frontend/src/lib/quote-book-svg.test.ts
git commit -m "feat(chart): 五檔視覺化 SVG 共用元件(inline-hex,landscape 大字)"
```

---

## Task 5: bot 端產五檔 PNG(`renderQuotePng`)

**Files:**
- Modify: `bot/src/render.ts`
- Test: `bot/src/render.test.ts`

- [ ] **Step 1: 寫失敗測試**

在 `bot/src/render.test.ts` 末尾(第 76 行後)加:

```ts
import { renderQuotePng } from "./render";
import type { QuoteResp } from "./data";

describe("renderQuotePng — 五檔圖 pipeline smoke test", () => {
  it("回傳有效 PNG(magic bytes + size > 1000)", () => {
    const quote: QuoteResp = {
      bids: [{ price: 384, size: 5 }, { price: 383.5, size: 1 }],
      asks: [{ price: 385, size: 13 }, { price: 386, size: 1 }],
      is_limit_up_bid: false, is_limit_up_ask: false,
      is_limit_down_bid: false, is_limit_down_ask: false,
    };
    const png = renderQuotePng(quote);
    expect(png).toBeInstanceOf(Buffer);
    expect(png.length).toBeGreaterThan(1000);
    const MAGIC = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
    expect(png.subarray(0, 8)).toEqual(MAGIC);
  });
});
```

- [ ] **Step 2: 跑測試,確認失敗**

Run: `npx vitest run src/render.test.ts -t "renderQuotePng"`(在 `bot/`)
Expected: FAIL —— `renderQuotePng` 尚未匯出。

- [ ] **Step 3: 重構 `render.ts` 抽 `svgToPng` + 新增 `renderQuotePng`**

`render.ts`:在 import 區加入 `QuoteBookSvg`、`QUOTE_W`、`QUOTE_H` 與 `QuoteResp`:

```diff
 import {
   IntradayChartStatic, computeIntradayGeometry, INTRADAY_THEME,
   CHART_W, CHART_H, TOTAL_H, type IntradayChartInput,
 } from "../../frontend/src/lib/intraday-chart-svg";
+import { QuoteBookSvg, QUOTE_W, QUOTE_H } from "../../frontend/src/lib/quote-book-svg";
+import type { QuoteResp } from "./data";
```

把 `renderChartPng` 內「`new Resvg(svg, ...)` → `Buffer.from(...)`」那段抽成共用 helper。在 `renderChartPng` 函式**之後**新增:

```ts
// SVG 字串 → PNG buffer 的共用 resvg 管線(分時圖 / 五檔圖共用)。
// 2x zoom = retina 解析度(點開清楚);feed 顯示尺寸由 Discord 控,與此無關。
function svgToPng(svg: string): Buffer {
  const resvg = new Resvg(svg, {
    fitTo: { mode: "zoom", value: 2 },
    font: {
      loadSystemFonts: true,
      defaultFontFamily: FONT_FAMILY,
      ...(EXTRA_FONTS.length > 0 ? { fontFiles: EXTRA_FONTS } : {}),
    },
  });
  return Buffer.from(resvg.render().asPng());
}

// 委買賣五檔獨立 PNG。跟即時 quote 同產、不走 loadSlow 30s 快取(spec §3.1.3)。
export function renderQuotePng(quote: QuoteResp): Buffer {
  const svg = renderToStaticMarkup(
    createElement(QuoteBookSvg, {
      bids: quote.bids,
      asks: quote.asks,
      isLimitUp: quote.is_limit_up_bid || quote.is_limit_up_ask,
      isLimitDown: quote.is_limit_down_bid || quote.is_limit_down_ask,
      theme: THEME,
    }),
  );
  return svgToPng(svg);
}
```

然後把 `renderChartPng` 結尾的 resvg 段改用 helper:

```diff
-  const resvg = new Resvg(svg, {
-    fitTo: { mode: "zoom", value: 2 },
-    font: {
-      loadSystemFonts: true,
-      defaultFontFamily: FONT_FAMILY,
-      ...(EXTRA_FONTS.length > 0 ? { fontFiles: EXTRA_FONTS } : {}),
-    },
-  });
-  return Buffer.from(resvg.render().asPng());
+  return svgToPng(svg);
```

> `QUOTE_W` / `QUOTE_H` import 後雖未在 render.ts 直接使用(viewBox 在元件內),保留供日後尺寸引用;若 lint 報未使用就只 import `QuoteBookSvg`。

- [ ] **Step 4: 跑測試,確認通過(含既有 `renderChartPng` smoke 仍綠)**

Run: `npx vitest run src/render.test.ts`(在 `bot/`)
Expected: PASS（renderChartPng + safeRender + renderQuotePng 全綠)

- [ ] **Step 5: Commit**

```bash
git add bot/src/render.ts bot/src/render.test.ts
git commit -m "feat(bot): 抽 svgToPng helper + renderQuotePng 產五檔圖"
```

---

## Task 6: `buildReply` 收五檔圖、移除文字五檔

**Files:**
- Modify: `bot/src/embed.ts`
- Test: `bot/src/embed.test.ts`

- [ ] **Step 1: 確認 `formatLadder` / `sumSize` 無其他 caller**

Run(repo 根):`git grep -n "formatLadder\|sumSize"`
Expected: 只出現在 `bot/src/embed.ts` 與 `bot/src/embed.test.ts`。若有別處,停下回報。

- [ ] **Step 2: 改測試 —— 移除 ladder 測試、buildReply 改驗兩張圖/不含五檔文字**

`embed.test.ts`:
1. 刪掉第 8-28 行整個 `describe("formatLadder ...")`(含 `sumSize` 子測試)。
2. import 改成只留 `buildReply`:`import { buildReply } from "./embed";`
3. `baseArgs`(第 39-44 行)加上 `quotePng: Buffer.from([0x89, 0x51])`:

```ts
const baseArgs = {
  symbol: "2330", name: "台積電",
  lastClose: 600, change: 12, changePct: 2.04,
  open: 590, high: 601, low: 588, vwap: 595, volume: 12000,
  cdp: CDP_F, ma: MA_F, quote: QUOTE_F, quotePng: Buffer.from([0x89, 0x51]) as Buffer | null, asOf: "13:30",
};
```

4. 把第 46-70 行的 `describe("buildReply ...")` 兩條測試改成:

```ts
describe("buildReply — 五檔改圖 + 降級(spec §8 不讓整則炸)", () => {
  it("分時圖 + 五檔圖都在 → files 兩張、description 不再含文字五檔", () => {
    const r = buildReply({ ...baseArgs, png: Buffer.from([0x89, 0x50]) });
    expect(r.files).toHaveLength(2);                     // chart.png + quote.png
    const embed = r.embeds[0];
    expect(embed.data.description).toContain("600.00");  // 現價還在
    expect(embed.data.description).not.toContain("買盤"); // 文字五檔已移除
    const cdpField = (embed.data.fields ?? []).find((f) => f.name === "CDP");
    expect(cdpField?.value).toContain("AH*");            // CDP 全標(功能 2)
    const fieldNames = (embed.data.fields ?? []).map((f) => f.name);
    expect(fieldNames).not.toContain("委買 / 委賣(張)");  // 重複總量欄已移除
  });

  it("分時圖失敗(png=null)→ 只附五檔圖、embed 文字欄仍在", () => {
    const r = buildReply({ ...baseArgs, png: null });
    expect(r.files).toHaveLength(1);                     // 只剩 quote.png
    const embed = r.embeds[0];
    expect(embed.data.image).toBeUndefined();            // 沒有 setImage
    expect(embed.data.description).toContain("600.00");
  });

  it("五檔圖失敗(quotePng=null)→ 只附分時圖", () => {
    const r = buildReply({ ...baseArgs, png: Buffer.from([0x89, 0x50]), quotePng: null });
    expect(r.files).toHaveLength(1);                     // 只剩 chart.png
    expect(r.embeds[0].data.image?.url).toBe("attachment://chart.png");
  });

  it("兩張都失敗 → files 空、純文字 embed", () => {
    const r = buildReply({ ...baseArgs, png: null, quotePng: null });
    expect(r.files).toHaveLength(0);
    expect(r.embeds[0].data.description).toContain("600.00");
  });
});
```

- [ ] **Step 3: 跑測試,確認失敗**

Run: `npx vitest run src/embed.test.ts`(在 `bot/`)
Expected: FAIL —— `buildReply` 還沒收 `quotePng`、description 仍含「買盤」、`formatLadder` import 已移除導致引用錯誤。

- [ ] **Step 4: 改實作 `embed.ts`**

1. 刪掉第 6-25 行的 `Lvl` 型別、`cell` / `qty` / `side` / `formatLadder` / `sumSize`(整段 helper)。
2. `buildReply` 參數型別(第 27-31 行)把 `png` 換成 `png` + `quotePng`:

```diff
 export function buildReply(args: {
   symbol: string; name: string | null; lastClose: number; change: number; changePct: number;
   open: number; high: number; low: number; vwap: number; volume: number;
-  cdp: CdpLevels | null; ma: MaLevels | null; quote: QuoteResp | null; png: Buffer | null; asOf: string;
+  cdp: CdpLevels | null; ma: MaLevels | null; quote: QuoteResp | null;
+  png: Buffer | null; quotePng: Buffer | null; asOf: string;
 }) {
```

3. 函式內第 35-37 行:移除 `ladder`,新增五檔圖 attachment:

```diff
   const up = args.change > 0;
   const color = up ? 0xe85a4f : args.change < 0 ? 0x7fc99a : 0x8a8273;
-  const file = args.png ? new AttachmentBuilder(args.png, { name: "chart.png" }) : null;
-  // 五檔失敗 quote=null:五檔區降級,不拖垮已備好的圖/現價/CDP/MA
-  const ladder = args.quote ? formatLadder(args.quote.bids, args.quote.asks) : "　五檔暫無資料";
+  const chartFile = args.png ? new AttachmentBuilder(args.png, { name: "chart.png" }) : null;
+  // 五檔改成獨立第二張圖(quote.png),不被 embed 引用 → Discord 顯示在 embed 下方(spec §3.1.4)
+  const quoteFile = args.quotePng ? new AttachmentBuilder(args.quotePng, { name: "quote.png" }) : null;
```

4. description 拿掉 ladder code block(第 51-54 行):

```diff
     .setDescription(
-      `**${args.lastClose.toFixed(2)}**　${arrow}${Math.abs(args.change).toFixed(2)} (${args.changePct >= 0 ? "+" : ""}${args.changePct.toFixed(2)}%)${limit}\n` +
-      "```\n" + ladder + "\n```",
+      `**${args.lastClose.toFixed(2)}**　${arrow}${Math.abs(args.change).toFixed(2)} (${args.changePct >= 0 ? "+" : ""}${args.changePct.toFixed(2)}%)${limit}`,
     )
```

5. 移除「委買 / 委賣(張)」欄位(第 58 行那條 `addFields` 項):

```diff
     .addFields(
       { name: "開 / 高 / 低", value: `${args.open} / ${args.high} / ${args.low}`, inline: true },
       { name: "均價 / 量", value: `${args.vwap.toFixed(2)} / ${args.volume}`, inline: true },
-      { name: "委買 / 委賣(張)", value: args.quote ? `${sumSize(args.quote.bids)} / ${sumSize(args.quote.asks)}` : "—", inline: true },
       { name: "CDP", value: cdp, inline: false },
       { name: "均線", value: ma, inline: false },
     )
```

6. 結尾組 files(第 63-66 行):

```diff
-  // 產圖失敗時 png=null:省略附圖,只回文字 embed(現價/五檔/CDP/MA 已備齊)
-  if (file) embed.setImage("attachment://chart.png");
-
-  return { embeds: [embed], files: file ? [file] : [] };
+  // 分時圖放進 embed;五檔圖當額外附件(不被 embed 引用 → 顯示在 embed 下方)
+  if (chartFile) embed.setImage("attachment://chart.png");
+  const files = [chartFile, quoteFile].filter((f): f is AttachmentBuilder => f !== null);
+  return { embeds: [embed], files };
```

- [ ] **Step 5: 跑測試,確認通過**

Run: `npx vitest run src/embed.test.ts`(在 `bot/`)
Expected: PASS（4 條 buildReply 測試)

- [ ] **Step 6: Commit**

```bash
git add bot/src/embed.ts bot/src/embed.test.ts
git commit -m "feat(bot): buildReply 收五檔圖 + 移除文字五檔/委買委賣欄"
```

---

## Task 7: `composeReply` 串接 `quotePng`

**Files:**
- Modify: `bot/src/reply.ts`
- Test: `bot/src/reply.test.ts`

- [ ] **Step 1: 改測試 —— `composeReply` 簽名加 `quotePng`、五檔改圖**

`reply.test.ts` 第 58-81 行的 `describe("composeReply ...")` 改成:

```ts
describe("composeReply — 組裝降級(五檔改圖)", () => {
  const slowOk = {
    empty: false as const, name: "台積電", cdp: CDP, ma: MA, png: Buffer.from([0x89]) as Buffer | null,
    lastClose: 102, change: 2, changePct: 2, open: 100, high: 103, low: 99, vwap: 101, volume: 4000, asOf: "13:30",
  };
  const quotePng = Buffer.from([0x89, 0x51]);

  it("分時圖 + 五檔圖都在 → files 兩張", () => {
    const r = composeReply("2330", slowOk, QUOTE, quotePng);
    expect(r.files).toHaveLength(2);
  });

  it("分時圖失敗(png=null)→ 只附五檔圖、描述不再含文字五檔", () => {
    const r = composeReply("2330", { ...slowOk, png: null }, QUOTE, quotePng);
    expect(r.files).toHaveLength(1);
    expect((r.embeds![0] as { data: { description?: string } }).data.description).not.toContain("買盤");
  });

  it("空盤前(empty)→ 回純文字 content(含 CDP/MA5)、不附圖", () => {
    const r = composeReply("2330", { empty: true as const, cdp: CDP, ma: MA, name: "台積電", prevClose: 100 }, null, null);
    expect(r.content).toContain("無分時資料");
    expect(r.content).toContain("MA5");
    expect(r.files ?? []).toHaveLength(0);
  });

  it("五檔抓不到(quote=null, quotePng=null)→ 仍回含分時圖的 embed", () => {
    const r = composeReply("2330", slowOk, null, null);
    expect(r.files).toHaveLength(1);
  });
});
```

- [ ] **Step 2: 跑測試,確認失敗**

Run: `npx vitest run src/reply.test.ts -t "composeReply"`(在 `bot/`)
Expected: FAIL —— `composeReply` 只收 3 個參數。

- [ ] **Step 3: 改實作 `reply.ts`**

`composeReply`(第 64-79 行)加 `quotePng` 參數並下傳:

```diff
-export function composeReply(symbol: string, s: SlowResult, quote: QuoteResp | null): MessageReplyOptions {
+export function composeReply(symbol: string, s: SlowResult, quote: QuoteResp | null, quotePng: Buffer | null): MessageReplyOptions {
   if (s.empty) {
     return {
       content:
         `\`${symbol}\` 目前無分時資料(盤前/非交易日)。` +
         `CDP:${s.cdp ? `${s.cdp.cdp}` : "—"} MA5:${s.ma?.sma_5 ?? "—"}`,
     };
   }
   return buildReply({
     symbol, name: s.name,
     lastClose: s.lastClose, change: s.change, changePct: s.changePct,
     open: s.open, high: s.high, low: s.low,
     vwap: s.vwap, volume: s.volume,
-    cdp: s.cdp, ma: s.ma, quote, png: s.png, asOf: s.asOf,
+    cdp: s.cdp, ma: s.ma, quote, png: s.png, quotePng, asOf: s.asOf,
   });
 }
```

- [ ] **Step 4: 跑測試,確認通過**

Run: `npx vitest run src/reply.test.ts`(在 `bot/`)
Expected: PASS（composeReply + 既有 loadSlow 測試全綠)

- [ ] **Step 5: Commit**

```bash
git add bot/src/reply.ts bot/src/reply.test.ts
git commit -m "feat(bot): composeReply 串接五檔圖 quotePng"
```

---

## Task 8: `index.ts` 接線 —— 抓 quote 後即時產五檔圖

**Files:**
- Modify: `bot/src/index.ts:11-23`

> `handle()` 是 discord 事件處理、無既有單元測試;此 task 靠型別檢查 + 手動煙霧驗證(Task 9)。

- [ ] **Step 1: import `renderQuotePng` / `safeRender`**

`index.ts` 第 5-6 行附近的 import 區加:

```diff
 import { getQuote } from "./data";
+import { renderQuotePng, safeRender } from "./render";
 import { TtlCache } from "./cache";
 import { loadSlow, composeReply, type SlowResult } from "./reply";
```

- [ ] **Step 2: `handle()` 產 `quotePng` 並傳入 `composeReply`**

第 11-23 行 `handle` 改成:

```diff
 async function handle(msg: Message, symbol: string) {
   try {
     const [s, quote] = await Promise.all([
       slow.get(symbol, () => loadSlow(symbol)),
       getQuote(symbol).catch(() => null),  // 五檔失敗 → null,不拖垮已備好的圖/CDP/MA(spec §8)
     ]);
-    await msg.reply(composeReply(symbol, s, quote));
+    // 五檔圖必須跟即時 quote 同產(quote 不走快取);產圖失敗 → null,只少一張圖(spec §3.1.3)
+    const quotePng = quote ? safeRender(() => renderQuotePng(quote)) : null;
+    await msg.reply(composeReply(symbol, s, quote, quotePng));
   } catch (e) {
     msg.reply(`\`${symbol}\` 查詢失敗(行情暫時不可用)。`).catch(console.error);
     console.warn(`[bot] ${symbol} 失敗:`, e);
   }
 }
```

- [ ] **Step 3: 型別檢查**

Run: `npx tsc --noEmit`(在 `bot/`)
Expected: 無錯誤(`composeReply` 第 4 參數已對齊)。

- [ ] **Step 4: Commit**

```bash
git add bot/src/index.ts
git commit -m "feat(bot): handle 抓 quote 後即時產五檔圖、傳入 composeReply"
```

---

## Task 9: 全測試 + 視覺驗證 + 收尾

**Files:** 無新增;驗證關卡。

- [ ] **Step 1: 跑前端全測試**

Run: `npx vitest run`(在 `frontend/`)
Expected: 全綠(含更新後的 snapshot、CDP 全標、五檔圖元件)。

- [ ] **Step 2: 跑 bot 全測試**

Run: `npx vitest run`(在 `bot/`)
Expected: 全綠(render / embed / reply 全綠)。

- [ ] **Step 3: 視覺驗證 —— 兩張 PNG 縮到 feed 寬可讀**

寫一次性 scratch(或在 bot 端 node REPL)呼叫 `renderChartPng(...)` 與 `renderQuotePng(...)` 各存一張 PNG,把圖縮到 **~450px 寬**(模擬 Discord 桌面 feed)肉眼確認:
- 五檔圖:總量、5 檔價量、量條、「市價」、鎖漲停 badge 都看得清。
- 分時圖:Y 軸價、CDP* label、時間軸、今日高低 都看得清,免點開。
- 不夠清楚 → 回 Task 3 / Task 4 微調字級(已預留)。

- [ ] **Step 4: 視覺驗證 —— 網頁分時圖沒破版**

Run: `npm run dev`(在 `frontend/`),開分時圖確認:CDP 5 條都帶 `*`、字級變大後沒擠爆 / 重疊(共用層副作用)。

- [ ] **Step 5: 盤中實測(user,台股 9:00–13:30)**

bot 上線,Discord 打 `p2330`,手機 + 電腦各確認:分時圖在上、五檔圖在下,兩張免點開可讀;鎖漲跌停時 badge 正確。

- [ ] **Step 6: 收尾 commit(若視覺微調有改動)**

```bash
git add -A
git commit -m "chore(bot): 五檔圖/分時圖 feed 可讀性視覺微調"
```

---

## Self-Review(已執行)

**Spec coverage:** 五檔改圖(Task 4-8)、CDP 全標圖+文字(Task 1-2)、圖片 feed 可讀(Task 3 分時圖 + Task 4 五檔 landscape + Task 9 驗證)、即時不快取(Task 5 註解 + Task 8 接線)、降級(Task 6 四條 buildReply 測試)、共用層副作用(Task 9 Step 4 網頁驗證)—— 全部對應到 task。

**Placeholder scan:** 無 TBD/TODO;字級 15/13 是明確起點值(視覺驗證後微調為預期迭代,已標明)。

**Type consistency:** `quotePng: Buffer | null` 一路貫穿 `renderQuotePng`(產出)→ `index.handle`(產)→ `composeReply`(第 4 參數)→ `buildReply`(`args.quotePng`)→ `quote.png` attachment;`QuoteBookSvgInput` 的 `isLimitUp`/`isLimitDown` 由 bot `renderQuotePng` 從 `QuoteResp.is_limit_up_*` 映射,一致。
