/**
 * 台股 tick ladder helpers — 跟 backend services/cdp.py 的 _tick_size / round_to_tick_tw
 * 對齊。每個價位的最小升降單位不同：
 *   < 10        tick 0.01
 *   10–50       tick 0.05
 *   50–100      tick 0.10
 *   100–500     tick 0.50
 *   500–1000    tick 1.00
 *   >= 1000     tick 5.00
 */

const TICK_LADDER: ReadonlyArray<readonly [number, number]> = [
  [10, 0.01],
  [50, 0.05],
  [100, 0.10],
  [500, 0.50],
  [1000, 1.00],
  [Infinity, 5.00],
];

export function tickSize(price: number): number {
  for (const [upper, tick] of TICK_LADDER) {
    if (price < upper) return tick;
  }
  return 5.00;
}

/** 對齊到最近的台股 tick。浮點誤差修正。 */
export function roundToNearestTick(price: number): number {
  const tick = tickSize(price);
  return Math.round((Math.round(price / tick) * tick) * 100) / 100;
}

/** 依 tick 決定小數位 — 大價位顯示整數，小價位顯示 2 位。 */
export function formatTickPrice(price: number): string {
  const rounded = roundToNearestTick(price);
  const tick = tickSize(rounded);
  const decimals = tick >= 1 ? 0 : tick >= 0.1 ? 1 : 2;
  return rounded.toFixed(decimals);
}
