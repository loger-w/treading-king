/**
 * 訊號規則 UI 共用標籤 — ActiveSignalEditor / PresetStrategyFields /
 * SignalRulesDialog 原本各抄一份,中文已 drift(中線 vs 中軸,正名為「中軸」)。
 */

export const ALL_CDP_LEVELS = ["ah", "nh", "cdp", "nl", "al"] as const;
export type CdpLevelKey = typeof ALL_CDP_LEVELS[number];

export const CDP_LEVEL_LABEL: Record<CdpLevelKey, string> = {
  ah: "AH (最高值)", nh: "NH (近高)", cdp: "CDP 中軸",
  nl: "NL (近低)", al: "AL (最低值)",
};

export const STRATEGY_LABEL: Record<string, string> = {
  limit_up_open_touch: "漲停打開碰 CDP",
  breakout_retest: "順勢爆拉突破回踩",
};
