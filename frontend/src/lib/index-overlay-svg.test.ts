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
  it("各算漲跌%(加權 +1%、櫃買約 -0.465%)", () => {
    const g = computeOverlayGeometry(A, B);
    expect(g.lines[0].lastPct).toBeCloseTo(1.0, 5);
    expect(g.lines[1].lastPct).toBeCloseTo(-0.4651, 3);
  });
  it("Y 範圍涵蓋 0% 與兩線極值", () => {
    const g = computeOverlayGeometry(A, B);
    expect(g.yMin).toBeLessThanOrEqual(-0.4651);
    expect(g.yMax).toBeGreaterThanOrEqual(1.0);
    expect(g.yMin).toBeLessThanOrEqual(0);
    expect(g.yMax).toBeGreaterThanOrEqual(0);
  });
  it("缺 prevClose 的 series → 該線 lastPct null、poly 空,另一線仍算", () => {
    const g = computeOverlayGeometry(A, { ...B, prevClose: null });
    expect(g.lines[0].lastPct).toBeCloseTo(1.0, 5);
    expect(g.lines[1].lastPct).toBeNull();
    expect(g.lines[1].poly).toBe("");
  });
  it("hover:pctByCodeAtMinute 取最近分鐘", () => {
    const g = computeOverlayGeometry(A, B);
    expect(g.pctByCodeAtMinute("IX0001", 600)).toBeCloseTo(1.0, 5);
    expect(g.pctByCodeAtMinute("IX0001", 540)).toBeCloseTo(0, 5);
  });
});
