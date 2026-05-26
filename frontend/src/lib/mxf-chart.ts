import type { MXFCandle } from "./api";

const DAY_OPEN_MIN = 8 * 60 + 45;  // 08:45 = 525

/**
 * 找今日日盤開盤價 — 用 `now` 判定「今天」（本地時區），
 * 然後找第一根 minuteOfDay >= 08:45 且日期等於 now.toDateString() 的 candle.open。
 * 凌晨夜盤中（今日日盤未開）時回傳 null。
 */
export function dayOpenBaseline(candles: MXFCandle[], now: Date): number | null {
  const today = now.toDateString();
  for (const c of candles) {
    const d = new Date(c.date);
    if (d.toDateString() !== today) continue;
    const m = d.getHours() * 60 + d.getMinutes();
    if (m >= DAY_OPEN_MIN) return c.open;
  }
  return null;
}
