import { describe, test, expect } from "vitest";
import {
  MARKET_OPEN_MIN,
  MARKET_CLOSE_MIN,
  TRADING_MINUTES,
  minuteOfDay,
} from "./intraday-time";

describe("intraday-time constants", () => {
  test("9:00 = 540 分", () => {
    expect(MARKET_OPEN_MIN).toBe(540);
  });
  test("13:30 = 810 分", () => {
    expect(MARKET_CLOSE_MIN).toBe(810);
  });
  test("交易窗 = 270 分", () => {
    expect(TRADING_MINUTES).toBe(270);
  });
});

describe("minuteOfDay", () => {
  test("9:00:00+08:00 → 540", () => {
    expect(minuteOfDay("2026-05-22T09:00:00.000+08:00")).toBe(540);
  });
  test("9:01:30+08:00 → 541 (秒/毫秒忽略)", () => {
    expect(minuteOfDay("2026-05-22T09:01:30.000+08:00")).toBe(541);
  });
  test("13:30:00+08:00 → 810", () => {
    expect(minuteOfDay("2026-05-22T13:30:00.000+08:00")).toBe(810);
  });
  test("UTC 01:00 = 台北 09:00 → 540 (跨時區 ISO 也要正確)", () => {
    expect(minuteOfDay("2026-05-22T01:00:00.000Z")).toBe(540);
  });
  test("8:30:00+08:00 → 510 (試撮時段也要算對)", () => {
    expect(minuteOfDay("2026-05-22T08:30:00.000+08:00")).toBe(510);
  });
});
