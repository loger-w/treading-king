import { describe, it, expect } from "vitest";
import { formatLadder, sumSize } from "./embed";

const lvl = (price: number, size: number) => ({ price, size });

describe("formatLadder", () => {
  it("賣5→買5 排列、量單位張、不足補 —、price=0 顯示市價", () => {
    const out = formatLadder(
      [lvl(634.5, 340), lvl(635.0, 210), lvl(635.5, 88)],  // bids: 買1..買3
      [lvl(636.0, 120), lvl(0, 0)],                          // asks: 賣1..賣2(賣2 鎖停=市價)
    );
    const lines = out.split("\n");
    expect(lines[0]).toContain("賣5"); expect(lines[0]).toContain("—");      // 賣5 缺檔
    expect(lines.some((l) => l.includes("市價"))).toBe(true);                 // 賣2 price=0 → 市價
    expect(lines.some((l) => l.includes("買1") && l.includes("634.50") && l.includes("340"))).toBe(true);
    expect(lines.some((l) => l.startsWith("───"))).toBe(true);
  });

  it("sumSize 加總五檔量", () => {
    expect(sumSize([{ price: 100, size: 3 }, { price: 200, size: 7 }])).toBe(10);
    expect(sumSize([])).toBe(0);
  });
});
