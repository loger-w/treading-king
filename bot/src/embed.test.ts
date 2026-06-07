import { describe, it, expect } from "vitest";
import { buildReply, imageMessage } from "./embed";
import type { CdpLevels, MaLevels } from "../../frontend/src/lib/api";
import type { QuoteResp } from "./data";

const CDP_F: CdpLevels = { ah: 620, nh: 608, cdp: 592, nl: 576, al: 564, as_of_date: "2026-06-04" };
const MA_F: MaLevels = { symbol: "2330", sma_5: 593.5, sma_20: 588.0, as_of_date: "2026-06-04" };
const QUOTE_F: QuoteResp = {
  bids: [{ price: 599, size: 100 }], asks: [{ price: 599.5, size: 80 }],
  is_limit_up_bid: false, is_limit_up_ask: false, is_limit_down_bid: false, is_limit_down_ask: false,
};
const baseArgs = {
  symbol: "2330", name: "台積電",
  lastClose: 600, change: 12, changePct: 2.04,
  open: 590, high: 601, low: 588, vwap: 595, volume: 12000,
  cdp: CDP_F, ma: MA_F, quote: QUOTE_F, asOf: "13:30",
};

describe("buildReply — 文字訊息那則(不含圖,圖各自獨立一則)", () => {
  it("純文字 embed:現價/CDP(5 條全標*)/均線都在、不內嵌圖、不帶 files", () => {
    const r = buildReply(baseArgs);
    expect(r.embeds).toHaveLength(1);
    const embed = r.embeds[0];
    expect(embed.data.description).toContain("600.00");      // 現價
    expect(embed.data.image).toBeUndefined();                // 不內嵌圖(分時圖改各自一則)
    expect((r as { files?: unknown[] }).files ?? []).toHaveLength(0);  // 文字那則不帶附件
    const cdpField = (embed.data.fields ?? []).find((f) => f.name === "CDP");
    for (const k of ["AH*", "NH*", "CDP*", "NL*", "AL*"]) expect(cdpField?.value).toContain(k);
    const fieldNames = (embed.data.fields ?? []).map((f) => f.name);
    expect(fieldNames).toContain("均線");
    expect(fieldNames).not.toContain("委買 / 委賣(張)");      // 總量在五檔圖裡,不重複
    expect(embed.data.description).not.toContain("買盤");     // 無文字五檔
  });

  it("鎖漲停 → description 帶 🔺鎖漲停", () => {
    const r = buildReply({ ...baseArgs, quote: { ...QUOTE_F, is_limit_up_ask: true } });
    expect(r.embeds[0].data.description).toContain("鎖漲停");
  });

  it("imageMessage:頂層附件那則(files 一張、無 embed)", () => {
    const m = imageMessage(Buffer.from([0x89, 0x50]), "chart.png");
    expect(m.files).toHaveLength(1);
    expect((m as { embeds?: unknown[] }).embeds ?? []).toHaveLength(0);
  });
});
