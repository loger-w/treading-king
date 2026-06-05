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
