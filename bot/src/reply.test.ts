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
  renderIndex: () => Buffer.from([0x89, 0x50]),
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
    if (s.empty && !s.isIndex) { expect(s.cdp).toEqual(CDP); expect(s.ma).toEqual(MA); }
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

describe("composeReply — 三則訊息(文字 / 分時圖 / 五檔圖)", () => {
  const slowOk = {
    empty: false as const, isIndex: false as const, name: "台積電", cdp: CDP, ma: MA, png: Buffer.from([0x89]) as Buffer | null,
    lastClose: 102, change: 2, changePct: 2, open: 100, high: 103, low: 99, vwap: 101, volume: 4000, asOf: "13:30",
  };
  const quotePng = Buffer.from([0x89, 0x51]);
  const filesOf = (m: { files?: readonly unknown[] }) => m.files ?? [];

  it("三者齊 → 三則:文字(無圖)+ 分時圖 + 五檔圖", () => {
    const msgs = composeReply("2330", slowOk, QUOTE, quotePng);
    expect(msgs).toHaveLength(3);
    expect(msgs[0].embeds).toHaveLength(1);     // 第一則:文字 embed
    expect(filesOf(msgs[0])).toHaveLength(0);   //   且不帶附件
    expect(filesOf(msgs[1])).toHaveLength(1);   // 第二則:分時圖
    expect(filesOf(msgs[2])).toHaveLength(1);   // 第三則:五檔圖
  });

  it("分時圖失敗(png=null)→ 兩則:文字 + 五檔圖(跳過分時圖)", () => {
    const msgs = composeReply("2330", { ...slowOk, png: null }, QUOTE, quotePng);
    expect(msgs).toHaveLength(2);
    expect(msgs[0].embeds).toHaveLength(1);     // 文字
    expect(filesOf(msgs[1])).toHaveLength(1);   // 五檔圖
  });

  it("五檔圖失敗(quotePng=null)→ 兩則:文字 + 分時圖", () => {
    const msgs = composeReply("2330", slowOk, QUOTE, null);
    expect(msgs).toHaveLength(2);
    expect(filesOf(msgs[1])).toHaveLength(1);   // 分時圖
  });

  it("兩圖都失敗 → 只剩一則文字", () => {
    const msgs = composeReply("2330", { ...slowOk, png: null }, null, null);
    expect(msgs).toHaveLength(1);
    expect(msgs[0].embeds).toHaveLength(1);
  });

  it("空盤前(empty)→ 一則純文字 content(含 CDP/MA5)、無圖", () => {
    const msgs = composeReply("2330", { empty: true as const, isIndex: false as const, cdp: CDP, ma: MA, name: "台積電", prevClose: 100 }, null, null);
    expect(msgs).toHaveLength(1);
    expect(msgs[0].content).toContain("無分時資料");
    expect(msgs[0].content).toContain("MA5");
    expect(filesOf(msgs[0])).toHaveLength(0);
  });
});

describe("指數精簡路徑", () => {
  const indexDeps: ReplyDeps = {
    ...okDeps,
    getCandles: async () => ({
      date: "2026-06-09", symbol: "IX0001",
      data: [
        { date: "2026-06-09T09:00:00.000+08:00", open: 45000, high: 45050, low: 44980, close: 45010, volume: 50_000_000_000, average: 0 },
        { date: "2026-06-09T13:30:00.000+08:00", open: 45200, high: 45260, low: 45100, close: 45231, volume: 60_000_000_000, average: 0 },
      ],
      prev_close: 45000,
    }),
    getCdp: async () => { throw new Error("指數不該抓 CDP"); },
    getMa: async () => { throw new Error("指數不該抓 MA"); },
    getName: async () => "加權指數",
    render: () => { throw new Error("指數應走 renderIndex"); },
    renderIndex: () => Buffer.from("PNG"),
  };

  it("走精簡路徑:isIndex、現價正確、有圖", async () => {
    const s = await loadSlow("IX0001", indexDeps);
    expect(s.empty).toBe(false);
    if (!s.empty && s.isIndex) {
      expect(s.isIndex).toBe(true);
      expect(s.lastClose).toBe(45231);
      expect(s.volume).toBe(110_000_000_000);          // 兩分鐘成交值加總
      expect(s.amplitude).toBeCloseTo(0.62, 2);          // (45260−44980)/45000×100
      expect(s.png).not.toBeNull();
    }
  });

  it("composeReply 指數:2 則(文字 + 走勢圖),不含五檔", async () => {
    const s = await loadSlow("IX0001", indexDeps);
    const msgs = composeReply("IX0001", s, null, null);
    expect(msgs).toHaveLength(2);
  });
});
