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

// ============================================================
// SVG 子元件 — 都是 stateless,接 props 後輸出 <g>
// ============================================================

export interface OHLCCandle {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  average?: number;
}

interface ScaleProps {
  scaleX: (iso: string) => number;
  scaleY: (v: number) => number;
}

interface CandlestickProps extends ScaleProps {
  candles: OHLCCandle[];
  width: number;
  bullColor?: string;
  bearColor?: string;
}

export function CandlestickSeries({
  candles,
  scaleX,
  scaleY,
  width: _width,
  bullColor = "#d9534f",
  bearColor = "#2e7d32",
}: CandlestickProps) {
  if (candles.length < 2) return null;
  // 估每根 K 棒寬:取相鄰兩根 X 距離的 60%
  const x0 = scaleX(candles[0].date);
  const x1 = scaleX(candles[1].date);
  const barW = Math.max(1, Math.abs(x1 - x0) * 0.6);
  return (
    <g>
      {candles.map((c) => {
        const cx = scaleX(c.date);
        if (Number.isNaN(cx)) return null;
        const yOpen = scaleY(c.open);
        const yClose = scaleY(c.close);
        const yHigh = scaleY(c.high);
        const yLow = scaleY(c.low);
        const up = c.close >= c.open;
        const color = up ? bullColor : bearColor;
        const bodyTop = Math.min(yOpen, yClose);
        const bodyH = Math.max(1, Math.abs(yClose - yOpen));
        return (
          <g key={c.date}>
            <line x1={cx} x2={cx} y1={yHigh} y2={yLow} stroke={color} strokeWidth={1} />
            <rect x={cx - barW / 2} y={bodyTop} width={barW} height={bodyH} fill={color} />
          </g>
        );
      })}
    </g>
  );
}

interface LineSeriesProps extends ScaleProps {
  candles: OHLCCandle[];
  field: "close" | "average";
  stroke?: string;
  strokeWidth?: number;
  dashed?: boolean;
}

export function LineSeries({ candles, scaleX, scaleY, field, stroke = "#d9534f", strokeWidth = 1.5, dashed = false }: LineSeriesProps) {
  const points = candles
    .map((c) => {
      const x = scaleX(c.date);
      if (Number.isNaN(x)) return null;
      const v = field === "close" ? c.close : c.average ?? c.close;
      return `${x},${scaleY(v)}`;
    })
    .filter((p): p is string => p !== null)
    .join(" ");
  return (
    <polyline
      fill="none"
      stroke={stroke}
      strokeWidth={strokeWidth}
      strokeDasharray={dashed ? "3 3" : undefined}
      points={points}
    />
  );
}

interface MALineProps extends ScaleProps {
  candles: OHLCCandle[];
  maValues: number[];  // 跟 candles 同長度,NaN 表示該根沒值
  stroke: string;
  label?: string;
}

export function MALine({ candles, maValues, scaleX, scaleY, stroke, label }: MALineProps) {
  const points: string[] = [];
  let lastX = 0;
  let lastY = 0;
  for (let i = 0; i < candles.length; i++) {
    if (Number.isNaN(maValues[i])) continue;
    const x = scaleX(candles[i].date);
    if (Number.isNaN(x)) continue;
    const y = scaleY(maValues[i]);
    points.push(`${x},${y}`);
    lastX = x;
    lastY = y;
  }
  if (points.length === 0) return null;
  return (
    <g>
      <polyline fill="none" stroke={stroke} strokeWidth={1.2} points={points.join(" ")} />
      {label && <text x={lastX + 4} y={lastY + 3} fontSize={10} fill={stroke}>{label}</text>}
    </g>
  );
}

interface VolumeSubChartProps {
  candles: OHLCCandle[];
  scaleX: (iso: string) => number;
  yTop: number;       // sub-chart 頂端 y
  height: number;     // sub-chart 高
  barWidth: number;
  bullColor?: string;
  bearColor?: string;
}

export function VolumeSubChart({
  candles,
  scaleX,
  yTop,
  height,
  barWidth,
  bullColor = "#d9534f",
  bearColor = "#2e7d32",
}: VolumeSubChartProps) {
  if (candles.length === 0) return null;
  const maxVol = Math.max(...candles.map((c) => c.volume), 1);
  return (
    <g>
      {candles.map((c) => {
        const cx = scaleX(c.date);
        if (Number.isNaN(cx)) return null;
        const h = (c.volume / maxVol) * height;
        const up = c.close >= c.open;
        return (
          <rect
            key={c.date}
            x={cx - barWidth / 2}
            y={yTop + (height - h)}
            width={barWidth}
            height={h}
            fill={up ? bullColor : bearColor}
            opacity={0.7}
          />
        );
      })}
    </g>
  );
}

interface HoverCrosshairProps {
  x: number;
  y: number;
  height: number;
  width: number;
  label?: string;
}

export function HoverCrosshair({ x, y, height, width, label }: HoverCrosshairProps) {
  return (
    <g pointerEvents="none">
      <line x1={x} x2={x} y1={0} y2={height} stroke="#999" strokeDasharray="2 2" strokeWidth={1} />
      <line x1={0} x2={width} y1={y} y2={y} stroke="#999" strokeDasharray="2 2" strokeWidth={1} />
      {label && (
        <g>
          <rect x={x + 4} y={y - 18} width={120} height={16} fill="white" stroke="#999" />
          <text x={x + 8} y={y - 6} fontSize={11} fill="#333">{label}</text>
        </g>
      )}
    </g>
  );
}
