/**
 * Chart 繪圖工具 — 純函式部分。
 *
 * 跨多 session 的 X 軸壓縮:給定 sessions(每段有 startIso/endIso)、
 * scaleX_compressed 把 ISO 時間映射到 [0, width],session 之間的 gap 視覺上佔 1%。
 */

export interface ChartSession {
  startIso: string;  // ISO 8601 with tz offset
  endIso: string;
}

interface Span {
  start: number;  // epoch ms
  end: number;
  pxStart: number;
  pxEnd: number;
}

const GAP_RATIO = 0.01;  // 每個 gap 佔總寬 1%(雙線視覺夠用)

function buildSpans(sessions: ChartSession[], width: number): Span[] {
  if (sessions.length === 0) return [];
  const durations = sessions.map((s) => new Date(s.endIso).getTime() - new Date(s.startIso).getTime());
  const totalSessionDuration = durations.reduce((a, b) => a + b, 0);
  const gapCount = sessions.length - 1;
  // gap 總寬 = width × (gapCount × GAP_RATIO);剩下分給 sessions(按 duration 比例)
  const totalGapPx = width * gapCount * GAP_RATIO;
  const sessionTotalPx = width - totalGapPx;

  const spans: Span[] = [];
  let cursorPx = 0;
  for (let i = 0; i < sessions.length; i++) {
    const sStart = new Date(sessions[i].startIso).getTime();
    const sEnd = new Date(sessions[i].endIso).getTime();
    const widthPx = (durations[i] / totalSessionDuration) * sessionTotalPx;
    spans.push({ start: sStart, end: sEnd, pxStart: cursorPx, pxEnd: cursorPx + widthPx });
    cursorPx += widthPx;
    if (i < sessions.length - 1) cursorPx += width * GAP_RATIO;
  }
  return spans;
}

export function scaleX_compressed(iso: string, sessions: ChartSession[], width: number): number {
  const spans = buildSpans(sessions, width);
  const t = new Date(iso).getTime();
  for (const span of spans) {
    if (t >= span.start && t <= span.end) {
      const ratio = (t - span.start) / (span.end - span.start);
      return span.pxStart + ratio * (span.pxEnd - span.pxStart);
    }
  }
  return NaN;
}

/** 算出每個 session 邊界的 px 位置(gap 在哪) — 給虛線分隔用 */
export function sessionBoundaries(sessions: ChartSession[], width: number): { gapStartPx: number; gapEndPx: number }[] {
  const spans = buildSpans(sessions, width);
  return spans.slice(0, -1).map((span, i) => ({
    gapStartPx: span.pxEnd,
    gapEndPx: spans[i + 1].pxStart,
  }));
}

export function scaleY_clamped(value: number, yMin: number, yMax: number, height: number): number {
  if (yMax === yMin) return height / 2;
  return height - ((value - yMin) / (yMax - yMin)) * height;
}

interface VWAPInputCandle {
  close: number;
  volume: number;
}

export function computeVWAP(candles: VWAPInputCandle[]): number[] {
  const out: number[] = [];
  let sumPV = 0;
  let sumV = 0;
  for (const c of candles) {
    sumPV += c.close * c.volume;
    sumV += c.volume;
    out.push(sumV > 0 ? sumPV / sumV : c.close);
  }
  return out;
}

export function computeMA(closes: number[], period: number): number[] {
  const out: number[] = new Array(closes.length).fill(NaN);
  if (period <= 0 || closes.length < period) return out;
  let sum = 0;
  for (let i = 0; i < closes.length; i++) {
    sum += closes[i];
    if (i >= period) sum -= closes[i - period];
    if (i >= period - 1) out[i] = sum / period;
  }
  return out;
}
