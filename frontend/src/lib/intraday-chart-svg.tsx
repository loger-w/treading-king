// 股票分時圖的共用畫圖層 — 網頁(IntradayChart)與 Discord bot 共用同一份 JSX。
// 顏色一律 inline hex(取自 tailwind.config.js 的 Editorial Dark);
// 不可用 Tailwind class 或 var(--color-…) —— resvg(bot 端)不解析。
import { createElement, Fragment } from "react";
import type { IntradayCandle, CdpLevels, CamarillaLevels, MaLevels } from "./api";
import { formatTickPrice, roundToNearestTick } from "./tick";
import { resolveCollisions, type LabelInput } from "./chart-labels";
import {
  MARKET_OPEN_MIN, MARKET_CLOSE_MIN, TRADING_MINUTES, minuteOfDay,
} from "./intraday-time";

export const INTRADAY_THEME = {
  bg: "#14110c",
  bull: "#e85a4f",   // 台股漲紅
  bear: "#7fc99a",   // 台股跌綠
  ink: "#ede4d3",
  inkMuted: "#d4c8b0",
  inkDim: "#8a8273",
  line: "#2e2a22",
  accent: "#e85a4f", // CDP
  camarilla: "#3b82f6",
  ma5: "#f0b429",
  ma20: "#b794f4",
  fontFamily: '"Inter Tight", system-ui, sans-serif',
};
export type ChartTheme = typeof INTRADAY_THEME;

export const CHART_W = 820;
export const CHART_H = 460;
export const PAD_L = 56;
export const PAD_R = 56;
export const PAD_T = 12;
export const PAD_B = 28;
export const VOL_GAP = 4;
export const VOL_H = 72;
export const VOL_PAD_T = 6;
export const TOTAL_H = CHART_H + VOL_GAP + VOL_H;

export interface ChartFlags {
  vwap: boolean; cdp: boolean; camarilla: boolean; volume: boolean; ma: boolean;
}

export interface IntradayChartInput {
  candles: IntradayCandle[];
  prevClose: number | null;
  cdp: CdpLevels | null;
  camarilla: CamarillaLevels | null;
  ma: MaLevels | null;
  flags: ChartFlags;
  theme?: ChartTheme;
}

export interface IntradayGeometry {
  yMin: number; yMax: number;
  scaleX: (m: number) => number;
  scaleY: (v: number) => number;
  polyClose: string; polyVwap: string;
  visibleCdpKeys: Array<"ah" | "nh" | "cdp" | "nl" | "al">;
  visibleCamKeys: Array<"h4" | "h3" | "h2" | "h1" | "l1" | "l2" | "l3" | "l4">;
  visibleMaKeys: Array<"sma_5" | "sma_20">;
  todayHigh: number; todayHighIdx: number; todayLow: number; todayLowIdx: number;
  maxVolume: number; scaleVolY: (v: number) => number; volBarW: number;
  resolvedLabels: ReturnType<typeof resolveCollisions>;
  minutesByIdx: number[];
  filteredCandles: IntradayCandle[];
}

// 台股慣例:平盤上紅、平盤下綠、剛好平盤中性。回傳 hex。
function priceColor(price: number, baseline: number, t: ChartTheme): string {
  if (price > baseline) return t.bull;
  if (price < baseline) return t.bear;
  return t.ink;
}

export function formatVolume(v: number): string {
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `${Math.round(v / 1_000)}K`;
  return String(v);
}

export function computeIntradayGeometry(input: IntradayChartInput): IntradayGeometry {
  const { candles, prevClose, cdp, camarilla, ma, flags } = input;
  const theme = input.theme ?? INTRADAY_THEME;

  // 擋試撮 / 盤後 candle:只留正盤時段(9:00 ≤ 分鐘 ≤ 13:30)
  const filteredCandles: IntradayCandle[] = candles.filter((c) => {
    const m = minuteOfDay(c.date);
    return m >= MARKET_OPEN_MIN && m <= MARKET_CLOSE_MIN;
  });

  if (filteredCandles.length === 0) {
    return {
      yMin: 0, yMax: 0,
      scaleX: () => 0, scaleY: () => 0,
      polyClose: "", polyVwap: "",
      visibleCdpKeys: [] as Array<"ah" | "nh" | "cdp" | "nl" | "al">,
      visibleCamKeys: [] as Array<"h4" | "h3" | "h2" | "h1" | "l1" | "l2" | "l3" | "l4">,
      visibleMaKeys: [] as Array<"sma_5" | "sma_20">,
      todayHigh: 0, todayHighIdx: -1, todayLow: 0, todayLowIdx: -1,
      maxVolume: 0, scaleVolY: (_: number) => 0, volBarW: 0,
      resolvedLabels: [] as ReturnType<typeof resolveCollisions>,
      minutesByIdx: [] as number[],
      filteredCandles: [] as IntradayCandle[],
    };
  }

  const closes = filteredCandles.map((c) => c.close);
  const vwaps = filteredCandles.map((c) => c.average);

  // 基準價 = 昨日收盤（台股漲跌停以昨收為基準）；prev_close 沒有時 fallback 今日開盤
  const refPrice = prevClose ?? filteredCandles[0].open;
  const refMin = refPrice * 0.9;
  const refMax = refPrice * 1.1;

  // CDP 5 線：過濾掉超出 ±10% 的 key
  const allCdpKeys = ["ah", "nh", "cdp", "nl", "al"] as const;
  const visibleCdpKeys: Array<typeof allCdpKeys[number]> = (flags.cdp && cdp)
    ? allCdpKeys.filter((k) => cdp[k] >= refMin && cdp[k] <= refMax)
    : [];

  // Camarilla 8 線:過濾掉超出 ±10% 的 key
  const allCamKeys = ["h4", "h3", "h2", "h1", "l1", "l2", "l3", "l4"] as const;
  const visibleCamKeys: Array<typeof allCamKeys[number]> = (flags.camarilla && camarilla)
    ? allCamKeys.filter((k) => camarilla[k] >= refMin && camarilla[k] <= refMax)
    : [];
  // 只顯示 4 條主 level 的 label(H3/L3/H4/L4),H1/H2/L1/L2 不顯示 label 避免擠
  const labeledCamKeys: Array<typeof allCamKeys[number]> = visibleCamKeys.filter(
    (k) => k === "h3" || k === "h4" || k === "l3" || k === "l4"
  );

  // Y 軸：±10% 為最小範圍，價格超出就拉大（隱藏的 CDP 不算）
  const priceMin = Math.min(...closes, ...vwaps);
  const priceMax = Math.max(...closes, ...vwaps);
  const yMin = Math.min(refMin, priceMin) * 0.998;
  const yMax = Math.max(refMax, priceMax) * 1.002;

  // 今日最高/最低 — 用 candle.high/low（聚合，含同分鐘 tick 波動），不是 close
  let todayHigh = filteredCandles[0].high;
  let todayHighIdx = 0;
  let todayLow = filteredCandles[0].low;
  let todayLowIdx = 0;
  for (let i = 1; i < filteredCandles.length; i++) {
    if (filteredCandles[i].high > todayHigh) { todayHigh = filteredCandles[i].high; todayHighIdx = i; }
    if (filteredCandles[i].low < todayLow) { todayLow = filteredCandles[i].low; todayLowIdx = i; }
  }

  const xRange = CHART_W - PAD_L - PAD_R;
  const yRange = CHART_H - PAD_T - PAD_B;
  // X 軸固定 9:00~13:30(分鐘 540~810);scaleX 吃「分鐘 of day」非 candle index
  const minutesByIdx = filteredCandles.map((c) => minuteOfDay(c.date));
  const scaleX = (m: number) =>
    PAD_L + ((m - MARKET_OPEN_MIN) / TRADING_MINUTES) * xRange;
  const scaleY = (v: number) => PAD_T + (1 - (v - yMin) / (yMax - yMin || 1)) * yRange;
  const polyClose = filteredCandles.map((c, i) => `${scaleX(minutesByIdx[i])},${scaleY(c.close)}`).join(" ");
  const polyVwap = filteredCandles.map((c, i) => `${scaleX(minutesByIdx[i])},${scaleY(c.average)}`).join(" ");

  // 成交量子圖 scale：0 到 maxVolume，pane 範圍 [volTop, volBottom]
  const maxVolume = Math.max(1, ...filteredCandles.map((c) => c.volume));
  const volTop = CHART_H + VOL_GAP + VOL_PAD_T;
  const volBottom = TOTAL_H;
  const scaleVolY = (v: number) =>
    volTop + (1 - v / maxVolume) * (volBottom - volTop);
  // bar 寬度：每根 candle 在 x 軸佔的格子寬 * 0.7（留間隙）；最少 1px
  // 固定 slot 寬 = xRange / 270 分;不隨 candle 數量伸縮
  const slotW = xRange / TRADING_MINUTES;
  const volBarW = Math.max(1, slotW * 0.7);

  // MA 可見性過濾(超出 ±10% 範圍的不畫,沿用 CDP 同邏輯)
  const allMaKeys = ["sma_5", "sma_20"] as const;
  const visibleMaKeys: Array<typeof allMaKeys[number]> = (flags.ma && ma)
    ? allMaKeys.filter((k) => {
        const v = ma[k];
        return v !== null && v >= refMin && v <= refMax;
      })
    : [];

  // 收集右邊 margin 所有 label,做碰撞撐開。
  // 顏色用 hex 直填(Tailwind theme colors 沒當 CSS var 暴露,inline style 解析會失敗)
  const labelInputs: LabelInput[] = [];
  if (flags.cdp && cdp) {
    for (const k of visibleCdpKeys) {
      labelInputs.push({
        originalY: scaleY(cdp[k]),
        text: formatTickPrice(cdp[k]),
        color: theme.accent,
      });
    }
  }
  if (flags.camarilla && camarilla) {
    for (const k of labeledCamKeys) {
      labelInputs.push({
        originalY: scaleY(camarilla[k]),
        text: formatTickPrice(camarilla[k]),
        color: theme.camarilla,
      });
    }
  }
  if (flags.ma && ma) {
    for (const k of visibleMaKeys) {
      const v = roundToNearestTick(ma[k]!);
      labelInputs.push({
        originalY: scaleY(v),
        text: formatTickPrice(v),
        color: k === "sma_5" ? theme.ma5 : theme.ma20,
      });
    }
  }
  if (flags.vwap && filteredCandles.length > 0) {
    const lastAvg = filteredCandles[filteredCandles.length - 1].average;
    labelInputs.push({
      originalY: scaleY(lastAvg),
      text: formatTickPrice(lastAvg),
      color: theme.inkDim,
    });
  }
  const resolvedLabels = resolveCollisions(
    labelInputs,
    16,
    [PAD_T, CHART_H - PAD_B],
  );

  return {
    yMin, yMax, scaleX, scaleY,
    polyClose, polyVwap, visibleCdpKeys, visibleCamKeys, visibleMaKeys,
    todayHigh, todayHighIdx, todayLow, todayLowIdx,
    maxVolume, scaleVolY, volBarW,
    resolvedLabels,
    minutesByIdx,
    filteredCandles,
  };
}

export interface IntradayChartStaticProps extends IntradayChartInput {
  geometry: IntradayGeometry;
}

// presentational 元件 — 所有靜態圖層,不含 hover crosshair、外層 <svg>、toggle 按鈕。
// 網頁與 bot 共用同一份 JSX;bot 端 resvg 只認 inline hex,不解析 Tailwind / CSS var。
export function IntradayChartStatic(props: IntradayChartStaticProps) {
  const t = props.theme ?? INTRADAY_THEME;
  const { flags, cdp, camarilla, ma } = props;
  const {
    scaleX, scaleY, scaleVolY, yMin: _yMin, yMax: _yMax, polyClose, polyVwap,
    visibleCdpKeys, visibleCamKeys, visibleMaKeys,
    todayHigh, todayHighIdx, todayLow, todayLowIdx,
    maxVolume, volBarW, resolvedLabels, minutesByIdx, filteredCandles,
  } = props.geometry;
  const baseline = props.prevClose ?? (filteredCandles[0]?.open ?? 0);
  if (filteredCandles.length === 0) return null;

  return createElement(Fragment, null,

    // ── 1. 紅綠背景填色 + clipPath defs ──────────────────────────────────────
    // 走勢線跟平盤之間,漲紅跌綠(看盤軟體典型樣式)
    // 一個多邊形(走勢→baseline 封閉區),clipPath 切上下兩半分別塗色
    baseline > 0 && (() => {
      const baselineY = scaleY(baseline);
      const lastIdx = filteredCandles.length - 1;
      const points = [
        `${scaleX(minutesByIdx[0])},${baselineY}`,
        ...filteredCandles.map((c, i) => `${scaleX(minutesByIdx[i])},${scaleY(c.close)}`),
        `${scaleX(minutesByIdx[lastIdx])},${baselineY}`,
      ].join(" ");
      return createElement(Fragment, null,
        createElement("defs", null,
          createElement("clipPath", { id: "above-baseline" },
            createElement("rect", {
              x: PAD_L, y: PAD_T,
              width: CHART_W - PAD_L - PAD_R,
              height: Math.max(0, baselineY - PAD_T),
            }),
          ),
          createElement("clipPath", { id: "below-baseline" },
            createElement("rect", {
              x: PAD_L, y: baselineY,
              width: CHART_W - PAD_L - PAD_R,
              height: Math.max(0, CHART_H - PAD_B - baselineY),
            }),
          ),
        ),
        createElement("polygon", { points, fill: t.bull, fillOpacity: "0.15", clipPath: "url(#above-baseline)" }),
        createElement("polygon", { points, fill: t.bear, fillOpacity: "0.15", clipPath: "url(#below-baseline)" }),
      );
    })(),

    // ── 2. Y 軸 ±2% 格線 ─────────────────────────────────────────────────────
    // 以昨收 baseline 為中心 (0%)，每 ±2% 一條 (±0/±2/±4/.../±10)
    // 每條 snap 到合法台股 tick，超出 [yMin, yMax] 範圍的不畫、tick 重複的 dedupe
    baseline > 0 && (() => {
      const baselineTick = roundToNearestTick(baseline);
      const linePrices = Array.from(new Set(
        [-10, -8, -6, -4, -2, 0, 2, 4, 6, 8, 10].map((pct) =>
          roundToNearestTick(baseline * (1 + pct / 100))
        )
      )).filter((v) => v >= props.geometry.yMin && v <= props.geometry.yMax);
      return createElement(Fragment, null,
        ...linePrices.map((vTick) => {
          const y = scaleY(vTick);
          const isBaseline = vTick === baselineTick;
          return createElement("g", { key: vTick },
            createElement("line", {
              x1: PAD_L, y1: y, x2: CHART_W - PAD_R, y2: y,
              stroke: t.line,
              strokeWidth: isBaseline ? 0.8 : 0.5,
              opacity: isBaseline ? 1 : 0.55,
            }),
            createElement("text", {
              x: PAD_L - 4, y: y + 3, textAnchor: "end",
              fill: isBaseline ? t.ink : t.inkDim,
              fontSize: 12, fontFamily: t.fontFamily,
            }, formatTickPrice(vTick)),
          );
        }),
      );
    })(),

    // ── 3. CDP 5 線 ──────────────────────────────────────────────────────────
    // 超出 ±10% 範圍的隱藏;label 留給統一 resolvedLabels render
    flags.cdp && cdp && visibleCdpKeys.length > 0 && createElement(Fragment, null,
      ...visibleCdpKeys.map((k) => createElement("line", {
        key: k,
        x1: PAD_L, y1: scaleY(cdp[k]), x2: CHART_W - PAD_R, y2: scaleY(cdp[k]),
        stroke: t.accent, strokeWidth: "0.6", strokeDasharray: "4 3", opacity: "0.6",
      })),
    ),

    // ── 4. Camarilla 8 線 ────────────────────────────────────────────────────
    // H3/L3/H4/L4 主反轉/突破位較粗、H1/H2/L1/L2 弱化
    flags.camarilla && camarilla && visibleCamKeys.length > 0 && createElement(Fragment, null,
      ...visibleCamKeys.map((k) => {
        const isMain = k === "h3" || k === "h4" || k === "l3" || k === "l4";
        return createElement("line", {
          key: `cam-${k}`,
          x1: PAD_L, y1: scaleY(camarilla[k]), x2: CHART_W - PAD_R, y2: scaleY(camarilla[k]),
          stroke: t.camarilla,
          strokeWidth: isMain ? "0.8" : "0.6",
          strokeDasharray: "2 4",
          opacity: isMain ? "0.75" : "0.4",
        });
      }),
    ),

    // ── 5. MA5(黃) / MA20(紫) 兩條水平線 ────────────────────────────────────
    // raw SMA 常落在非合法 tick — visibleMaKeys 已 snap 過,這裡只畫線
    // (label 由 resolvedLabels 統一處理碰撞撐開)
    flags.ma && ma && visibleMaKeys.length > 0 && createElement(Fragment, null,
      ...visibleMaKeys.map((k) => {
        const v = roundToNearestTick(ma[k]!);
        const isShort = k === "sma_5";
        return createElement("line", {
          key: k,
          x1: PAD_L, y1: scaleY(v), x2: CHART_W - PAD_R, y2: scaleY(v),
          stroke: isShort ? t.ma5 : t.ma20,
          strokeWidth: "0.6", strokeDasharray: "2 4", opacity: "0.7",
        });
      }),
    ),

    // ── 6. VWAP 線 ───────────────────────────────────────────────────────────
    // label 由 resolvedLabels 處理
    flags.vwap && polyVwap && createElement("polyline", {
      points: polyVwap, fill: "none",
      stroke: t.inkDim, strokeWidth: "1", strokeDasharray: "3 2",
    }),

    // ── 7. 右側 margin label(resolvedLabels) ─────────────────────────────────
    // 自動碰撞撐開,需要時加引導線
    ...resolvedLabels.map((lbl, i) => createElement("g", { key: `lbl-${i}` },
      lbl.y !== lbl.originalY && createElement("line", {
        x1: CHART_W - PAD_R, y1: lbl.originalY,
        x2: CHART_W - PAD_R + 4, y2: lbl.y,
        stroke: lbl.color, strokeWidth: "0.7", opacity: "0.5",
      }),
      createElement("text", {
        x: CHART_W - PAD_R + 6, y: lbl.y + 3, textAnchor: "start",
        fill: lbl.color, fontSize: 12, fontFamily: t.fontFamily,
      }, lbl.text),
    )),

    // ── 8. 主價線 ─────────────────────────────────────────────────────────────
    // 用 clipPath 切上下兩段,平盤上紅、平盤下綠(跟 fill 同步)
    polyClose && baseline > 0 && createElement(Fragment, null,
      createElement("polyline", {
        points: polyClose, fill: "none",
        stroke: t.bull, strokeWidth: "1", clipPath: "url(#above-baseline)",
      }),
      createElement("polyline", {
        points: polyClose, fill: "none",
        stroke: t.bear, strokeWidth: "1", clipPath: "url(#below-baseline)",
      }),
    ),
    // baseline 沒值時(極罕見)fallback 單條白線
    polyClose && !(baseline > 0) && createElement("polyline", {
      points: polyClose, fill: "none",
      stroke: t.ink, strokeWidth: "1",
    }),

    // ── 9. 今日高低 marker ────────────────────────────────────────────────────
    // 顏色跟著漲跌走 — 高/低點在平盤上紅、平盤下綠、剛好等於平盤中性
    // (例:低點若仍在平盤上,代表整天都漲,低點也用紅色)
    todayHighIdx >= 0 && createElement("g", null,
      createElement("circle", {
        cx: scaleX(minutesByIdx[todayHighIdx]), cy: scaleY(todayHigh), r: "2.5",
        fill: priceColor(todayHigh, baseline, t),
      }),
      createElement("text", {
        x: scaleX(minutesByIdx[todayHighIdx]),
        y: scaleY(todayHigh) - 6,
        textAnchor: "middle",
        fill: priceColor(todayHigh, baseline, t),
        fontSize: 12, fontFamily: t.fontFamily,
      }, formatTickPrice(todayHigh)),
    ),
    todayLowIdx >= 0 && createElement("g", null,
      createElement("circle", {
        cx: scaleX(minutesByIdx[todayLowIdx]), cy: scaleY(todayLow), r: "2.5",
        fill: priceColor(todayLow, baseline, t),
      }),
      createElement("text", {
        x: scaleX(minutesByIdx[todayLowIdx]),
        y: scaleY(todayLow) + 13,
        textAnchor: "middle",
        fill: priceColor(todayLow, baseline, t),
        fontSize: 12, fontFamily: t.fontFamily,
      }, formatTickPrice(todayLow)),
    ),

    // ── 10. 成交量子圖 ────────────────────────────────────────────────────────
    // bar 顏色用 candle 本身的方向（close vs open）
    flags.volume && filteredCandles.length > 0 && createElement("g", null,
      // 分隔線：主圖底部與成交量 pane 之間
      createElement("line", {
        x1: PAD_L, y1: CHART_H + VOL_GAP / 2,
        x2: CHART_W - PAD_R, y2: CHART_H + VOL_GAP / 2,
        stroke: t.line, strokeWidth: "0.5", opacity: "0.6",
      }),
      // 最大值 label（top-right of pane）
      createElement("text", {
        x: CHART_W - PAD_R - 2, y: CHART_H + VOL_GAP + VOL_PAD_T + 8,
        textAnchor: "end", fill: t.inkDim, fontSize: 11, fontFamily: t.fontFamily,
      }, formatVolume(maxVolume)),
      // "VOL" label（top-left）
      createElement("text", {
        x: PAD_L - 4, y: CHART_H + VOL_GAP + VOL_PAD_T + 8,
        textAnchor: "end", fill: t.inkDim, fontSize: 11, fontFamily: t.fontFamily,
      }, "Vol"),
      // bars
      ...filteredCandles.map((c, i) => {
        const x = scaleX(minutesByIdx[i]) - volBarW / 2;
        const y = scaleVolY(c.volume);
        const h = TOTAL_H - y;
        const fill = c.close > c.open ? t.bull : c.close < c.open ? t.bear : t.inkDim;
        return createElement("rect", {
          key: i, x, y, width: volBarW, height: Math.max(0, h),
          fill, fillOpacity: "0.7",
        });
      }),
    ),

    // ── 11. X 軸時間 label ────────────────────────────────────────────────────
    // 固定 6 個整點(9/10/11/12/13/13:30),不隨 candle 數量變動
    ...[
      { min: 540, label: "9:00" },
      { min: 600, label: "10:00" },
      { min: 660, label: "11:00" },
      { min: 720, label: "12:00" },
      { min: 780, label: "13:00" },
      { min: 810, label: "13:30" },
    ].map(({ min, label }) => createElement("text", {
      key: min, x: scaleX(min), y: CHART_H - 8, textAnchor: "middle",
      fill: t.inkDim, fontSize: 12, fontFamily: t.fontFamily,
    }, label)),
  );
}
