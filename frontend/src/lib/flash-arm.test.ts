import { describe, expect, it } from "vitest";
import { ARM_IDLE_MS, initialArm, reduceArm } from "./flash-arm";

describe("武裝開關狀態機", () => {
  it("預設未武裝", () => expect(initialArm().armed).toBe(false));

  it("toggle 開/關,開時失敗計數歸零", () => {
    let s = reduceArm({ armed: false, failStreak: 2 }, { type: "toggle" });
    expect(s).toEqual({ armed: true, failStreak: 0 });
    s = reduceArm(s, { type: "toggle" });
    expect(s.armed).toBe(false);
  });

  it("換標的 / 連線斷 / 閒置逾時 → 解除武裝", () => {
    const armed = { armed: true, failStreak: 0 };
    for (const t of ["symbol_changed", "conn_lost", "idle_timeout"] as const) {
      expect(reduceArm(armed, { type: t }).armed).toBe(false);
    }
  });

  it("連續 3 次送單失敗 → 自動解除;成功會重置計數", () => {
    let s = { armed: true, failStreak: 0 };
    s = reduceArm(s, { type: "send_fail" });
    s = reduceArm(s, { type: "send_fail" });
    expect(s.armed).toBe(true);
    s = reduceArm(s, { type: "send_ok" });      // 重置
    s = reduceArm(s, { type: "send_fail" });
    s = reduceArm(s, { type: "send_fail" });
    expect(s.armed).toBe(true);                  // 只累積 2
    s = reduceArm(s, { type: "send_fail" });
    expect(s.armed).toBe(false);                 // 第 3 次 → 解除
    expect(s.failStreak).toBe(0);
  });

  it("disarm 只會解除、不會反向武裝(與 toggle 的差異所在)", () => {
    expect(reduceArm({ armed: true, failStreak: 1 }, { type: "disarm" }).armed).toBe(false);
    // 已解除時冪等 — 解除列/Esc 連按絕不能把狀態翻回武裝
    expect(reduceArm({ armed: false, failStreak: 0 }, { type: "disarm" }).armed).toBe(false);
  });

  it("閒置時限 = 5 分鐘", () => expect(ARM_IDLE_MS).toBe(5 * 60 * 1000));
});
