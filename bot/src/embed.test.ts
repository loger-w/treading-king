import { describe, it, expect } from "vitest";
import { formatLadder, sumSize, buildReply } from "./embed";
import type { CdpLevels, MaLevels } from "../../frontend/src/lib/api";
import type { QuoteResp } from "./data";

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

const CDP_F: CdpLevels = { ah: 620, nh: 608, cdp: 592, nl: 576, al: 564, as_of_date: "2026-06-04" };
const MA_F: MaLevels = { symbol: "2330", sma_5: 593.5, sma_20: 588.0, as_of_date: "2026-06-04" };
const QUOTE_F: QuoteResp = {
  bids: [{ price: 599, size: 100 }, { price: 598.5, size: 50 }],
  asks: [{ price: 599.5, size: 80 }, { price: 600, size: 60 }],
  is_limit_up_bid: false, is_limit_up_ask: false,
  is_limit_down_bid: false, is_limit_down_ask: false,
};
// png 故意不放;各測試自行帶 png:Buffer 或 png:null
const baseArgs = {
  symbol: "2330", name: "台積電",
  lastClose: 600, change: 12, changePct: 2.04,
  open: 590, high: 601, low: 588, vwap: 595, volume: 12000,
  cdp: CDP_F, ma: MA_F, quote: QUOTE_F, asOf: "13:30",
};

describe("buildReply — 降級 fallback(review #1 / spec §8 不讓整則炸)", () => {
  it("產圖失敗(png=null)仍回含現價/五檔/CDP/MA 的 embed、且不附圖", () => {
    const r = buildReply({ ...baseArgs, png: null });
    expect(r.files).toHaveLength(0);                     // 不附圖
    const embed = r.embeds[0];
    expect(embed.data.image).toBeUndefined();            // embed 不設圖
    expect(embed.data.description).toContain("600.00");  // 現價還在
    expect(embed.data.description).toContain("買1");      // 五檔還在
    const fieldNames = (embed.data.fields ?? []).map((f) => f.name);
    expect(fieldNames).toContain("CDP");                 // CDP 還在
    expect(fieldNames).toContain("均線");                 // MA 還在
  });

  it("五檔失敗(quote=null)仍回含圖/現價/CDP/MA 的 embed、五檔區降級且不丟例外", () => {
    const r = buildReply({ ...baseArgs, png: Buffer.from([0x89, 0x50]), quote: null });
    const embed = r.embeds[0];
    expect(r.files).toHaveLength(1);                     // 圖還在
    expect(embed.data.description).toContain("600.00");  // 現價還在
    const fieldNames = (embed.data.fields ?? []).map((f) => f.name);
    expect(fieldNames).toContain("CDP");                 // CDP 還在
    expect(fieldNames).toContain("均線");                 // MA 還在
  });
});
