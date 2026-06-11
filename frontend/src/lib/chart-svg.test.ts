import { describe, it, expect } from "vitest";
import {
  scaleX_compressed,
  scaleY_clamped,
  computeMA,
} from "./chart-svg";

describe("scaleX_compressed (futures day+night)", () => {
  // 期貨交易日 = 前一天 15:00 → 當天 13:45
  // sessions: [{ start: "2026-05-24T15:00:00+08:00", end: "2026-05-25T05:00:00+08:00" },  // 夜盤 14h
  //           { start: "2026-05-25T08:45:00+08:00", end: "2026-05-25T13:45:00+08:00" }]   // 日盤 5h
  // gap 壓縮:gap (05:00→08:45 = 3h45m) 在視覺上佔極小寬度(用 sessions 總時長之 1%)
  const sessions = [
    { startIso: "2026-05-24T15:00:00+08:00", endIso: "2026-05-25T05:00:00+08:00" },
    { startIso: "2026-05-25T08:45:00+08:00", endIso: "2026-05-25T13:45:00+08:00" },
  ];
  const width = 1900;

  it("夜盤開始時 = 0", () => {
    expect(scaleX_compressed("2026-05-24T15:00:00+08:00", sessions, width)).toBeCloseTo(0, 0);
  });

  it("日盤結束時 = width", () => {
    expect(scaleX_compressed("2026-05-25T13:45:00+08:00", sessions, width)).toBeCloseTo(width, 0);
  });

  it("夜盤中間點", () => {
    // 夜盤 14h 一半 = 7h,夜盤結束於 1400px 左右(width × 14/(14+5+gap_small))
    const x = scaleX_compressed("2026-05-24T22:00:00+08:00", sessions, width);
    expect(x).toBeGreaterThan(600);
    expect(x).toBeLessThan(800);
  });

  it("休市時段內(gap):落在 sessions 之間,回 NaN", () => {
    expect(Number.isNaN(scaleX_compressed("2026-05-25T07:00:00+08:00", sessions, width))).toBe(true);
  });
});

describe("scaleY_clamped", () => {
  it("min 對應 height", () => {
    expect(scaleY_clamped(17000, 17000, 17100, 400)).toBeCloseTo(400);
  });
  it("max 對應 0", () => {
    expect(scaleY_clamped(17100, 17000, 17100, 400)).toBeCloseTo(0);
  });
  it("中間值線性內插", () => {
    expect(scaleY_clamped(17050, 17000, 17100, 400)).toBeCloseTo(200);
  });
});

describe("computeMA", () => {
  it("不夠期數的位置為 NaN", () => {
    const closes = [100, 102, 101];
    const ma = computeMA(closes, 5);
    expect(ma.every(Number.isNaN)).toBe(true);
  });
  it("MA-3 計算正確", () => {
    const closes = [100, 110, 120, 130, 140];
    const ma = computeMA(closes, 3);
    expect(Number.isNaN(ma[0])).toBe(true);
    expect(Number.isNaN(ma[1])).toBe(true);
    expect(ma[2]).toBeCloseTo(110);
    expect(ma[3]).toBeCloseTo(120);
    expect(ma[4]).toBeCloseTo(130);
  });
});
