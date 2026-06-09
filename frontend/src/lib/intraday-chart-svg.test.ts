import { describe, it, expect } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { computeIntradayGeometry, IntradayChartStatic, CHART_W, PAD_L } from "./intraday-chart-svg";
import type { IntradayCandle } from "./api";

function candle(min: number, close: number): IntradayCandle {
  const hh = String(Math.floor(min / 60)).padStart(2, "0");
  const mm = String(min % 60).padStart(2, "0");
  return { date: `2026-06-05T${hh}:${mm}:00.000+08:00`, open: close, high: close, low: close, close, volume: 100, average: close };
}

// 從 renderToStaticMarkup 的 SVG 字串抽出每個 <text> 的實際 x / font-size / text-anchor / 內容。
// 用來「真的」量 label 渲染後落點,而非只驗 geometry 算術。
function extractTextElements(svg: string): Array<{ x: number; fontSize: number; textAnchor: string; text: string }> {
  const out: Array<{ x: number; fontSize: number; textAnchor: string; text: string }> = [];
  const re = /<text\b([^>]*)>(.*?)<\/text>/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(svg)) !== null) {
    const attrs = m[1];
    const x = Number(/\bx="([-\d.]+)"/.exec(attrs)?.[1]);
    const fontSize = Number(/\bfont-size="([-\d.]+)"/.exec(attrs)?.[1]);
    const textAnchor = /\btext-anchor="([^"]+)"/.exec(attrs)?.[1] ?? "start"; // SVG 預設 start
    if (Number.isFinite(x) && Number.isFinite(fontSize)) {
      out.push({ x, fontSize, textAnchor, text: m[2] });
    }
  }
  return out;
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

  it("CDP 5 條 label 全帶 *(不再只標中樞)", () => {
    const candles = [candle(540, 100)];
    const cdp = { ah: 108, nh: 104, cdp: 101, nl: 98, al: 95, as_of_date: "2026-06-04" };
    const g = computeIntradayGeometry({ candles, prevClose: 100, cdp, camarilla: null, ma: null, flags: FLAGS });
    const cdpLabels = g.resolvedLabels.filter((l) => l.color === "#e85a4f"); // theme.accent
    expect(cdpLabels).toHaveLength(5);                          // 5 條都在 ±10% 內
    expect(cdpLabels.every((l) => l.text.endsWith("*"))).toBe(true);  // 全帶 *
  });

  it("空 candles 不爆,回安全空值", () => {
    const g = computeIntradayGeometry({ candles: [], prevClose: 100, cdp: null, camarilla: null, ma: null, flags: FLAGS });
    expect(g.filteredCandles).toEqual([]);
    expect(g.polyClose).toBe("");
  });

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
});

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

it("數字 text 用 tabular-nums、今日高低 font-medium、Vol uppercase(§9 外觀不變 / review #3)", () => {
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
  expect(svg).toContain("font-variant-numeric:tabular-nums"); // 價格/量/時間數字等寬對齊
  expect(svg).toContain("font-weight:500");                    // 今日高低標籤(font-medium)
  expect(svg).toContain("text-transform:uppercase");           // Vol
});

// 防漂移不能只鎖一種組合(review #6):補量能關 / 全 toggle 關 / prevClose=null 三種版面
const VCANDLES = [candle(540, 100), candle(600, 103), candle(660, 99), candle(810, 102)];
const VCDP = { ah: 108, nh: 104, cdp: 101, nl: 98, al: 95, as_of_date: "2026-06-04" };
const VMA = { symbol: "2330", sma_5: 100.5, sma_20: 99.2, as_of_date: "2026-06-04" };
const renderVariant = (input: Parameters<typeof computeIntradayGeometry>[0]) =>
  renderToStaticMarkup(createElement(IntradayChartStatic, { ...input, geometry: computeIntradayGeometry(input) }));

it("snapshot variant — 量能關(volume:false,少了量能 pane)", () => {
  expect(renderVariant({
    candles: VCANDLES, prevClose: 100, cdp: VCDP, camarilla: null, ma: VMA,
    flags: { vwap: true, cdp: true, camarilla: false, volume: false, ma: true },
  })).toMatchSnapshot();
});

it("snapshot variant — 全 toggle 關(只剩價線+軸)", () => {
  expect(renderVariant({
    candles: VCANDLES, prevClose: 100, cdp: VCDP, camarilla: null, ma: VMA,
    flags: { vwap: false, cdp: false, camarilla: false, volume: false, ma: false },
  })).toMatchSnapshot();
});

it("snapshot variant — prevClose=null(baseline 改用首根 open)", () => {
  expect(renderVariant({
    candles: VCANDLES, prevClose: null, cdp: VCDP, camarilla: null, ma: VMA,
    flags: { vwap: true, cdp: true, camarilla: false, volume: true, ma: true },
  })).toMatchSnapshot();
});

it("snapshot variant — Camarilla 開(8 線、CDP/MA 關)", () => {
  expect(renderVariant({
    candles: VCANDLES, prevClose: 100, cdp: null, ma: null,
    camarilla: { h4: 106, h3: 104, h2: 102.5, h1: 101.5, l1: 99, l2: 98, l3: 96, l4: 94, as_of_date: "2026-06-04", prev_close: 100 },
    flags: { vwap: true, cdp: false, camarilla: true, volume: true, ma: false },
  })).toMatchSnapshot();
});

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

it("scale=1.6:最長 CDP label 從 padR 右緣 margin 起排,字級 24、向右展開(幾何位置)", () => {
  // 純 geometry 位置測試 —— 釘住 label 的「起排錨點」與「字級」這兩個程式碼決定、
  // 且可在無字型量測引擎下確定的事實。
  // 不宣稱「不超出畫布」:24px 下 6 字寬的實際像素端視 resvg+JhengHei 字型 metrics,
  // jsdom/vitest 無法量;保守上界(6×24×0.62≈90px > 可用 84px)甚至可能超界。
  // 真正的像素邊界把關交給盤中肉眼複核(設計 §6 / 計畫 Task 4),這裡只守落點與字級。
  // 384.5* 是 formatTickPrice 下最長的 label(100–500 元股,1 位小數 + *)。
  const candles = [candle(540, 384.5), candle(810, 384.5)];
  const cdp = { ah: 400, nh: 392, cdp: 384.5, nl: 376, al: 368, as_of_date: "2026-06-04" };
  const input = {
    candles, prevClose: 384.5, cdp, camarilla: null, ma: null,
    flags: { vwap: false, cdp: true, camarilla: false, volume: false, ma: false }, scale: 1.6,
  };
  const g = computeIntradayGeometry(input);
  const svg = renderToStaticMarkup(createElement(IntradayChartStatic, { ...input, geometry: g }));

  const cdpLabels = extractTextElements(svg).filter((el) => el.text.endsWith("*"));
  expect(cdpLabels.some((el) => el.text === "384.5*")).toBe(true); // 最長 6 字 label 真的被渲染
  for (const el of cdpLabels) {
    expect(el.textAnchor).toBe("start");                  // 向右展開,錨點是左緣
    expect(el.fontSize).toBe(24);                          // 15 * 1.6,跟著 fontScale 放大
    expect(el.x).toBe(CHART_W - g.padR + 6);               // 錨點 = 右側 margin 起排點(=736)
  }
  expect(g.padR).toBe(90);                                 // round(56 * 1.6),撐出右側 margin
});
