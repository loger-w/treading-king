import { describe, it, expect } from "vitest";
import { grossPnl, netPnl, snapshotPrices } from "./capital-pnl";

describe("capital-pnl", () => {
  it("gross = qty*1000*(price-avg)", () => {
    expect(grossPnl(5, 575, 590)).toBe(75000);
  });
  it("short position gross", () => {
    expect(grossPnl(-2, 100, 95)).toBe(10000);
  });
  it("net subtracts entry+exit fee and sell tax", () => {
    // qty5 avg575 cur590 feeRate0.001425 taxRate0.003
    // gross=75000; entryFee=round(5*1000*575*0.001425)=4097
    // exitFee=round(5*1000*590*0.001425)=4204; tax=round(5*1000*590*0.003)=8850
    // net = 75000-4097-4204-8850 = 57849
    expect(netPnl(5, 575, 590, 0.001425, 0.003)).toBe(57849);
  });
  it("null price -> 0", () => {
    expect(grossPnl(5, 575, null)).toBe(0);
    expect(netPnl(5, 575, null, 0.001425, 0.003)).toBe(0);
  });
});

describe("snapshotPrices 快照價全量重建", () => {
  it("每輪回傳全新 map(凍結 bug 回歸:舊值不得殘留)、null 略過", () => {
    // 舊 bug:「已有值就不蓋」合併讓第一輪快照價永久凍結,損益不動
    const r1 = snapshotPrices([{ symbol: "2330", last_price: 100 }, { symbol: "3357", last_price: null }]);
    expect(r1).toEqual({ "2330": 100 });
    const r2 = snapshotPrices([{ symbol: "2330", last_price: 101.5 }]);
    expect(r2).toEqual({ "2330": 101.5 });
  });
});
