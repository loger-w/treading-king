import { describe, it, expect } from "vitest";
import { grossPnl, netPnl } from "./capital-pnl";

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
