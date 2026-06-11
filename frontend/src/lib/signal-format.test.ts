import { describe, expect, it } from "vitest";
import { extractTouch, formatTouch } from "./signal-format";
import type { TouchMeta } from "./api";

describe("formatTouch", () => {
  it("已知 role/level 用中文標籤", () => {
    const t: TouchMeta = { level: "ah", direction: "from_below", role: "resistance", touch_index: 2 };
    expect(formatTouch(t)).toBe("第 2 次碰到壓力 · CDP AH");
  });

  it("未知 role 退回原字串,不渲染「undefined」", () => {
    // context_json 來自本機 JSONL(跨版本、不可信),extractTouch 只驗 string
    // 不驗 enum——後端新 role 或歷史資料異常時不可顯示「第 N 次undefined」
    const t = { level: "ah", direction: "from_below", role: "breakout", touch_index: 1 } as unknown as TouchMeta;
    expect(formatTouch(t)).toBe("第 1 次breakout · CDP AH");
  });

  it("未知 level 退回原字串(既有寬鬆策略)", () => {
    const t = { level: "sma_60", direction: "from_above", role: "support", touch_index: 3 } as unknown as TouchMeta;
    expect(formatTouch(t)).toBe("第 3 次碰到支撐 · sma_60");
  });
});

describe("extractTouch", () => {
  it("欄位齊全才回 TouchMeta,缺欄位回 undefined", () => {
    expect(extractTouch({ cdp_touch: { level: "ah", direction: "horizontal", role: "touch", touch_index: 1 } }, "cdp_touch"))
      .toEqual({ level: "ah", direction: "horizontal", role: "touch", touch_index: 1 });
    expect(extractTouch({ cdp_touch: { level: "ah" } }, "cdp_touch")).toBeUndefined();
    expect(extractTouch(null, "cdp_touch")).toBeUndefined();
  });
});
