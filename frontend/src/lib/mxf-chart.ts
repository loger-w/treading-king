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

export interface ViewRange {
  startIdx: number;
  endIdx: number;
}

export interface ComputeNewViewRangeArgs {
  prevRange: ViewRange;
  mouseRatio: number;     // 0 = left edge, 1 = right edge
  deltaY: number;          // wheel deltaY: positive = zoom out, negative = zoom in
  candlesLen: number;
  innerW: number;
  minCandlePx: number;
}

const ZOOM_FACTOR = 1.15;
const MIN_VISIBLE = 5;

export function computeNewViewRange(args: ComputeNewViewRangeArgs): ViewRange {
  const { prevRange, mouseRatio, deltaY, candlesLen, innerW, minCandlePx } = args;
  const visible = prevRange.endIdx - prevRange.startIdx + 1;
  const factor = deltaY > 0 ? ZOOM_FACTOR : 1 / ZOOM_FACTOR;
  let newVisible = Math.round(visible * factor);
  // Clamp visible to [MIN_VISIBLE, maxByPx, candlesLen]
  const maxByPx = Math.floor(innerW / minCandlePx);
  newVisible = Math.max(MIN_VISIBLE, Math.min(maxByPx, candlesLen, newVisible));

  // Anchor: keep candle under cursor at same pixel
  const anchorIdx = prevRange.startIdx + Math.round(mouseRatio * (visible - 1));
  let newStart = Math.round(anchorIdx - mouseRatio * (newVisible - 1));
  // Clamp newStart to [0, candlesLen - newVisible]
  newStart = Math.max(0, Math.min(candlesLen - newVisible, newStart));

  return { startIdx: newStart, endIdx: newStart + newVisible - 1 };
}

const INTERVALS_MIN = [5, 15, 30, 60, 120, 240];

/**
 * 依「視窗內所有 session 時長加總」自動選 label interval (分鐘)。
 * Target: 不超過 ~7 個 label / 視窗。
 */
export function pickInterval(visibleMinutesSum: number, targetLabelCount = 7): number {
  for (const iv of INTERVALS_MIN) {
    if (visibleMinutesSum / iv <= targetLabelCount) return iv;
  }
  return 240;
}
