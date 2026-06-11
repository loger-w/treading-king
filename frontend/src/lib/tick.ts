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

/** 方向性對齊 tick。用 0.1 分(deci-cent)整數運算:ref×1.1/×0.9 合法地帶
 *  半分尾數(9.05×1.1=9.955),整數「分」會把該被 floor/ceil 的尾數提前
 *  四捨五入;deci-cent 下這些輸入是精確整數,round 只殺 float 雜訊(~1e-12),
 *  同時保留整數除法繞開 53.9/0.1=538.999… 的 floor 誤捨陷阱。 */
export function roundToTick(price: number, dir: "up" | "down"): number {
  const tick = tickSize(price);
  const deci = Math.round(price * 1000);
  const tickDeci = Math.round(tick * 1000);
  const units = dir === "down" ? Math.floor(deci / tickDeci) : Math.ceil(deci / tickDeci);
  return (units * tickDeci) / 1000;
}

/** 台股漲停價 = 參考價 ×1.1 尾數捨去(絕不超過 +10%);tick 以漲停價當下級距為準。 */
export function limitUp(reference: number): number {
  return roundToTick(reference * 1.1, "down");
}

/** 台股跌停價 = 參考價 ×0.9 尾數進位(絕不超過 -10%)。 */
export function limitDown(reference: number): number {
  return roundToTick(reference * 0.9, "up");
}
