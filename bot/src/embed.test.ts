import { describe, it, expect } from "vitest";
import { buildReply } from "./embed";
import type { CdpLevels, MaLevels } from "../../frontend/src/lib/api";
import type { QuoteResp } from "./data";

const CDP_F: CdpLevels = { ah: 620, nh: 608, cdp: 592, nl: 576, al: 564, as_of_date: "2026-06-04" };
const MA_F: MaLevels = { symbol: "2330", sma_5: 593.5, sma_20: 588.0, as_of_date: "2026-06-04" };
const QUOTE_F: QuoteResp = {
  bids: [{ price: 599, size: 100 }, { price: 598.5, size: 50 }],
  asks: [{ price: 599.5, size: 80 }, { price: 600, size: 60 }],
  is_limit_up_bid: false, is_limit_up_ask: false,
  is_limit_down_bid: false, is_limit_down_ask: false,
};
const baseArgs = {
  symbol: "2330", name: "台積電",
  lastClose: 600, change: 12, changePct: 2.04,
  open: 590, high: 601, low: 588, vwap: 595, volume: 12000,
  cdp: CDP_F, ma: MA_F, quote: QUOTE_F, quotePng: Buffer.from([0x89, 0x51]) as Buffer | null, asOf: "13:30",
};

describe("buildReply — 五檔改圖 + 降級(spec §8 不讓整則炸)", () => {
  it("分時圖 + 五檔圖都在 → files 兩張、description 不再含文字五檔", () => {
    const r = buildReply({ ...baseArgs, png: Buffer.from([0x89, 0x50]) });
    expect(r.files).toHaveLength(2);                     // chart.png + quote.png
    const embed = r.embeds[0];
    expect(embed.data.description).toContain("600.00");  // 現價還在
    expect(embed.data.description).not.toContain("買盤"); // 文字五檔已移除
    const cdpField = (embed.data.fields ?? []).find((f) => f.name === "CDP");
    // CDP 5 條全標 *(功能 2,補滿覆蓋)
    expect(cdpField?.value).toContain("AH*");
    expect(cdpField?.value).toContain("NH*");
    expect(cdpField?.value).toContain("CDP*");
    expect(cdpField?.value).toContain("NL*");
    expect(cdpField?.value).toContain("AL*");
    const fieldNames = (embed.data.fields ?? []).map((f) => f.name);
    expect(fieldNames).not.toContain("委買 / 委賣(張)");  // 重複總量欄已移除
  });

  it("分時圖失敗(png=null)→ 只附五檔圖、embed 文字欄仍在", () => {
    const r = buildReply({ ...baseArgs, png: null });
    expect(r.files).toHaveLength(1);                     // 只剩 quote.png
    const embed = r.embeds[0];
    expect(embed.data.image).toBeUndefined();            // 沒有 setImage
    expect(embed.data.description).toContain("600.00");
  });

  it("五檔圖失敗(quotePng=null)→ 只附分時圖", () => {
    const r = buildReply({ ...baseArgs, png: Buffer.from([0x89, 0x50]), quotePng: null });
    expect(r.files).toHaveLength(1);                     // 只剩 chart.png
    expect(r.embeds[0].data.image?.url).toBe("attachment://chart.png");
  });

  it("兩張都失敗 → files 空、純文字 embed", () => {
    const r = buildReply({ ...baseArgs, png: null, quotePng: null });
    expect(r.files).toHaveLength(0);
    expect(r.embeds[0].data.description).toContain("600.00");
  });
});
