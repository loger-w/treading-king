import { describe, it, expect } from "vitest";
import { loadSlow, composeReply, type ReplyDeps } from "./reply";
import type { IntradayCandle, CdpLevels, MaLevels } from "../../frontend/src/lib/api";
import type { QuoteResp } from "./data";

function candle(hourMin: number, close: number, volume = 1000): IntradayCandle {
  const hh = String(Math.floor(hourMin / 60)).padStart(2, "0");
  const mm = String(hourMin % 60).padStart(2, "0");
  return { date: `2026-06-05T${hh}:${mm}:00.000+08:00`, open: close, high: close + 1, low: close - 1, close, volume, average: close + 0.5 };
}

const CANDLES = [candle(540, 100), candle(600, 103), candle(660, 99), candle(810, 102)];
const CDP: CdpLevels = { ah: 108, nh: 104, cdp: 101, nl: 98, al: 95, as_of_date: "2026-06-04" };
const MA: MaLevels = { symbol: "2330", sma_5: 100.5, sma_20: 99.2, as_of_date: "2026-06-04" };
const QUOTE: QuoteResp = {
  bids: [{ price: 101, size: 100 }], asks: [{ price: 102, size: 80 }],
  is_limit_up_bid: false, is_limit_up_ask: false, is_limit_down_bid: false, is_limit_down_ask: false,
};

const okDeps: ReplyDeps = {
  getCandles: async () => ({ date: "2026-06-05", symbol: "2330", data: CANDLES, prev_close: 100 }),
  getCdp: async () => CDP,
  getMa: async () => MA,
  getName: async () => "台積電",
  render: () => Buffer.from([0x89, 0x50]),
};

describe("loadSlow — orchestration 降級(review #5)", () => {
  it("產圖丟錯 → png=null(safeRender 吞例外,讓上層退純文字)", async () => {
    const s = await loadSlow("2330", { ...okDeps, render: () => { throw new Error("resvg crash"); } });
    expect(s.empty).toBe(false);
    if (!s.empty) expect(s.png).toBeNull();
  });

  it("無分時資料(空盤前)→ empty=true,仍帶 cdp/ma 供降級顯示", async () => {
    const s = await loadSlow("2330", {
      ...okDeps,
      getCandles: async () => ({ date: "2026-06-05", symbol: "2330", data: [], prev_close: 100 }),
    });
    expect(s.empty).toBe(true);
    if (s.empty) { expect(s.cdp).toEqual(CDP); expect(s.ma).toEqual(MA); }
  });

  it("推播分時圖不畫 MA 線:render 收到 flags.ma===false(均線改由 embed 文字呈現)", async () => {
    const seenFlags: { ma: boolean; vwap: boolean; cdp: boolean; volume: boolean }[] = [];
    await loadSlow("2330", {
      ...okDeps,
      render: (input) => { seenFlags.push(input.flags); return Buffer.from([0x89]); },
    });
    expect(seenFlags).toHaveLength(1);
    expect(seenFlags[0].ma).toBe(false);    // MA5/MA20 兩條線不畫進圖
    expect(seenFlags[0].vwap).toBe(true);   // 其餘圖層不動,確認只關了 MA
    expect(seenFlags[0].cdp).toBe(true);
    expect(seenFlags[0].volume).toBe(true);
  });
});

describe("composeReply — 組裝降級(五檔改圖)", () => {
  const slowOk = {
    empty: false as const, name: "台積電", cdp: CDP, ma: MA, png: Buffer.from([0x89]) as Buffer | null,
    lastClose: 102, change: 2, changePct: 2, open: 100, high: 103, low: 99, vwap: 101, volume: 4000, asOf: "13:30",
  };
  const quotePng = Buffer.from([0x89, 0x51]);

  it("分時圖 + 五檔圖都在 → files 兩張", () => {
    const r = composeReply("2330", slowOk, QUOTE, quotePng);
    expect(r.files).toHaveLength(2);
  });

  it("分時圖失敗(png=null)→ 只附五檔圖、描述不再含文字五檔", () => {
    const r = composeReply("2330", { ...slowOk, png: null }, QUOTE, quotePng);
    expect(r.files).toHaveLength(1);
    expect((r.embeds![0] as { data: { description?: string } }).data.description).not.toContain("買盤");
  });

  it("空盤前(empty)→ 回純文字 content(含 CDP/MA5)、不附圖", () => {
    const r = composeReply("2330", { empty: true as const, cdp: CDP, ma: MA, name: "台積電", prevClose: 100 }, null, null);
    expect(r.content).toContain("無分時資料");
    expect(r.content).toContain("MA5");
    expect(r.files ?? []).toHaveLength(0);
  });

  it("五檔抓不到(quote=null, quotePng=null)→ 仍回含分時圖的 embed", () => {
    const r = composeReply("2330", slowOk, null, null);
    expect(r.files).toHaveLength(1);
  });
});
