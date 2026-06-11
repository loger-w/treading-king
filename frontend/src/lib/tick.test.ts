import { describe, expect, it } from "vitest";
import { tickSize, roundToTick, limitUp, limitDown } from "./tick";

describe("tickSize 台股六級距", () => {
  it.each([
    [5, 0.01], [9.99, 0.01],
    [10, 0.05], [49.95, 0.05],
    [50, 0.1], [99.9, 0.1],
    [100, 0.5], [499.5, 0.5],
    [500, 1], [999, 1],
    [1000, 5], [1500, 5],
  ])("price %f → tick %f", (price, tick) => {
    expect(tickSize(price)).toBe(tick);
  });
});

describe("limitUp(對齊後端 cdp.limit_up_price 既有測例)", () => {
  it("整數對齊:100 → 110", () => expect(limitUp(100)).toBe(110));
  it("尾數捨去不超 +10%:10.05 → 11.05(非 11.06)", () => expect(limitUp(10.05)).toBe(11.05));
  it("以漲停價級距取 tick:49 → 53.9", () => expect(limitUp(49)).toBe(53.9));
  it("千元股 tick=5:1000 → 1100", () => expect(limitUp(1000)).toBe(1100));
  it("低價股:5 → 5.5", () => expect(limitUp(5)).toBe(5.5));
});

describe("limitDown(向上取,不超 -10%)", () => {
  it("整數對齊:100 → 90", () => expect(limitDown(100)).toBe(90));
  it("尾數進位:10.05 → 9.05(9.045 向上取至 tick 0.01)", () => expect(limitDown(10.05)).toBe(9.05));
  it("千元股:1000 → 900", () => expect(limitDown(1000)).toBe(900));
});

describe("roundToTick 跨級距與浮點", () => {
  it("向下:53.94 → 53.9(tick 0.1)", () => expect(roundToTick(53.94, "down")).toBe(53.9));
  it("向上:53.91 → 54.0", () => expect(roundToTick(53.91, "up")).toBe(54));
  it("浮點陷阱:53.9 down 不可誤捨成 53.8", () => expect(roundToTick(53.9, "down")).toBe(53.9));
});
