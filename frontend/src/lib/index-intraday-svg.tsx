// 指數分時圖共用畫圖層 — 網頁(IndexIntradayChart)與 Discord bot 共用同一份 JSX。
// 精簡版:autofit Y、不碰 average(指數無此欄位)、不套股票 tick(指數非個股 tick ladder)。
// 顏色一律 inline hex(resvg 不解析 Tailwind / var(--color-…))。
import { createElement, Fragment } from "react";
import type { IntradayCandle } from "./api";
import {
  CHART_W, CHART_H, PAD_L, PAD_R, PAD_T, PAD_B, VOL_GAP, VOL_PAD_T, TOTAL_H, INTRADAY_THEME, type ChartTheme,
} from "./intraday-chart-svg";
import { MARKET_OPEN_MIN, MARKET_CLOSE_MIN, TRADING_MINUTES, minuteOfDay } from "./intraday-time";

const Y_BUFFER = 0.0015; // autofit 上下各留 0.15%

/** 指數價格格式化:千分位 + 2 位小數,不依賴 ICU、不套股票 tick。 */
export function fmtIndex(v: number): string {
  return v.toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

/** 指數成交值格式化:元 → 億(四捨五入整數)+ 千分位。指數 candle.volume 是成交值(元)非張數。 */
export function fmtIndexVol(valueYuan: number): string {
  const yi = Math.round(valueYuan / 1e8);
  return `${String(yi).replace(/\B(?=(\d{3})+(?!\d))/g, ",")}億`;
}

/** 振幅 = (今日高 − 今日低) / 昨收 × 100。無昨收回 null。 */
export function indexAmplitude(high: number, low: number, prevClose: number | null): number | null {
  if (prevClose == null || prevClose === 0) return null;
  return ((high - low) / prevClose) * 100;
}

export interface IndexChartInput {
  candles: IntradayCandle[];
  prevClose: number | null;
  theme?: ChartTheme;
  scale?: number; // 內容放大倍率:網頁 1、bot 1.6
}

export interface IndexGeometry {
  yMin: number; yMax: number;
  scaleX: (m: number) => number;
  scaleY: (v: number) => number;
  padL: number; padR: number; padT: number; padB: number;
  fontScale: number;
  polyClose: string;
  minutesByIdx: number[];
  filteredCandles: IntradayCandle[];
  todayHigh: number; todayHighIdx: number;
  todayLow: number; todayLowIdx: number;
  maxVolume: number; scaleVolY: (v: number) => number; volBarW: number;
}

export function computeIndexGeometry(input: IndexChartInput): IndexGeometry {
  const scale = input.scale ?? 1;
  const padL = Math.round(PAD_L * scale);
  const padR = Math.round(PAD_R * scale);
  const padT = Math.round(PAD_T * scale);
  const padB = Math.round(PAD_B * scale);

  const filteredCandles = input.candles.filter((cd) => {
    const m = minuteOfDay(cd.date);
    return m >= MARKET_OPEN_MIN && m <= MARKET_CLOSE_MIN;
  });

  if (filteredCandles.length === 0) {
    return {
      yMin: 0, yMax: 0, scaleX: () => 0, scaleY: () => 0,
      padL, padR, padT, padB, fontScale: scale, polyClose: "",
      minutesByIdx: [], filteredCandles: [],
      todayHigh: 0, todayHighIdx: -1, todayLow: 0, todayLowIdx: -1,
      maxVolume: 0, scaleVolY: () => 0, volBarW: 0,
    };
  }

  const highs = filteredCandles.map((cd) => cd.high);
  const lows = filteredCandles.map((cd) => cd.low);
  const ref = input.prevClose;
  const rawMax = Math.max(...highs, ...(ref != null ? [ref] : []));
  const rawMin = Math.min(...lows, ...(ref != null ? [ref] : []));
  const yMax = rawMax * (1 + Y_BUFFER);
  const yMin = rawMin * (1 - Y_BUFFER);

  const xRange = CHART_W - padL - padR;
  const yRange = CHART_H - padT - padB;
  const minutesByIdx = filteredCandles.map((cd) => minuteOfDay(cd.date));
  const scaleX = (m: number) => padL + ((m - MARKET_OPEN_MIN) / TRADING_MINUTES) * xRange;
  const scaleY = (v: number) => padT + (1 - (v - yMin) / (yMax - yMin || 1)) * yRange;
  const polyClose = filteredCandles
    .map((cd, i) => `${scaleX(minutesByIdx[i])},${scaleY(cd.close)}`).join(" ");

  const maxVolume = Math.max(1, ...filteredCandles.map((cd) => cd.volume));
  const volTop = CHART_H + VOL_GAP + VOL_PAD_T;
  const scaleVolY = (v: number) => volTop + (1 - v / maxVolume) * (TOTAL_H - volTop);
  const volBarW = Math.max(1, (xRange / TRADING_MINUTES) * 0.7);

  let todayHigh = filteredCandles[0].high, todayHighIdx = 0;
  let todayLow = filteredCandles[0].low, todayLowIdx = 0;
  for (let i = 1; i < filteredCandles.length; i++) {
    if (filteredCandles[i].high > todayHigh) { todayHigh = filteredCandles[i].high; todayHighIdx = i; }
    if (filteredCandles[i].low < todayLow) { todayLow = filteredCandles[i].low; todayLowIdx = i; }
  }

  return {
    yMin, yMax, scaleX, scaleY, padL, padR, padT, padB, fontScale: scale,
    polyClose, minutesByIdx, filteredCandles, todayHigh, todayHighIdx, todayLow, todayLowIdx,
    maxVolume, scaleVolY, volBarW,
  };
}

function priceColor(price: number, baseline: number, t: ChartTheme): string {
  if (price > baseline) return t.bull;
  if (price < baseline) return t.bear;
  return t.ink;
}

export interface IndexIntradayStaticProps extends IndexChartInput {
  geometry: IndexGeometry;
  idPrefix?: string; // clipPath id 唯一化 — 並排兩張同頁時避免 id 衝突
}

// presentational — 靜態圖層,不含 hover / 外層 <svg>。網頁與 bot resvg 共用。
export function IndexIntradayStatic(props: IndexIntradayStaticProps) {
  const t = props.theme ?? INTRADAY_THEME;
  const idp = props.idPrefix ?? "";
  const aboveId = `idx-above-${idp}`;
  const belowId = `idx-below-${idp}`;
  const {
    scaleX, scaleY, polyClose, minutesByIdx, filteredCandles,
    todayHigh, todayHighIdx, todayLow, todayLowIdx, padL, padR, padT, padB, fontScale,
    maxVolume, scaleVolY, volBarW,
  } = props.geometry;
  const fs = (base: number) => Math.round(base * fontScale);
  const sw = (base: number) => base * fontScale;
  const baseline = props.prevClose ?? (filteredCandles[0]?.open ?? 0);
  if (filteredCandles.length === 0) return null;

  const baselineY = scaleY(baseline);
  const lastIdx = filteredCandles.length - 1;
  const fillPoints = [
    `${scaleX(minutesByIdx[0])},${baselineY}`,
    ...filteredCandles.map((cd, i) => `${scaleX(minutesByIdx[i])},${scaleY(cd.close)}`),
    `${scaleX(minutesByIdx[lastIdx])},${baselineY}`,
  ].join(" ");

  return createElement(Fragment, null,
    // 1. 紅綠填色(走勢↔昨收),clipPath 切上下
    baseline > 0 && createElement(Fragment, null,
      createElement("defs", null,
        createElement("clipPath", { id: aboveId },
          createElement("rect", { x: padL, y: padT, width: CHART_W - padL - padR, height: Math.max(0, baselineY - padT) })),
        createElement("clipPath", { id: belowId },
          createElement("rect", { x: padL, y: baselineY, width: CHART_W - padL - padR, height: Math.max(0, CHART_H - padB - baselineY) })),
      ),
      createElement("polygon", { points: fillPoints, fill: t.bull, fillOpacity: "0.15", clipPath: `url(#${aboveId})` }),
      createElement("polygon", { points: fillPoints, fill: t.bear, fillOpacity: "0.15", clipPath: `url(#${belowId})` }),
    ),
    // 2. 昨收基準線 + 標籤
    baseline > 0 && createElement("g", null,
      createElement("line", { x1: padL, y1: baselineY, x2: CHART_W - padR, y2: baselineY, stroke: t.inkDim, strokeWidth: sw(0.6), strokeDasharray: "4 3", opacity: "0.7" }),
      createElement("text", { x: padL - 4, y: baselineY + 3, textAnchor: "end", fill: t.inkDim, fontSize: fs(13), fontFamily: t.fontFamily }, "昨收"),
    ),
    // 3. 主價線(紅綠 clip)
    polyClose && baseline > 0 && createElement(Fragment, null,
      createElement("polyline", { points: polyClose, fill: "none", stroke: t.bull, strokeWidth: sw(1.2), clipPath: `url(#${aboveId})` }),
      createElement("polyline", { points: polyClose, fill: "none", stroke: t.bear, strokeWidth: sw(1.2), clipPath: `url(#${belowId})` }),
    ),
    polyClose && !(baseline > 0) && createElement("polyline", { points: polyClose, fill: "none", stroke: t.ink, strokeWidth: sw(1.2) }),
    // 4. 今日高低 marker
    todayHighIdx >= 0 && createElement("g", null,
      createElement("circle", { cx: scaleX(minutesByIdx[todayHighIdx]), cy: scaleY(todayHigh), r: sw(2.5), fill: priceColor(todayHigh, baseline, t) }),
      createElement("text", { x: scaleX(minutesByIdx[todayHighIdx]), y: scaleY(todayHigh) - 6, textAnchor: "middle", fill: priceColor(todayHigh, baseline, t), fontSize: fs(14), fontFamily: t.fontFamily }, fmtIndex(todayHigh)),
    ),
    todayLowIdx >= 0 && createElement("g", null,
      createElement("circle", { cx: scaleX(minutesByIdx[todayLowIdx]), cy: scaleY(todayLow), r: sw(2.5), fill: priceColor(todayLow, baseline, t) }),
      createElement("text", { x: scaleX(minutesByIdx[todayLowIdx]), y: scaleY(todayLow) + 13, textAnchor: "middle", fill: priceColor(todayLow, baseline, t), fontSize: fs(14), fontFamily: t.fontFamily }, fmtIndex(todayLow)),
    ),
    // 4.5 量能副圖(成交值):每分鐘成交值 bar,顏色比照個股 close vs open
    filteredCandles.length > 0 && createElement("g", null,
      createElement("line", {
        x1: padL, y1: CHART_H + VOL_GAP / 2, x2: CHART_W - padR, y2: CHART_H + VOL_GAP / 2,
        stroke: t.line, strokeWidth: sw(0.5), opacity: "0.6",
      }),
      createElement("text", {
        x: padL, y: CHART_H + VOL_GAP + VOL_PAD_T + 8, textAnchor: "start",
        fill: t.inkDim, fontSize: fs(13), fontFamily: t.fontFamily,
      }, "成交值(億)"),
      createElement("text", {
        x: CHART_W - padR - 2, y: CHART_H + VOL_GAP + VOL_PAD_T + 8, textAnchor: "end",
        fill: t.inkDim, fontSize: fs(13), fontFamily: t.fontFamily,
      }, fmtIndexVol(maxVolume)),
      ...filteredCandles.map((cd, i) => {
        const x = scaleX(minutesByIdx[i]) - volBarW / 2;
        const y = scaleVolY(cd.volume);
        const fill = cd.close > cd.open ? t.bull : cd.close < cd.open ? t.bear : t.inkDim;
        return createElement("rect", {
          key: i, x, y, width: volBarW, height: Math.max(0, TOTAL_H - y), fill, fillOpacity: "0.7",
        });
      }),
    ),
    // 5. X 軸時間 label(固定 6 點)
    ...[
      { min: 540, label: "9:00" }, { min: 600, label: "10:00" }, { min: 660, label: "11:00" },
      { min: 720, label: "12:00" }, { min: 780, label: "13:00" }, { min: 810, label: "13:30" },
    ].map(({ min, label }) => createElement("text", {
      key: min, x: scaleX(min), y: CHART_H - 8, textAnchor: "middle",
      fill: t.inkDim, fontSize: fs(14), fontFamily: t.fontFamily,
    }, label)),
  );
}
