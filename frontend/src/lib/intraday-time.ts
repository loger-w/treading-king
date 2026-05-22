// 台股正盤交易時段(分鐘 of day,台北時間)
// IntradayChart X 軸固定範圍 = [MARKET_OPEN_MIN, MARKET_CLOSE_MIN]
export const MARKET_OPEN_MIN = 9 * 60;            // 540
export const MARKET_CLOSE_MIN = 13 * 60 + 30;     // 810
export const TRADING_MINUTES = MARKET_CLOSE_MIN - MARKET_OPEN_MIN; // 270

// 從 ISO timestamp(可帶任何 timezone offset)抓出台北時區的分鐘 of day。
// Fubon candle.date 通常是 "2026-05-22T09:00:00.000+08:00",但保險起見
// 用 Date.getUTCHours/Minutes + 固定 +480 分鐘 offset(台北永遠 UTC+8)。
export function minuteOfDay(iso: string): number {
  const d = new Date(iso);
  const utcMinutes = d.getUTCHours() * 60 + d.getUTCMinutes();
  return (utcMinutes + 8 * 60) % (24 * 60);
}
