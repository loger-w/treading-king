import { describe, it, expect } from "vitest";
import { renderChartPng } from "./render";
import type { IntradayCandle, CdpLevels, MaLevels } from "../../frontend/src/lib/api";

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
