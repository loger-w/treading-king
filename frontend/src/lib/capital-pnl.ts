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
