import { describe, expect, it } from "vitest";
import { buildLadder, type MyOrderLot } from "./flash-ladder";

const noDepth = { bids: [], asks: [] };

describe("buildLadder 階梯生成", () => {
  it("以現價對齊 tick 為中心,高價在前(陣列頭),步進跨級距正確", () => {
    // center 49.9(tick 0.05),向上跨入 50+(tick 0.1):…50.1, 50.0, 49.95, 49.9, 49.85…
    const rows = buildLadder({ center: 49.9, reference: 49.9, ...noDepth, myOrders: [], rows: 3 });
    expect(rows.map((r) => r.price)).toEqual([50.1, 50.0, 49.95, 49.9, 49.85, 49.8, 49.75]);
    expect(rows[3].isCenter).toBe(true);
  });

  it("範圍夾在漲跌停之間:reference=100 → 不超過 110 / 不低於 90", () => {
    const rows = buildLadder({ center: 109.5, reference: 100, ...noDepth, myOrders: [], rows: 30 });
    expect(Math.max(...rows.map((r) => r.price))).toBeLessThanOrEqual(110);
    expect(Math.min(...rows.map((r) => r.price))).toBeGreaterThanOrEqual(90);
  });

  it("±5% 外 clickable=false(fat-finger 灰區);預設檔數必須蓋滿可點區", () => {
    // 不傳 rows 用預設 — 100 元股向下 tick=0.1,預設檔數不足會讓 95 根本不在階梯上
    const rows = buildLadder({ center: 100, reference: 100, ...noDepth, myOrders: [] });
    const at = (p: number) => rows.find((r) => r.price === p)!;
    expect(at(105).clickable).toBe(true);    // 恰在 +5% 邊界(含)
    expect(at(105.5).clickable).toBe(false); // 超過
    expect(at(95).clickable).toBe(true);
    expect(at(94.5).clickable).toBe(false);
  });

  it("五檔量對到價位列;範圍外為 null", () => {
    const rows = buildLadder({
      center: 100, reference: 100,
      bids: [{ price: 99.9, size: 45 }], asks: [{ price: 100.5, size: 88 }],
      myOrders: [], rows: 10,
    });
    expect(rows.find((r) => r.price === 99.9)!.buyVol).toBe(45);
    expect(rows.find((r) => r.price === 100.5)!.sellVol).toBe(88);
    expect(rows.find((r) => r.price === 101)!.sellVol).toBeNull();
  });

  it("我N聚合:同價多單張數加總、買賣分欄", () => {
    const my: MyOrderLot[] = [
      { price: 99.5, buySell: "B", lots: 2 },
      { price: 99.5, buySell: "B", lots: 3 },
      { price: 100.5, buySell: "S", lots: 5 },
    ];
    const rows = buildLadder({ center: 100, reference: 100, ...noDepth, myOrders: my, rows: 10 });
    expect(rows.find((r) => r.price === 99.5)!.myBuyLots).toBe(5);
    expect(rows.find((r) => r.price === 100.5)!.mySellLots).toBe(5);
  });

  it("reference=null → 用 center 估漲跌停,仍夾界", () => {
    const r = buildLadder({ center: 100, reference: null, ...noDepth, myOrders: [], rows: 50 });
    expect(r.every((row) => row.price <= 110 && row.price >= 90)).toBe(true);
  });
});
