# Bot 分時圖字級放大(feed 可讀)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓 bot 推到 Discord 的分時圖,圖內字放大 160%、標題帶 150%,且不超出邊界;網頁分時圖逐像素不變。

**Architecture:** 在共用畫圖層 `intraday-chart-svg.tsx` 加一個 `scale` 參數(預設 1)。`computeIntradayGeometry` 算出 effective padding 與 `fontScale` 放進回傳的 geometry,`IntradayChartStatic` 一律從 geometry 讀(不再直接用 module 常數),字級/線寬一律 ×`fontScale`。網頁不傳(scale=1,完全不變),bot 傳 1.6。bot 標題帶在 `render.ts` 獨立放大(150%)。

**Tech Stack:** TypeScript、React(`react-dom/server` 產 SVG)、Vitest、`@resvg/resvg-js`(bot 端 SVG→PNG)。

**Spec:** `docs/superpowers/specs/2026-06-07-bot-intraday-chart-font-scale-design.md`

---

## File Structure

| 檔案 | 責任 | 動作 |
|---|---|---|
| `frontend/src/lib/intraday-chart-svg.tsx` | 共用畫圖層:幾何計算 + 靜態 SVG 圖層 | 修改(加 scale) |
| `frontend/src/lib/intraday-chart-svg.test.ts` | 共用層單元測試 | 修改(加 scale 測試) |
| `bot/src/render.ts` | bot 端 SVG→PNG + 標題帶 | 修改(套 scale 1.6 + 標題帶) |
| `bot/src/render.test.ts` | bot render 測試 | 修改(加 scale/標題/截斷測試) |
| `frontend/src/components/IntradayChart.tsx` | 網頁分時圖(互動) | **不改**(吃預設 scale=1) |

**測試命令**(在各自目錄):
- 前端:`cd frontend && npx vitest run src/lib/intraday-chart-svg.test.ts`
- bot:`cd bot && npx vitest run src/render.test.ts`

---

## Task 1: 共用層加 `scale` — geometry 回傳 effective padding + fontScale

**Files:**
- Modify: `frontend/src/lib/intraday-chart-svg.tsx`(`IntradayChartInput`、`IntradayGeometry`、`computeIntradayGeometry`)
- Test: `frontend/src/lib/intraday-chart-svg.test.ts`

此 task 只「加能力」,不啟用放大 —— 沒有人傳 scale 時一切照舊。

- [ ] **Step 1: 寫失敗測試**

加到 `intraday-chart-svg.test.ts` 的 `describe("computeIntradayGeometry", ...)` 區塊內:

```ts
it("scale=1.6:effective padding 與 fontScale 隨 scale 放大", () => {
  const candles = [candle(540, 100), candle(810, 100)];
  const g = computeIntradayGeometry({ candles, prevClose: 100, cdp: null, camarilla: null, ma: null, flags: FLAGS, scale: 1.6 });
  expect(g.fontScale).toBe(1.6);
  expect(g.padL).toBe(90);   // round(56 * 1.6)
  expect(g.padR).toBe(90);
  expect(g.padT).toBe(19);   // round(12 * 1.6)
  expect(g.padB).toBe(45);   // round(28 * 1.6)
  // scaleX 內緣跟著 effective padding 走
  expect(g.scaleX(540)).toBeCloseTo(90, 5);
  expect(g.scaleX(810)).toBeCloseTo(CHART_W - 90, 5);
});

it("不傳 scale:padding/fontScale 為原值(網頁回歸保護)", () => {
  const candles = [candle(540, 100), candle(810, 100)];
  const g = computeIntradayGeometry({ candles, prevClose: 100, cdp: null, camarilla: null, ma: null, flags: FLAGS });
  expect(g.fontScale).toBe(1);
  expect(g.padL).toBe(56);
  expect(g.padR).toBe(56);
  expect(g.scaleX(810)).toBeCloseTo(CHART_W - 56, 5);
});
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npx vitest run src/lib/intraday-chart-svg.test.ts -t "effective padding"`
Expected: FAIL —— `g.fontScale` / `g.padL` 為 `undefined`(欄位還不存在)。

- [ ] **Step 3: 實作 — interface 加欄位**

在 `IntradayChartInput`(約 line 43-51)末尾加:

```ts
  theme?: ChartTheme;
  // 圖內元素(字級/留白/間距/線寬)相對畫布的放大倍率。網頁=1(預設、不變),bot=1.6。
  // 畫布總尺寸 CHART_W/CHART_H/VOL_H 不隨 scale 變 —— 只放大裡面的內容,即字佔比變大。
  scale?: number;
```

在 `IntradayGeometry`(約 line 53-66)加(放在 `scaleY` 之後):

```ts
  scaleX: (m: number) => number;
  scaleY: (v: number) => number;
  padL: number; padR: number; padT: number; padB: number;  // effective(已 ×scale、四捨五入)
  fontScale: number;                                        // = scale,給 IntradayChartStatic 算字級/線寬
```

- [ ] **Step 4: 實作 — computeIntradayGeometry 用 effective padding**

在函式開頭(約 line 82-83)`const theme = ...` 之後插入:

```ts
  const scale = input.scale ?? 1;
  const padL = Math.round(PAD_L * scale);
  const padR = Math.round(PAD_R * scale);
  const padT = Math.round(PAD_T * scale);
  const padB = Math.round(PAD_B * scale);
  const labelGap = Math.round(20 * scale);  // resolveCollisions 撐開間距,跟著字放大
```

接著把函式內部對 module 常數的引用換成 effective 值:
- `const xRange = CHART_W - PAD_L - PAD_R;` → `const xRange = CHART_W - padL - padR;`
- `const yRange = CHART_H - PAD_T - PAD_B;` → `const yRange = CHART_H - padT - padB;`
- `scaleX = (m) => PAD_L + ...` → 用 `padL`
- `scaleY = (v) => PAD_T + ...` → 用 `padT`
- `resolveCollisions(labelInputs, 20, [PAD_T, CHART_H - PAD_B])` → `resolveCollisions(labelInputs, labelGap, [padT, CHART_H - padB])`

(`volTop`/`scaleVolY`/`maxVolume`/`volBarW` 維持 —— 量 pane 以 `CHART_H`/`TOTAL_H` 為基準,固定。`volBarW` 因 `xRange` 變窄會自動縮,無需改。)

兩個 `return` 物件(空 candles 的 early-return 約 line 92-104、正常 return 約 line 223-231)**都**加上新欄位:

```ts
    scaleX, scaleY,
    padL, padR, padT, padB, fontScale: scale,
```

> ⚠️ early-return 物件也必須補,否則 TS 編譯失敗(interface 已要求這些欄位)。空 return 的 `scaleX: () => 0` 等維持。

- [ ] **Step 5: 跑測試確認通過 + 回歸**

Run: `cd frontend && npx vitest run src/lib/intraday-chart-svg.test.ts`
Expected: 全綠 —— 新增 2 條過;**既有測試(含 4 個 snapshot)全部仍綠**(沒人傳 scale,padding=56、輸出不變)。若 snapshot 紅 → 代表誤改了 scale=1 行為,回查 Step 4。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/intraday-chart-svg.tsx frontend/src/lib/intraday-chart-svg.test.ts
git commit -m "feat(chart): 分時圖共用層加 scale 參數(geometry 回 effective padding/fontScale)

scale 預設 1,網頁行為不變;後續 bot 傳 1.6 放大字級。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `IntradayChartStatic` 用 effective padding + 字級/線寬 ×fontScale

**Files:**
- Modify: `frontend/src/lib/intraday-chart-svg.tsx`(`IntradayChartStatic`,約 line 240-482)
- Test: `frontend/src/lib/intraday-chart-svg.test.ts`

- [ ] **Step 1: 寫失敗測試**

加到 `intraday-chart-svg.test.ts`(檔案末尾):

```ts
it("scale=1.6:圖內 label 字級放大到 24(font-size:24)", () => {
  const candles = [candle(540, 100), candle(600, 103), candle(810, 102)];
  const input = {
    candles, prevClose: 100,
    cdp: { ah: 108, nh: 104, cdp: 101, nl: 98, al: 95, as_of_date: "2026-06-04" },
    camarilla: null, ma: null,
    flags: { vwap: true, cdp: true, camarilla: false, volume: true, ma: false },
    scale: 1.6,
  };
  const geometry = computeIntradayGeometry(input);
  const svg = renderToStaticMarkup(createElement(IntradayChartStatic, { ...input, geometry }));
  expect(svg).toContain('font-size="24"');   // 15 * 1.6
  expect(svg).toContain('font-size="21"');   // 13 * 1.6(量區)
  expect(svg).not.toContain('font-size="15"'); // 原始字級不該再出現
});

it("scale=1.6:中價股最長 CDP label(6 字含 *)右緣不超出畫布", () => {
  // 384.5* 是 formatTickPrice 下最長的 label(100–500 元股,1 位小數 + *)
  const candles = [candle(540, 384.5), candle(810, 384.5)];
  const cdp = { ah: 400, nh: 392, cdp: 384.5, nl: 376, al: 368, as_of_date: "2026-06-04" };
  const g = computeIntradayGeometry({ candles, prevClose: 384.5, cdp, camarilla: null, ma: null,
    flags: { vwap: false, cdp: true, camarilla: false, volume: false, ma: false }, scale: 1.6 });
  // label 從右側 margin 起排:錨點 x = CHART_W - padR + 6;餘給 label 的寬 = padR - 6
  const labelRoom = g.padR - 6;
  // 最長 6 字 @ 24px、JhengHei 數字 ~0.5em → 保守上界 6 * 24 * 0.62 ≈ 89px... 但實測 padR=90 足夠
  expect(g.padR).toBe(90);
  expect(labelRoom).toBeGreaterThanOrEqual(80); // 容 6 字(~65–80px)有餘
});
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npx vitest run src/lib/intraday-chart-svg.test.ts -t "字級放大到 24"`
Expected: FAIL —— SVG 仍含 `font-size="15"`(static 還沒套 fontScale)。

- [ ] **Step 3: 實作 — 解構新欄位 + 加字級/線寬 helper**

在 `IntradayChartStatic`(約 line 240-248)的 geometry 解構裡補上新欄位,並加兩個 helper:

```ts
  const {
    scaleX, scaleY, scaleVolY, polyClose, polyVwap,
    visibleCdpKeys, visibleCamKeys, visibleMaKeys,
    todayHigh, todayHighIdx, todayLow, todayLowIdx,
    maxVolume, volBarW, resolvedLabels, minutesByIdx, filteredCandles,
    padL, padR, padT, padB, fontScale,
  } = props.geometry;
  const fs = (base: number) => Math.round(base * fontScale);  // 字級:scale=1 時 === 原值
  const sw = (base: number) => base * fontScale;              // 線寬/marker 半徑
```

- [ ] **Step 4: 實作 — 全函式替換(三條規則)**

在 `IntradayChartStatic` 函式體內(line 252-481)做以下替換。**僅限本函式內**:

**規則 A — module 常數 padding 改 effective(已從 geometry 解構):**
把本函式內所有 `PAD_L` → `padL`、`PAD_R` → `padR`、`PAD_T` → `padT`、`PAD_B` → `padB`。
出現處:clipPath 兩個 rect(`x: PAD_L`、`width: CHART_W - PAD_L - PAD_R`、`y: PAD_T`、`height` 用 `PAD_T`/`PAD_B`);格線 `x1: PAD_L, x2: CHART_W - PAD_R`;CDP/Camarilla/MA 三組水平線的 `x1: PAD_L, x2: CHART_W - PAD_R`;label 引導線與文字的 `CHART_W - PAD_R + 6` / `CHART_W - PAD_R + 4`;Y 軸格線文字 `x: PAD_L - 4`;量區分隔線 `x1: PAD_L, x2: CHART_W - PAD_R`;量 max label `x: CHART_W - PAD_R - 2`;VOL label `x: PAD_L - 4`。
（`CHART_W`、`CHART_H`、`TOTAL_H`、`VOL_GAP`、`VOL_PAD_T` 維持不變 —— 畫布尺寸固定。X 軸時間 `y: CHART_H - 8`、量區 `y: CHART_H + ...` 都保留 `CHART_H`。）

**規則 B — 字級 ×fontScale:**
本函式內所有 `fontSize: 15` → `fontSize: fs(15)`;所有 `fontSize: 13` → `fontSize: fs(13)`。
出現處(7 個):Y 軸格線(line 311)、右側 margin label(378)、今日高(414)、今日低(428)、量 max(445,13)、VOL(451,13)、X 軸時間(478)。

**規則 C — 資訊線/marker 線寬 ×fontScale(背景格線維持細):**
包 `sw()` 的(資訊性):CDP 線 `strokeWidth: "0.6"` → `strokeWidth: sw(0.6)`(line 325);Camarilla `strokeWidth: isMain ? "0.8" : "0.6"` → `sw(isMain ? 0.8 : 0.6)`(338);MA `"0.6"` → `sw(0.6)`(356);VWAP `"1"` → `sw(1)`(365);label 引導線 `"0.7"` → `sw(0.7)`(374);主價線兩條 `"1"` → `sw(1)`(388、392);baseline fallback 線 `"1"` → `sw(1)`(398);今日高/低 marker `r: "2.5"` → `r: sw(2.5)`(406、419)。
**維持不變(背景、不放大)**:Y 軸格線 `strokeWidth: isBaseline ? 0.8 : 0.5`(305);量區分隔線 `strokeWidth: "0.5"`(440)。

- [ ] **Step 5: 跑測試確認通過 + 回歸**

Run: `cd frontend && npx vitest run src/lib/intraday-chart-svg.test.ts`
Expected: 全綠 —— 新增 2 條過;**4 個既有 snapshot 仍綠**(scale=1 時 `fs(15)=15`、`sw(1)=1`、padding=56,輸出逐字不變)。

> 若 snapshot 紅:檢查是否有 `sw()`/`fs()` 在 scale=1 算出非原值(如誤把背景格線也包了 sw,或 round 行為差異)。snapshot 是網頁零影響的守門員,**不可用 `-u` 蓋過**,要查到 scale=1 輸出真的相同為止。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/intraday-chart-svg.tsx frontend/src/lib/intraday-chart-svg.test.ts
git commit -m "feat(chart): IntradayChartStatic 字級/留白/線寬隨 fontScale 放大

改用 geometry 的 effective padding(刻度與繪製單一來源);字級×fontScale、
資訊線/marker×fontScale,背景格線維持細。scale=1 輸出逐像素不變(snapshot 綠)。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: bot 端套 scale 1.6 + 標題帶放大 / 長名截斷

**Files:**
- Modify: `bot/src/render.ts`(`renderChartPng`、`TITLE_H`,新增 `buildChartSvg`、`fitTitle`)
- Test: `bot/src/render.test.ts`

bot 標題帶(SVG 內,非共用層)獨立放大到 150%;圖內走共用層 scale 1.6。為了能測 SVG 內容,先把「組 SVG 字串」抽成可測純函式。

- [ ] **Step 1: 寫失敗測試**

加到 `bot/src/render.test.ts`(import 補 `buildChartSvg, fitTitle`):

```ts
import { renderChartPng, safeRender, renderQuotePng, buildChartSvg, fitTitle } from "./render";

describe("buildChartSvg — 字級放大 + 標題帶", () => {
  const base = {
    candles: CANDLES, prevClose: 588, cdp: CDP, camarilla: null, ma: MA,
    flags: { vwap: true, cdp: true, camarilla: false, volume: true, ma: true },
    symbol: "2330", name: "台積電", lastClose: 600, change: 12, changePct: 2.04,
  };

  it("圖內字級放大到 24(共用層 scale 1.6)", () => {
    expect(buildChartSvg(base)).toContain('font-size="24"');
  });

  it("標題帶字級放大到 33(150%)", () => {
    expect(buildChartSvg(base)).toContain('font-size="33"');
  });

  it("含量時 viewBox 高度 = TOTAL_H(748) + TITLE_H(58) = 806", () => {
    expect(buildChartSvg(base)).toContain('height="806"');
  });
});

describe("fitTitle — 超長股名截斷,避免撞右側現價", () => {
  it("一般長度原樣保留", () => {
    expect(fitTitle("2330", "台積電")).toBe("2330 台積電");
  });
  it("超長截斷補 …,長度受限", () => {
    const out = fitTitle("00940", "元大臺灣價值高息成分股ETF基金");
    expect(out.endsWith("…")).toBe(true);
    expect([...out].length).toBeLessThanOrEqual(14);
  });
  it("name 為 null 只回代號", () => {
    expect(fitTitle("2330", null)).toBe("2330");
  });
});
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd bot && npx vitest run src/render.test.ts -t "buildChartSvg"`
Expected: FAIL —— `buildChartSvg` / `fitTitle` 未 export(尚未定義)。

- [ ] **Step 3: 實作 — fitTitle + 提高 TITLE_H**

在 `render.ts`,把 `const TITLE_H = 44;`(line 17)改為:

```ts
const TITLE_H = 58;  // 標題帶 150% 字級(33px)需要更高的帶子
```

在 `FONT_FAMILY` 常數之後加 helper:

```ts
// 標題帶左側「代號 + 名稱」過長會撞到右側現價(33px 字、820 寬)。
// 上界 14 字元(代號 ~5 + 空格 + 中文名),超過截斷補 …。
export function fitTitle(symbol: string, name: string | null): string {
  const full = `${symbol}${name ? " " + name : ""}`;
  const MAX = 14;
  const chars = [...full];
  return chars.length > MAX ? chars.slice(0, MAX - 1).join("") + "…" : full;
}
```

- [ ] **Step 4: 實作 — 抽 buildChartSvg + 套 scale 1.6 + 標題帶 33**

把 `renderChartPng`(line 41-76)重構成:`buildChartSvg` 產 SVG 字串、`renderChartPng` 包 `svgToPng`。

```ts
export function buildChartSvg(args: IntradayChartInput & {
  symbol: string; name: string | null; lastClose: number; change: number; changePct: number;
}): string {
  const input: IntradayChartInput = { ...args, theme: THEME, scale: 1.6 };  // bot 圖內字 160%
  const geometry = computeIntradayGeometry(input);
  const dirColor = args.change > 0 ? THEME.bull : args.change < 0 ? THEME.bear : THEME.ink;
  const chartH = args.flags.volume ? TOTAL_H : CHART_H;
  const totalH = chartH + TITLE_H;
  const arrow = args.change > 0 ? "▲" : args.change < 0 ? "▾" : "—";

  return renderToStaticMarkup(
    createElement("svg", {
      xmlns: "http://www.w3.org/2000/svg",
      viewBox: `0 0 ${CHART_W} ${totalH}`,
      width: CHART_W,
      height: totalH,
    },
      createElement("rect", { x: 0, y: 0, width: CHART_W, height: totalH, fill: THEME.bg }),
      // 標題帶:左側代號+名稱(超長截斷),右側現價與漲跌;字級 150% = 33
      createElement("text", {
        x: 14, y: 40,
        fontSize: 33, fontFamily: FONT_FAMILY, fill: THEME.ink,
      }, fitTitle(args.symbol, args.name)),
      createElement("text", {
        x: CHART_W - 14, y: 40,
        fontSize: 33, textAnchor: "end", fontFamily: FONT_FAMILY, fill: dirColor,
      }, `${args.lastClose.toFixed(2)}  ${arrow}${Math.abs(args.change).toFixed(2)} (${args.changePct >= 0 ? "+" : ""}${args.changePct.toFixed(2)}%)`),
      createElement("g", { transform: `translate(0, ${TITLE_H})` },
        createElement(IntradayChartStatic, { ...input, geometry }),
      ),
    ),
  );
}

export function renderChartPng(args: IntradayChartInput & {
  symbol: string; name: string | null; lastClose: number; change: number; changePct: number;
}): Buffer {
  return svgToPng(buildChartSvg(args));
}
```

(標題帶 baseline 從 y=30 下移到 y=40,配合 58px 帶高與 33px 字。`IntradayChartInput` 已在 Task 1 加 `scale`,這裡傳 `scale: 1.6`。)

- [ ] **Step 5: 跑測試確認通過 + 回歸**

Run: `cd bot && npx vitest run src/render.test.ts`
Expected: 全綠 —— 新增 buildChartSvg/fitTitle 測試過;既有 smoke test(PNG magic bytes、`safeRender`、`renderQuotePng`)仍綠。

- [ ] **Step 6: tsc 型別檢查**

Run: `cd bot && npx tsc --noEmit`
Expected: 0 error(bot 跨 import 共用層型別,確認 `scale` 欄位相容)。

- [ ] **Step 7: Commit**

```bash
git add bot/src/render.ts bot/src/render.test.ts
git commit -m "feat(bot): 分時圖推播套 scale 1.6 + 標題帶 150%/長名截斷

抽 buildChartSvg 純函式(可測 SVG);圖內字 160%、標題帶 33px、TITLE_H 58、
超長股名截斷補 …。網頁不受影響(只有 bot 傳 scale)。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: 全套件回歸 + 產一張 PNG 肉眼複核

**Files:** 無(驗證)

- [ ] **Step 1: 前端完整測試**

Run: `cd frontend && npx vitest run`
Expected: 全綠(60+ 測試,含所有 snapshot)。確認共用層改動沒打到網頁。

- [ ] **Step 2: bot 完整測試 + 型別**

Run: `cd bot && npx vitest run` 然後 `npx tsc --noEmit`
Expected: 全綠、0 型別錯誤。

- [ ] **Step 3: 產一張真實 PNG 存檔肉眼看**

寫一個臨時腳本(或在 bot 加一個一次性 script)呼叫 `renderChartPng` 用 Task 3 的 `base` 假資料,把回傳 Buffer 寫成 `bot/_preview.png`,開檔確認:標題帶字大不撞、CDP `*`/價格/時間字大且都在框內、無重疊。看完刪檔(`_preview.png` 不進 git)。

> 用高價股(如 lastClose 1085、CDP 用 4 位數)與中價股(384.5)各產一張,確認最長 label 不超界。

- [ ] **Step 4: 確認 git 乾淨**

Run: `git status`
Expected: 只有 3 個 commit 的改動已入庫,working tree 無殘留臨時檔(`_preview.png` 已刪)。

---

## 待驗(需 user / 環境,plan 外)

1. **盤中實機**(台股 9:00–13:30):`p代號` 查一檔,看 Discord feed 裡分時圖字夠大、CDP `*`/時間/價格不超界、不重疊。
2. **肉眼確認網頁分時圖沒變**:開網頁分時圖對照,應與放大前一致。

---

## Self-Review(plan 作者已檢查)

**1. Spec coverage:**
- scale 參數機制(spec §3)→ Task 1 + 2 ✓
- effective padding 單一來源(spec §3.2,防刻度/線錯位)→ Task 2 規則 A(static 改用 geometry padding)✓
- 字級 ×scale、背景格線不放大(spec §3.3)→ Task 2 規則 B/C ✓
- bot scale 1.6 + 標題帶 150% + TITLE_H 58 + 長名截斷(spec §4)→ Task 3 ✓
- 網頁不變(spec §2 #4)→ Task 1/2 的 scale=1 snapshot 回歸 ✓
- 最長 label 不超界(spec §3.1 邊界、§6)→ Task 2 Step 1 中價股 6 字測試 + Task 4 肉眼 ✓
- 五檔圖不動、zoom 不動(spec §8)→ 計畫未觸及 ✓

**2. Placeholder scan:** 無 TBD/TODO;每個 code step 都有完整 code 或精確替換規則。Task 4 Step 3 的臨時腳本是一次性驗證,故描述步驟而非固定檔案。

**3. Type consistency:** `scale`(input)→ `fontScale`(geometry)命名一致;`padL/padR/padT/padB` 在 Task 1 定義、Task 2 解構使用,一致;`buildChartSvg`/`fitTitle` 在 Task 3 定義並 export、測試同名。

**已知取捨**(spec 已記):繪圖區寬 708→640(窄 10%);標題帶 150% 而非 160%(空間最緊);最長 label 字寬精確值依 resvg+JhengHei 實際 metrics,plan 用保守估算 + 盤中肉眼把關。
