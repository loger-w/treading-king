import { describe, expect, it } from "vitest";
import { isTickAligned, tickSize, roundToTick, limitUp, limitDown } from "./tick";

describe("isTickAligned 委託價檔位驗證", () => {
  // 前端不驗的話,off-tick 限價(105.13 / 1003)會通過二次確認,
  // 到券商/交易所才被退單,白費一輪失敗往返
  it.each([
    [105.13, false], [105.5, true], [105.0, true],   // 100–500 tick 0.5
    [1003, false], [1005, true],                      // >=1000 tick 5
    [9.99, true], [9.995, false],                     // <10 tick 0.01
    [23.45, true], [23.47, false],                    // 10–50 tick 0.05
  ])("price %f → %s", (price, ok) => {
    expect(isTickAligned(price)).toBe(ok);
  });
});

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

describe("limitUp/limitDown 半分尾數(deci-cent)不可先四捨五入", () => {
  // ref×1.1 / ×0.9 合法地帶 0.1 分尾數(9.05×1.1=9.955),先 round 到整數分
  // 等於把該被 floor/ceil 處理的尾數提前進位,算出的漲跌停會超出法定 ±10%
  it("limitUp(9.05) = 9.95(非 9.96)", () => expect(limitUp(9.05)).toBe(9.95));
  it("limitUp(5.55) = 6.10(非 6.11)", () => expect(limitUp(5.55)).toBe(6.1));
  it("limitUp(10.45) = 11.45(非 11.50)", () => expect(limitUp(10.45)).toBe(11.45));
  it("limitDown(10.45) = 9.41(非 9.40)", () => expect(limitDown(10.45)).toBe(9.41));
});

describe("roundToTick 跨級距與浮點", () => {
  it("向下:53.94 → 53.9(tick 0.1)", () => expect(roundToTick(53.94, "down")).toBe(53.9));
  it("向上:53.91 → 54.0", () => expect(roundToTick(53.91, "up")).toBe(54));
  it("浮點陷阱:53.9 down 不可誤捨成 53.8", () => expect(roundToTick(53.9, "down")).toBe(53.9));
});

describe("全價位掃描:漲跌停絕不超出法定 ±10% 且對齊 tick", () => {
  // 漲跌停餵真錢下單路徑(漲停快捷鈕/市價閘用估價/閃電梯上下界),
  // 算超出法定區間會被券商拒單。以 deci-cent 整數重算精確基準逐一比對。
  const bands: ReadonlyArray<readonly [number, number, number]> = [
    // [起, 迄, step],單位:分
    [1, 999, 1],        // < 10 元,tick 0.01
    [1000, 4995, 5],    // 10–50,tick 0.05
    [5000, 9990, 10],   // 50–100,tick 0.10
    [10000, 49950, 50], // 100–500,tick 0.50
    [50000, 99900, 100], // 500–1000,tick 1.00
  ];
  const tickDeciOf = (deci: number) =>
    deci < 10_000 ? 10 : deci < 50_000 ? 50 : deci < 100_000 ? 100
      : deci < 500_000 ? 500 : deci < 1_000_000 ? 1000 : 5000;

  it("0.01–999 元所有合法參考價,對照精確整數基準 0 誤差", () => {
    const mismatches: string[] = [];
    for (const [from, to, step] of bands) {
      for (let c = from; c <= to; c += step) {
        const upDeci = c * 11; // ref(分)×1.1 在 deci-cent 下是精確整數
        const upTick = tickDeciOf(upDeci);
        const expectedUp = Math.floor(upDeci / upTick) * upTick;
        const gotUp = Math.round(limitUp(c / 100) * 1000);
        if (gotUp !== expectedUp) mismatches.push(`limitUp(${c / 100}) = ${gotUp / 1000} ≠ ${expectedUp / 1000}`);

        const downDeci = c * 9;
        const downTick = tickDeciOf(downDeci);
        const expectedDown = Math.ceil(downDeci / downTick) * downTick;
        const gotDown = Math.round(limitDown(c / 100) * 1000);
        if (gotDown !== expectedDown) mismatches.push(`limitDown(${c / 100}) = ${gotDown / 1000} ≠ ${expectedDown / 1000}`);
      }
    }
    expect(mismatches.slice(0, 10)).toEqual([]);
    expect(mismatches.length).toBe(0);
  });
});
