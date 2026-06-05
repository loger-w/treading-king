import { describe, expect, test } from "vitest";
import { resolveDisplayChangePct } from "./quote-display";

// bug2:大漲股換新榜時,前端 snapshot(prevClose)還沒到 → quote.changePct=null → 漲跌幅「—」。
// 但後端大漲股 item 自帶 change_pct(漲幅榜算好的)。漲跌幅顯示應優先即時 quote、
// fallback 後端 change_pct,消除空窗的「—」。
describe("resolveDisplayChangePct", () => {
  test("有即時 quote.changePct 時優先用即時", () => {
    expect(resolveDisplayChangePct({ changePct: 5.2 }, { change_pct: 3.1 })).toBe(5.2);
  });

  test("quote.changePct 為 null(snapshot 還沒到)→ fallback 大漲股後端的 change_pct", () => {
    expect(resolveDisplayChangePct({ changePct: null }, { change_pct: 3.1 })).toBe(3.1);
  });

  test("quote 整個 undefined(symbol 還沒進訂閱)→ 一樣 fallback item.change_pct", () => {
    expect(resolveDisplayChangePct(undefined, { change_pct: 3.1 })).toBe(3.1);
  });

  test("平盤 0% 是有效值,不可被 fallback 蓋掉", () => {
    expect(resolveDisplayChangePct({ changePct: 0 }, { change_pct: 3.1 })).toBe(0);
  });

  test("兩邊都沒有(一般 user 書籤 + snapshot 未到)→ null,顯示 —", () => {
    expect(resolveDisplayChangePct(undefined, {})).toBe(null);
  });
});
