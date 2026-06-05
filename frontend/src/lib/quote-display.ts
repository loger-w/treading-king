/**
 * 漲跌幅顯示來源解析。
 *
 * 即時報價(snapshot prevClose + WS livePrice 算的 changePct)優先;拿不到時
 * (snapshot 還沒到、或 symbol 還沒進訂閱)fallback 用 item 自帶的 change_pct。
 * 後者是系統書籤(漲幅榜)item 本身就帶的欄位,讓換榜空窗期不會空白成「—」。
 *
 * 用 ??(不是 ||):平盤 0% 是有效值,不可被 fallback 蓋掉。
 */
export function resolveDisplayChangePct(
  quote: { changePct: number | null } | undefined,
  item: { change_pct?: number | null },
): number | null {
  return quote?.changePct ?? item.change_pct ?? null;
}
