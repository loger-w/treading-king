/** 未實現損益。qty 為張(放空為負);avg null=均價未知(庫存報告無均價欄)→ 0,顯示層另標「—」。 */
export function grossPnl(qty: number, avgPrice: number | null, currentPrice: number | null): number {
  if (currentPrice == null || avgPrice == null) return 0;
  return qty * 1000 * (currentPrice - avgPrice);
}

/** 快照價每輪全量重建(不吃前值 — 簽名就杜絕「已有值就不蓋」的凍結 bug)。
 *  tick 價另存一層,顯示時經 pickPrice 合併。 */
export function snapshotPrices(rows: Array<{ symbol: string; last_price: number | null }>): Record<string, number> {
  const out: Record<string, number> = {};
  for (const r of rows) if (r.last_price != null) out[r.symbol] = r.last_price;
  return out;
}

/** 券商淨損益基底+即時平移:基底=損益試算報告[9](含費稅息,與群益 App 同源、報告市價時點),
 *  現價跳動時平移純價差。賣出費稅隨價變動的微差刻意忽略(實測對 App 差個位數元)。 */
export function brokerPnl(qty: number, pnlBase: number, basePrice: number, cur: number | null): number {
  if (cur == null) return pnlBase;
  return pnlBase + qty * 1000 * (cur - basePrice);
}

export const TICK_FRESH_MS = 60_000;

/** 顯示價:新鮮 tick 優先,逾期退快照 — 標的被移出訂閱後,陳舊 tick 不得
 *  永久遮住每 30s 刷新的快照價(否則就是換個來源的凍結 bug)。無快照才用陳舊 tick。 */
export function pickPrice(
  tick: { price: number; ts: number } | undefined,
  snap: number | undefined,
  now: number,
): number | null {
  if (tick && now - tick.ts < TICK_FRESH_MS) return tick.price;
  return snap ?? tick?.price ?? null;
}
