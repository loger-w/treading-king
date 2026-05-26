import { describe, it, expect } from "vitest";
import { dayOpenBaseline } from "./mxf-chart";
import type { MXFCandle } from "../lib/api";

function makeCandle(date: string, open: number): MXFCandle {
  return { date, open, high: open + 1, low: open - 1, close: open, volume: 100, average: open };
}

describe("dayOpenBaseline", () => {
  const now = new Date("2026-05-26T10:00:00+08:00");

  it("returns null for empty candles", () => {
    expect(dayOpenBaseline([], now)).toBeNull();
  });

  it("returns null when no candle at or after 08:45 today", () => {
    const candles = [makeCandle("2026-05-26T03:00:00+08:00", 100)];
    expect(dayOpenBaseline(candles, now)).toBeNull();
  });

  it("returns first 08:45+ candle.open for today", () => {
    const candles = [
      makeCandle("2026-05-26T03:00:00+08:00", 100),
      makeCandle("2026-05-26T08:45:00+08:00", 105),
      makeCandle("2026-05-26T09:00:00+08:00", 106),
    ];
    expect(dayOpenBaseline(candles, now)).toBe(105);
  });

  it("returns today's day open, ignores yesterday's", () => {
    const candles = [
      makeCandle("2026-05-25T08:45:00+08:00", 95),
      makeCandle("2026-05-26T08:45:00+08:00", 105),
    ];
    expect(dayOpenBaseline(candles, now)).toBe(105);
  });
});
