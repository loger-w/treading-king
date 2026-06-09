// 重疊%圖共用畫圖層 — 兩指數各自從昨收 0% 起算,共用 % 軸。
// 線用固定識別色(不隨漲跌變紅綠,否則兩線同色分不出)。inline hex(resvg 友善)。
import { createElement, Fragment } from "react";
import type { IntradayCandle } from "./api";
import {
  CHART_W, CHART_H, PAD_L, PAD_R, PAD_T, PAD_B, INTRADAY_THEME, type ChartTheme,
} from "./intraday-chart-svg";
import { MARKET_OPEN_MIN, MARKET_CLOSE_MIN, TRADING_MINUTES, minuteOfDay } from "./intraday-time";

export interface OverlaySeries {
  code: string; short: string; color: string;
  candles: IntradayCandle[]; prevClose: number | null;
}

export interface OverlayLine {
  code: string; short: string; color: string;
  poly: string;          // SVG points("" 表無資料)
  lastPct: number | null;
  lastY: number | null;  // 線尾 y(放 % 標籤)
}

export interface OverlayInput { scale?: number; theme?: ChartTheme; }

export interface OverlayGeometry {
  yMin: number; yMax: number; // 單位:%
  scaleX: (m: number) => number;
  scaleY: (pct: number) => number;
  padL: number; padR: number; padT: number; padB: number;
  fontScale: number;
  lines: OverlayLine[];
  zeroY: number;
  pctByCodeAtMinute: (code: string, m: number) => number | null;
}

const PCT_BUFFER = 0.1; // 上下各留 0.1 百分點

function seriesPct(s: OverlaySeries): Array<{ m: number; pct: number }> {
  if (s.prevClose == null || s.prevClose === 0) return [];
  return s.candles
    .filter((cd) => { const m = minuteOfDay(cd.date); return m >= MARKET_OPEN_MIN && m <= MARKET_CLOSE_MIN; })
    .map((cd) => ({ m: minuteOfDay(cd.date), pct: ((cd.close - s.prevClose!) / s.prevClose!) * 100 }));
}

export function computeOverlayGeometry(a: OverlaySeries, b: OverlaySeries, input: OverlayInput = {}): OverlayGeometry {
  const scale = input.scale ?? 1;
  const padL = Math.round(PAD_L * scale), padR = Math.round(PAD_R * scale);
  const padT = Math.round(PAD_T * scale), padB = Math.round(PAD_B * scale);

  const ptsA = seriesPct(a), ptsB = seriesPct(b);
  const allPct = [...ptsA, ...ptsB].map((p) => p.pct);
  const yMin = allPct.length ? Math.min(0, ...allPct) - PCT_BUFFER : -1;
  const yMax = allPct.length ? Math.max(0, ...allPct) + PCT_BUFFER : 1;

  const xRange = CHART_W - padL - padR;
  const yRange = CHART_H - padT - padB;
  const scaleX = (m: number) => padL + ((m - MARKET_OPEN_MIN) / TRADING_MINUTES) * xRange;
  const scaleY = (pct: number) => padT + (1 - (pct - yMin) / (yMax - yMin || 1)) * yRange;

  const mkLine = (s: OverlaySeries, pts: Array<{ m: number; pct: number }>): OverlayLine => {
    if (pts.length === 0) return { code: s.code, short: s.short, color: s.color, poly: "", lastPct: null, lastY: null };
    const poly = pts.map((p) => `${scaleX(p.m)},${scaleY(p.pct)}`).join(" ");
    const last = pts[pts.length - 1];
    return { code: s.code, short: s.short, color: s.color, poly, lastPct: last.pct, lastY: scaleY(last.pct) };
  };

  const lines = [mkLine(a, ptsA), mkLine(b, ptsB)];
  const byCode: Record<string, Array<{ m: number; pct: number }>> = { [a.code]: ptsA, [b.code]: ptsB };
  const pctByCodeAtMinute = (code: string, m: number): number | null => {
    const pts = byCode[code]; if (!pts || pts.length === 0) return null;
    let best = pts[0];
    for (const p of pts) if (Math.abs(p.m - m) < Math.abs(best.m - m)) best = p;
    return best.pct;
  };

  return { yMin, yMax, scaleX, scaleY, padL, padR, padT, padB, fontScale: scale, lines, zeroY: scaleY(0), pctByCodeAtMinute };
}

function fmtPct(p: number): string { return `${p >= 0 ? "+" : ""}${p.toFixed(2)}%`; }

export interface IndexOverlayStaticProps extends OverlayInput { geometry: OverlayGeometry; }

export function IndexOverlayStatic(props: IndexOverlayStaticProps) {
  const t = props.theme ?? INTRADAY_THEME;
  const { scaleX, scaleY, yMin, yMax, padL, padR, lines, fontScale } = props.geometry;
  const fs = (base: number) => Math.round(base * fontScale);
  const sw = (base: number) => base * fontScale;

  const ticks = [yMin, yMin + (yMax - yMin) / 4, 0, yMax - (yMax - yMin) / 4, yMax]
    .filter((v, i, arr) => arr.indexOf(v) === i);

  return createElement(Fragment, null,
    // Y 軸格線 + %
    ...ticks.map((pct) => {
      const y = scaleY(pct); const isZero = Math.abs(pct) < 1e-9;
      return createElement("g", { key: pct },
        createElement("line", { x1: padL, y1: y, x2: CHART_W - padR, y2: y, stroke: isZero ? t.inkDim : t.line, strokeWidth: isZero ? sw(0.8) : sw(0.5), strokeDasharray: isZero ? "5 3" : undefined, opacity: isZero ? "0.8" : "0.5" }),
        createElement("text", { x: padL - 4, y: y + 3, textAnchor: "end", fill: isZero ? t.ink : t.inkDim, fontSize: fs(13), fontFamily: t.fontFamily }, fmtPct(pct)),
      );
    }),
    // X 軸時間
    ...[
      { min: 540, label: "9:00" }, { min: 600, label: "10:00" }, { min: 660, label: "11:00" },
      { min: 720, label: "12:00" }, { min: 780, label: "13:00" }, { min: 810, label: "13:30" },
    ].map(({ min, label }) => createElement("text", { key: min, x: scaleX(min), y: CHART_H - 8, textAnchor: "middle", fill: t.inkDim, fontSize: fs(14), fontFamily: t.fontFamily }, label)),
    // 兩條線 + 線尾 %
    ...lines.map((ln) => ln.poly && createElement(Fragment, { key: ln.code },
      createElement("polyline", { points: ln.poly, fill: "none", stroke: ln.color, strokeWidth: sw(1.6) }),
      ln.lastY != null && ln.lastPct != null && createElement("text", { x: CHART_W - padR + 4, y: ln.lastY + 3, textAnchor: "start", fill: ln.color, fontSize: fs(13), fontFamily: t.fontFamily }, fmtPct(ln.lastPct)),
    )),
  );
}
