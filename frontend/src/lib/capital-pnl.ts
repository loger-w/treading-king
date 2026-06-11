/** 未實現損益。qty 為張(放空為負);avg null=均價未知(庫存報告無均價欄)→ 0,顯示層另標「—」。 */
export function grossPnl(qty: number, avgPrice: number | null, currentPrice: number | null): number {
  if (currentPrice == null || avgPrice == null) return 0;
  return qty * 1000 * (currentPrice - avgPrice);
}

/** 淨損益 = 毛 − 進場手續費 − 出場手續費 − 證交稅(出場)。 */
export function netPnl(
  qty: number, avgPrice: number | null, currentPrice: number | null,
  feeRate: number, taxRate: number,
): number {
  if (currentPrice == null || avgPrice == null) return 0;
  const shares = Math.abs(qty) * 1000;
  const entryFee = Math.round(shares * avgPrice * feeRate);
  const exitFee = Math.round(shares * currentPrice * feeRate);
  const tax = Math.round(shares * currentPrice * taxRate);
  return grossPnl(qty, avgPrice, currentPrice) - entryFee - exitFee - tax;
}

/** 快照價每輪全量重建(不吃前值 — 簽名就杜絕「已有值就不蓋」的凍結 bug)。
 *  tick 價另存一層,顯示時經 pickPrice 合併。 */
export function snapshotPrices(rows: Array<{ symbol: string; last_price: number | null }>): Record<string, number> {
  const out: Record<string, number> = {};
  for (const r of rows) if (r.last_price != null) out[r.symbol] = r.last_price;
  return out;
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
