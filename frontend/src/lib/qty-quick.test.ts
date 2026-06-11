import { describe, expect, it } from "vitest";
import { initialQtyState, pressQuick, manualQty, QTY_PRESETS } from "./qty-quick";

describe("張數快捷:單點填入、同顆累加、切顆重填", () => {
  it("presets 固定 1/3/5/10", () => expect(QTY_PRESETS).toEqual([1, 3, 5, 10]));

  it("首點 5 → 填入 5", () => {
    const s = pressQuick(initialQtyState(), 5);
    expect(s.qty).toBe(5);
  });

  it("再點 5 → 累加成 10", () => {
    const s = pressQuick(pressQuick(initialQtyState(), 5), 5);
    expect(s.qty).toBe(10);
  });

  it("點 5 再點 3 → 重填為 3(切顆不累加)", () => {
    const s = pressQuick(pressQuick(initialQtyState(), 5), 3);
    expect(s.qty).toBe(3);
  });

  it("手動輸入後再點快捷 → 重填(輸入打斷累加鏈)", () => {
    let s = pressQuick(initialQtyState(), 5);   // 5
    s = manualQty(s, 7);                          // 手動 7
    s = pressQuick(s, 5);                         // 點 5 → 填入 5(非 12)
    expect(s.qty).toBe(5);
  });

  it("manualQty 下限 1:0 與負值收斂到 1", () => {
    expect(manualQty(initialQtyState(), 0).qty).toBe(1);
    expect(manualQty(initialQtyState(), -3).qty).toBe(1);
  });
});
