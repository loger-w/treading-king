import { describe, it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";
import { computeIndexGeometry, fmtIndex, fmtIndexVol, indexAmplitude, IndexIntradayStatic } from "./index-intraday-svg";
import type { IntradayCandle } from "./api";

function c(min: number, close: number, high = close, low = close, vol = 0): IntradayCandle {
  const hh = String(Math.floor(min / 60)).padStart(2, "0");
  const mm = String(min % 60).padStart(2, "0");
  return { date: `2026-06-09T${hh}:${mm}:00.000+08:00`, open: close, high, low, close, volume: vol, average: close };
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
});

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

describe("IndexIntradayStatic", () => {
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
  it("idPrefix 讓 clipPath id 唯一(並排不衝突)", () => {
    const candles = [c(540, 45000, 45010, 44990), c(600, 45200, 45210, 45190)];
    const input = { candles, prevClose: 45000 };
    const geometry = computeIndexGeometry(input);
    const a = renderToStaticMarkup(createElement(IndexIntradayStatic, { ...input, geometry, idPrefix: "IX0001" }));
    const b = renderToStaticMarkup(createElement(IndexIntradayStatic, { ...input, geometry, idPrefix: "IX0043" }));
    expect(a).toContain("idx-above-IX0001");
    expect(b).toContain("idx-above-IX0043");
  });
  it("空 candles 渲染回 null(不炸)", () => {
    const input = { candles: [] as IntradayCandle[], prevClose: 45000 };
    const svg = renderToStaticMarkup(
      createElement(IndexIntradayStatic, { ...input, geometry: computeIndexGeometry(input) }),
    );
    expect(svg).toBe("");
  });
});
