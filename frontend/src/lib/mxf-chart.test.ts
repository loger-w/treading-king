import { describe, test, expect } from "vitest";
import { dayOpenBaseline } from "./mxf-chart";
import type { MXFCandle } from "../lib/api";

function makeCandle(date: string, open: number): MXFCandle {
  return { date, open, high: open + 1, low: open - 1, close: open, volume: 100, average: open };
}

describe("dayOpenBaseline", () => {
  const now = new Date("2026-05-26T10:00:00+08:00");

  test("returns null for empty candles", () => {
    expect(dayOpenBaseline([], now)).toBeNull();
  });

  test("returns null when no candle at or after 08:45 today", () => {
    const candles = [makeCandle("2026-05-26T03:00:00+08:00", 100)];
    expect(dayOpenBaseline(candles, now)).toBeNull();
  });

  test("returns first 08:45+ candle.open for today", () => {
    const candles = [
      makeCandle("2026-05-26T03:00:00+08:00", 100),
      makeCandle("2026-05-26T08:45:00+08:00", 105),
      makeCandle("2026-05-26T09:00:00+08:00", 106),
    ];
    expect(dayOpenBaseline(candles, now)).toBe(105);
  });

  test("returns today's day open, ignores yesterday's", () => {
    const candles = [
      makeCandle("2026-05-25T08:45:00+08:00", 95),
      makeCandle("2026-05-26T08:45:00+08:00", 105),
    ];
    expect(dayOpenBaseline(candles, now)).toBe(105);
  });
});

import { computeNewViewRange } from "./mxf-chart";

describe("computeNewViewRange", () => {
  test("zooms in keeping anchor at center", () => {
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

  test("zooms out keeping anchor at right edge", () => {
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

  test("clamps min visible to 5", () => {
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

  test("clamps max visible by minCandlePx", () => {
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

  test("clamps when newStart would go negative", () => {
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

  test("candlesLen < MIN_VISIBLE 時 endIdx 不可越界(夜盤剛開盤僅 2-3 根)", () => {
    // MIN_VISIBLE 抬升不可勝過 candlesLen 上限,否則回傳的 ViewRange
    // 違反 endIdx <= candlesLen-1 的 invariant,下游 slice 全靠巧合夾住
    const result = computeNewViewRange({
      prevRange: { startIdx: 0, endIdx: 2 },  // 全部 3 根都在視窗
      mouseRatio: 0.5,
      deltaY: -100,  // zoom in
      candlesLen: 3,
      innerW: 888,
      minCandlePx: 6,
    });
    expect(result.startIdx).toBe(0);
    expect(result.endIdx).toBe(2);
  });
});

import { pickInterval } from "./mxf-chart";

describe("pickInterval", () => {
  test("picks 5m for short spans (<= 35min)", () => {
    expect(pickInterval(30)).toBe(5);
  });
  test("picks 15m for spans 35-105min", () => {
    expect(pickInterval(60)).toBe(15);
    expect(pickInterval(100)).toBe(15);
  });
  test("picks 30m for spans 105-210min", () => {
    expect(pickInterval(180)).toBe(30);
  });
  test("picks 60m for spans 210-420min", () => {
    expect(pickInterval(300)).toBe(60);
  });
  test("picks 120m for spans 420-840min", () => {
    expect(pickInterval(600)).toBe(120);
  });
  test("picks 240m for spans > 840min", () => {
    expect(pickInterval(2000)).toBe(240);
  });
});
