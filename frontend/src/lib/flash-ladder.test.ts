import { describe, expect, it } from "vitest";
import { buildLadder, splitMyLots, type MyOrderLot, type MyOrderSource } from "./flash-ladder";

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

describe("splitMyLots 方格(active)/黃括號(fills)來源拆分", () => {
  const o = (over: Partial<MyOrderSource>): MyOrderSource => ({
    price: 100, buy_sell: "B", order_qty: 5, filled_qty: 0, actionable: true, ...over,
  });

  it("活單:未成交張數進 active、已成交張數進 fills(掛5成4 → 方格1+括號4)", () => {
    const { active, fills } = splitMyLots([o({ order_qty: 5, filled_qty: 4 })]);
    expect(active).toEqual([{ price: 100, buySell: "B", lots: 1 }]);
    expect(fills).toEqual([{ price: 100, buySell: "B", lots: 4 }]);
  });

  it("已完成單不進 active;成交過的進 fills(黃括號整日留存)、純刪單不留痕", () => {
    const fullyFilled = o({ actionable: false, order_qty: 5, filled_qty: 5 });
    const cancelledNoFill = o({ actionable: false, order_qty: 3, filled_qty: 0 });
    const { active, fills } = splitMyLots([fullyFilled, cancelledNoFill]);
    expect(active).toEqual([]);
    expect(fills).toEqual([{ price: 100, buySell: "B", lots: 5 }]);
  });

  it("部分成交後刪掉的單:active 不含、已成交部分留在 fills(spec:黃括號整日留存)", () => {
    const cancelledPartial = o({ actionable: false, order_qty: 5, filled_qty: 2 });
    const { active, fills } = splitMyLots([cancelledPartial]);
    expect(active).toEqual([]);
    expect(fills).toEqual([{ price: 100, buySell: "B", lots: 2 }]);
  });

  it("市價單(price=null)/側別不明 → 不上階梯", () => {
    const { active, fills } = splitMyLots([
      o({ price: null, filled_qty: 2 }),
      o({ buy_sell: null, filled_qty: 2 }),
    ]);
    expect(active).toEqual([]);
    expect(fills).toEqual([]);
  });
});

describe("buildLadder myFills 聚合", () => {
  it("同價多單成交加總、買賣分欄;無成交的列為 0", () => {
    const fills: MyOrderLot[] = [
      { price: 99.5, buySell: "B", lots: 1 },
      { price: 99.5, buySell: "B", lots: 3 },
      { price: 100.5, buySell: "S", lots: 2 },
    ];
    const rows = buildLadder({ center: 100, reference: 100, ...noDepth, myOrders: [], myFills: fills, rows: 10 });
    expect(rows.find((r) => r.price === 99.5)!.myBuyFills).toBe(4);
    expect(rows.find((r) => r.price === 100.5)!.mySellFills).toBe(2);
    expect(rows.find((r) => r.price === 100)!.myBuyFills).toBe(0);
  });
});
