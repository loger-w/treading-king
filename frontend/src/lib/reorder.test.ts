import { describe, expect, it } from "vitest";
import { applyDragToOrder } from "./reorder";

describe("applyDragToOrder", () => {
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
