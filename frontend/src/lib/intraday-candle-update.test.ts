import { describe, test, expect } from "vitest";
import { resolveCandleUpdate } from "./intraday-candle-update";
import type { IntradayCandle } from "./api";

function candle(close: number): IntradayCandle {
  return {
    date: "2026-06-08T09:00:00.000+08:00",
    open: close, high: close, low: close, close, volume: 0, average: close,
  };
}

describe("resolveCandleUpdate", () => {
  test("過期回應(發起時的 symbol 已非當前選中)→ null", () => {
    // 快速切股:點 A(慢)後立刻點 B,A 的回應現在才回來。
    // 若不丟棄,A 的資料會蓋掉畫面上的 B → 顯示錯誤股票 / 把 B 清成載入中。
    const data = [candle(100)];
    expect(resolveCandleUpdate("A", "B", data)).toBeNull();
  });

  test("空陣列不覆蓋已顯示的圖 → null", () => {
    // 富邦對某些股票/時段會回空;輪詢拿到空時不可清掉已經畫好的圖
    // (對應「同一檔有時好有時壞」)。
    expect(resolveCandleUpdate("2330", "2330", [])).toBeNull();
  });

  test("缺 data 欄位(undefined)不覆蓋已顯示的圖 → null", () => {
    // 後端 candles.py 對富邦回 None 時回的 dict 缺 data 欄位 → 前端拿到 undefined。
    expect(resolveCandleUpdate("2330", "2330", undefined)).toBeNull();
  });

  test("當前 symbol 且有資料 → 回傳該資料", () => {
    const data = [candle(600), candle(601)];
    expect(resolveCandleUpdate("2330", "2330", data)).toBe(data);
  });
});
