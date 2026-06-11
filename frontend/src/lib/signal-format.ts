import type { TouchMeta } from "./api";

const ROLE_ZH: Record<TouchMeta["role"], string> = {
  resistance: "碰到壓力",
  support: "碰到支撐",
  touch: "平觸",
};

const LEVEL_ZH: Record<string, string> = {
  ah: "CDP AH", nh: "CDP NH", cdp: "CDP 中軸",
  nl: "CDP NL", al: "CDP AL",
  sma_5: "MA5", sma_20: "MA20",
};

export function formatTouch(t: TouchMeta): string {
  // role/level 同採寬鬆策略:context_json 來自本機 JSONL(跨版本),
  // 未知值退回原字串,不可渲染出「undefined」
  const role = ROLE_ZH[t.role] ?? t.role;
  const level = LEVEL_ZH[t.level] ?? t.level;
  return `第 ${t.touch_index} 次${role} · ${level}`;
}

/** SignalLogRow.context_json 內取 cdp_touch / ma_touch(可能不存在或 cast 失敗,回 undefined)。 */
export function extractTouch(
  context: Record<string, unknown> | null | undefined,
  key: "cdp_touch" | "ma_touch",
): TouchMeta | undefined {
  if (!context || typeof context !== "object") return undefined;
  const v = context[key];
  if (!v || typeof v !== "object") return undefined;
  const obj = v as Partial<TouchMeta>;
  if (typeof obj.level === "string"
      && typeof obj.direction === "string"
      && typeof obj.role === "string"
      && typeof obj.touch_index === "number") {
    return obj as TouchMeta;
  }
  return undefined;
}
