import { describe, it, expect } from "vitest";
import { brokerPnl, grossPnl, pickPrice, snapshotPrices } from "./capital-pnl";

// netPnl(env 費率估算)已移除:損益口徑統一為券商基底(brokerPnl),
// 前端不再自行估費稅(空單稅基課錯邊的 bug 隨之消滅)
describe("capital-pnl", () => {
  it("gross = qty*1000*(price-avg)", () => {
    expect(grossPnl(5, 575, 590)).toBe(75000);
  });
  it("short position gross", () => {
    expect(grossPnl(-2, 100, 95)).toBe(10000);
  });
  it("null price -> 0", () => {
    expect(grossPnl(5, 575, null)).toBe(0);
  });
  it("null avg -> 0(即時庫存報告無均價欄,未知均價不可估損益)", () => {
    expect(grossPnl(5, null, 590)).toBe(0);
  });
});

describe("snapshotPrices 快照價全量重建", () => {
  it("每輪回傳全新 map(凍結 bug 回歸:舊值不得殘留)、null 略過", () => {
    // 舊 bug:「已有值就不蓋」合併讓第一輪快照價永久凍結,損益不動
    const r1 = snapshotPrices([{ symbol: "2330", last_price: 100 }, { symbol: "3357", last_price: null }]);
    expect(r1).toEqual({ "2330": 100 });
    const r2 = snapshotPrices([{ symbol: "2330", last_price: 101.5 }]);
    expect(r2).toEqual({ "2330": 101.5 });
  });
});

describe("brokerPnl 券商淨損益基底+即時平移", () => {
  it("現價偏離報告市價時平移價差(2026-06-11 實例:報告288時-74636,現價288.5≈App的-73141)", () => {
    expect(brokerPnl(3, -74636, 288, 288.5)).toBe(-73136);
    expect(brokerPnl(3, -74636, 288, 287)).toBe(-77636);
  });

  it("缺現價回基底原值(報告時點的含費稅息損益)", () => {
    expect(brokerPnl(3, -74636, 288, null)).toBe(-74636);
  });
});

describe("pickPrice tick/快照雙層選價", () => {
  it("60s 內 tick 優先;逾期退快照(斷訂的陳舊 tick 不得變成新的凍結源)", () => {
    expect(pickPrice({ price: 100, ts: 0 }, 99, 30_000)).toBe(100);
    expect(pickPrice({ price: 100, ts: 0 }, 99, 61_000)).toBe(99);
  });

  it("無快照時寧用陳舊 tick;兩者皆無回 null", () => {
    expect(pickPrice({ price: 100, ts: 0 }, undefined, 61_000)).toBe(100);
    expect(pickPrice(undefined, undefined, 0)).toBeNull();
  });
});
