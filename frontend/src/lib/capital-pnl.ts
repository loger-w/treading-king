/** 未實現損益。qty 為張(放空為負)。 */
export function grossPnl(qty: number, avgPrice: number, currentPrice: number | null): number {
  if (currentPrice == null) return 0;
  return qty * 1000 * (currentPrice - avgPrice);
}

/** 淨損益 = 毛 − 進場手續費 − 出場手續費 − 證交稅(出場)。 */
export function netPnl(
  qty: number, avgPrice: number, currentPrice: number | null,
  feeRate: number, taxRate: number,
): number {
  if (currentPrice == null) return 0;
  const shares = Math.abs(qty) * 1000;
  const entryFee = Math.round(shares * avgPrice * feeRate);
  const exitFee = Math.round(shares * currentPrice * feeRate);
  const tax = Math.round(shares * currentPrice * taxRate);
  return grossPnl(qty, avgPrice, currentPrice) - entryFee - exitFee - tax;
}

/** 快照價每輪全量重建(不吃前值 — 簽名就杜絕「已有值就不蓋」的凍結 bug)。
 *  tick 價另存一層,顯示時 tick 優先。 */
export function snapshotPrices(rows: Array<{ symbol: string; last_price: number | null }>): Record<string, number> {
  const out: Record<string, number> = {};
  for (const r of rows) if (r.last_price != null) out[r.symbol] = r.last_price;
  return out;
}
