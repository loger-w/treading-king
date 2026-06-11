// 閃電武裝開關狀態機。武裝=點價直送(無確認彈窗),是唯一繞過二次確認的路徑,
// 所以解除要寬鬆觸發:換標的/斷線/閒置 5 分鐘/連 3 次失敗。切分頁=unmount,state 自然消失。
export const ARM_IDLE_MS = 5 * 60 * 1000;
const FAIL_LIMIT = 3;

export interface ArmState {
  armed: boolean;
  failStreak: number;
}

export type ArmEvent =
  | { type: "toggle" }
  | { type: "symbol_changed" }
  | { type: "conn_lost" }
  | { type: "idle_timeout" }
  | { type: "send_ok" }
  | { type: "send_fail" };

export function initialArm(): ArmState {
  return { armed: false, failStreak: 0 };
}

export function reduceArm(s: ArmState, e: ArmEvent): ArmState {
  switch (e.type) {
    case "toggle":
      return { armed: !s.armed, failStreak: 0 };
    case "symbol_changed":
    case "conn_lost":
    case "idle_timeout":
      return { ...s, armed: false };
    case "send_ok":
      return { ...s, failStreak: 0 };
    case "send_fail": {
      const n = s.failStreak + 1;
      if (n >= FAIL_LIMIT) return { armed: false, failStreak: 0 };
      return { ...s, failStreak: n };
    }
  }
}
