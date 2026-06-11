// 群益下單選項的顯示標籤 — OrderTicket / FlashPanel / OrderConfirmDialog 共用一份
export const TRADE_KINDS = ["cash", "margin", "short", "daytrade_sell"] as const;
export type TradeKindValue = (typeof TRADE_KINDS)[number];
export const TRADE_KIND_LABELS: Record<TradeKindValue, string> = {
  cash: "現股", margin: "融資", short: "融券", daytrade_sell: "無券",
};

export const TIF_VALUES = ["ROD", "IOC", "FOK"] as const;
export type TifValue = (typeof TIF_VALUES)[number];
