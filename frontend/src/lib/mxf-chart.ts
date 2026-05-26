import type { MXFCandle } from "./api";
import { minuteOfDay } from "./intraday-time";

const DAY_OPEN_MIN = 8 * 60 + 45;  // 08:45 = 525

// 把任意 ISO 時間映射成台北日期字串 "YYYY-MM-DD"（固定 UTC+8，不信任 JS local tz）
function taipeiDateStr(iso: string): string {
  const d = new Date(iso);
  const tpe = new Date(d.getTime() + 8 * 60 * 60 * 1000);
  return tpe.toISOString().slice(0, 10);
}

/**
 * 找今日日盤開盤價 — 用 `now` 判定「今天」（台北時間 UTC+8），
 * 然後找第一根 minuteOfDay >= 08:45 且台北日期等於今天的 candle.open。
 * 凌晨夜盤中（今日日盤未開）時回傳 null。
 */
export function dayOpenBaseline(candles: MXFCandle[], now: Date): number | null {
  const today = taipeiDateStr(now.toISOString());
  for (const c of candles) {
    if (taipeiDateStr(c.date) !== today) continue;
    if (minuteOfDay(c.date) >= DAY_OPEN_MIN) return c.open;
  }
  return null;
}
