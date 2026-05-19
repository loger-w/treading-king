import { describe, test, expect } from "vitest";
import { resolveCollisions, type LabelInput } from "./chart-labels";

const RANGE: [number, number] = [0, 400];

describe("resolveCollisions", () => {
  test("無重疊 - y 保持不變", () => {
    const items: LabelInput[] = [
      { originalY: 50,  text: "a", color: "red" },
      { originalY: 100, text: "b", color: "blue" },
      { originalY: 200, text: "c", color: "green" },
    ];
    const result = resolveCollisions(items, 16, RANGE);
    expect(result.map((r) => r.y)).toEqual([50, 100, 200]);
  });

  test("兩個重疊 - 下方推到 minGap 外", () => {
    const items: LabelInput[] = [
      { originalY: 100, text: "a", color: "red" },
      { originalY: 110, text: "b", color: "blue" },
    ];
    const result = resolveCollisions(items, 16, RANGE);
    expect(result[0].y).toBe(100);
    expect(result[1].y).toBe(116);
  });

  test("5 個全擠在 100~120 - 連續推開", () => {
    const items: LabelInput[] = [
      { originalY: 100, text: "a", color: "red" },
      { originalY: 105, text: "b", color: "blue" },
      { originalY: 110, text: "c", color: "green" },
      { originalY: 115, text: "d", color: "yellow" },
      { originalY: 120, text: "e", color: "purple" },
    ];
    const result = resolveCollisions(items, 16, RANGE);
    expect(result.map((r) => r.y)).toEqual([100, 116, 132, 148, 164]);
  });

  test("推出下邊界 - 回彈", () => {
    const items: LabelInput[] = [
      { originalY: 180, text: "a", color: "red" },
      { originalY: 190, text: "b", color: "blue" },
      { originalY: 195, text: "c", color: "green" },
    ];
    const result = resolveCollisions(items, 16, [0, 200]);
    expect(result.map((r) => r.y)).toEqual([168, 184, 200]);
  });

  test("input 順序與 originalY 不同 - 結果按 originalY 升序", () => {
    const items: LabelInput[] = [
      { originalY: 200, text: "z", color: "red" },
      { originalY: 50,  text: "a", color: "blue" },
      { originalY: 100, text: "m", color: "green" },
    ];
    const result = resolveCollisions(items, 16, RANGE);
    expect(result.map((r) => r.text)).toEqual(["a", "m", "z"]);
    expect(result.map((r) => r.y)).toEqual([50, 100, 200]);
  });

  test("空 input - 回空陣列", () => {
    expect(resolveCollisions([], 16, RANGE)).toEqual([]);
  });
});
