import { describe, expect, it } from "vitest";
import { applyDragToOrder, partitionByHits } from "./reorder";

describe("applyDragToOrder", () => {
  // 為何重要:置頂(訊號命中)項目不進拖拉區,但它在「完整順序」裡佔有 slot;
  // 拖拉結果必須套回完整順序而不破壞置頂項目的手動位置
  it("把 active 移到 over 的位置,其餘相對順序不變", () => {
    expect(applyDragToOrder(["A", "B", "C", "D"], "D", "B")).toEqual(["A", "D", "B", "C"]);
    expect(applyDragToOrder(["A", "B", "C", "D"], "A", "C")).toEqual(["B", "C", "A", "D"]);
  });
  it("active/over 不存在或相同時回傳原順序", () => {
    const order = ["A", "B"];
    expect(applyDragToOrder(order, "X", "B")).toBe(order);
    expect(applyDragToOrder(order, "A", "A")).toBe(order);
  });
});

describe("partitionByHits", () => {
  const items = [{ symbol: "A" }, { symbol: "B" }, { symbol: "C" }];
  it("命中的置頂(命中數降冪),其餘維持原順序", () => {
    const hits: Record<string, number> = { B: 2, C: 5 };
    const { pinned, rest } = partitionByHits(items, (s) => hits[s] ?? 0);
    expect(pinned.map((i) => i.symbol)).toEqual(["C", "B"]);
    expect(rest.map((i) => i.symbol)).toEqual(["A"]);
  });
  it("無命中時全部都在 rest、順序不變", () => {
    const { pinned, rest } = partitionByHits(items, () => 0);
    expect(pinned).toEqual([]);
    expect(rest.map((i) => i.symbol)).toEqual(["A", "B", "C"]);
  });
});
