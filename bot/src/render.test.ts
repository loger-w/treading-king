import { describe, it, expect } from "vitest";
import { renderChartPng, safeRender, renderQuotePng, buildChartSvg, fitTitle } from "./render";
import type { IntradayCandle, CdpLevels, MaLevels } from "../../frontend/src/lib/api";
import type { QuoteResp } from "./data";

// 輔助:建立一根分時 candle(時間格式對齊後端 ISO+08:00)
function candle(hourMin: number, close: number, volume = 1000): IntradayCandle {
  const hh = String(Math.floor(hourMin / 60)).padStart(2, "0");
  const mm = String(hourMin % 60).padStart(2, "0");
  return {
    date: `2026-06-05T${hh}:${mm}:00.000+08:00`,
    open: close,
    high: close + 1,
    low: close - 1,
    close,
    volume,
    average: close + 0.5,
  };
}

const CANDLES: IntradayCandle[] = [
  candle(540, 590),   // 09:00
  candle(600, 595),   // 10:00
  candle(660, 588),   // 11:00
  candle(720, 592),   // 12:00
  candle(780, 597),   // 13:00
  candle(810, 600),   // 13:30
];

const CDP: CdpLevels = {
  ah: 620, nh: 608, cdp: 592, nl: 576, al: 564,
  as_of_date: "2026-06-04",
};

const MA: MaLevels = {
  symbol: "2330",
  sma_5: 593.5,
  sma_20: 588.0,
  as_of_date: "2026-06-04",
};

describe("renderChartPng — render pipeline smoke test", () => {
  it("回傳有效的 PNG(magic bytes + size > 1000)", () => {
    const png = renderChartPng({
      candles: CANDLES,
      prevClose: 588,
      cdp: CDP,
      camarilla: null,
      ma: MA,
      flags: { vwap: true, cdp: true, camarilla: false, volume: true, ma: true },
      symbol: "2330",
      name: "台積電",
      lastClose: 600,
      change: 12,
      changePct: 2.04,
    });

    // 確認是 Buffer 且夠大(一張放大 2x 的 PNG 必然遠超 1000 bytes)
    expect(png).toBeInstanceOf(Buffer);
    expect(png.length).toBeGreaterThan(1000);

    // 確認 PNG magic bytes:89 50 4E 47 0D 0A 1A 0A
    const MAGIC = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
    expect(png.subarray(0, 8)).toEqual(MAGIC);
  });
});

describe("safeRender — 產圖失敗降級(review #1 / spec §8 不讓整則炸)", () => {
  it("render 丟錯時回 null(吞例外),讓上層退純文字 embed", () => {
    expect(safeRender(() => { throw new Error("resvg crash / 缺字型"); })).toBeNull();
  });

  it("render 正常時原樣回傳 Buffer", () => {
    const b = Buffer.from([0x89, 0x50]);
    expect(safeRender(() => b)).toBe(b);
  });
});

describe("buildChartSvg — 字級放大 + 標題帶", () => {
  const base = {
    candles: CANDLES, prevClose: 588, cdp: CDP, camarilla: null, ma: MA,
    flags: { vwap: true, cdp: true, camarilla: false, volume: true, ma: true },
    symbol: "2330", name: "台積電", lastClose: 600, change: 12, changePct: 2.04,
  };

  it("圖內字級放大到 24(共用層 scale 1.6)", () => {
    expect(buildChartSvg(base)).toContain('font-size="24"');
  });

  it("標題帶字級放大到 33(150%)", () => {
    expect(buildChartSvg(base)).toContain('font-size="33"');
  });

  it("含量時 viewBox 高度 = TOTAL_H(748) + TITLE_H(58) = 806", () => {
    expect(buildChartSvg(base)).toContain('height="806"');
  });
});

describe("fitTitle — 超長股名截斷,避免撞右側現價", () => {
  it("一般長度原樣保留", () => {
    expect(fitTitle("2330", "台積電")).toBe("2330 台積電");
  });
  it("超長截斷補 …,長度受限", () => {
    const out = fitTitle("00940", "元大臺灣價值高息成分股ETF基金");
    expect(out.endsWith("…")).toBe(true);
    expect([...out].length).toBeLessThanOrEqual(14);
  });
  it("name 為 null 只回代號", () => {
    expect(fitTitle("2330", null)).toBe("2330");
  });
});

describe("renderQuotePng — 五檔圖 pipeline smoke test", () => {
  it("回傳有效 PNG(magic bytes + size > 1000)", () => {
    const quote: QuoteResp = {
      bids: [{ price: 384, size: 5 }, { price: 383.5, size: 1 }],
      asks: [{ price: 385, size: 13 }, { price: 386, size: 1 }],
      is_limit_up_bid: false, is_limit_up_ask: false,
      is_limit_down_bid: false, is_limit_down_ask: false,
    };
    const png = renderQuotePng(quote);
    expect(png).toBeInstanceOf(Buffer);
    expect(png.length).toBeGreaterThan(1000);
    const MAGIC = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
    expect(png.subarray(0, 8)).toEqual(MAGIC);
  });
});
