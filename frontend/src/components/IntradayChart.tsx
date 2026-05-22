import { useEffect, useMemo, useState } from "react";
import { api, type CdpLevels, type IntradayCandle, type MaLevels } from "../lib/api";
import { useLocalToggle } from "../hooks/useLocalToggle";
import { formatTickPrice, roundToNearestTick } from "../lib/tick";
import { resolveCollisions, type LabelInput } from "../lib/chart-labels";
import {
  MARKET_OPEN_MIN,
  MARKET_CLOSE_MIN,
  TRADING_MINUTES,
  minuteOfDay,
} from "../lib/intraday-time";

interface Props {
  symbol: string;
  name: string | null;
  candles: IntradayCandle[];
  prevClose: number | null;  // 昨日收盤，給漲跌% / Y 軸 ±10% 用
  inAnyBookmark: boolean;
  onOpenBookmarkDialog: () => void;
}

const CHART_W = 820;
const CHART_H = 460;
const PAD_L = 56;
const PAD_R = 56;  // 寬一點，把 CDP label 擠到外面去（不被即時走勢價線蓋）
const PAD_T = 12;
const PAD_B = 28;
const VOL_GAP = 4;     // 主圖 x label 與成交量子圖的間隔
const VOL_H = 72;      // 成交量子圖高度
const VOL_PAD_T = 6;   // 成交量子圖內上 padding（給最大值 label 留空間）
const TOTAL_H = CHART_H + VOL_GAP + VOL_H;

function formatVolume(v: number): string {
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `${Math.round(v / 1_000)}K`;
  return String(v);
}

// 台股慣例:平盤上紅、平盤下綠、剛好平盤中性
function priceColorClass(price: number, baseline: number): string {
  if (price > baseline) return "fill-bull";
  if (price < baseline) return "fill-bear";
  return "fill-ink";
}

export function IntradayChart({ symbol, name, candles, prevClose, inAnyBookmark, onOpenBookmarkDialog }: Props) {
  const [showVwap, setShowVwap] = useState(true);
  const [showCdp, setShowCdp] = useLocalToggle("tk:chart:cdp", false);
  const [showVolume, setShowVolume] = useLocalToggle("tk:chart:volume", true);
  const [showMa, setShowMa] = useLocalToggle("tk:chart:ma", false);
  const [cdp, setCdp] = useState<CdpLevels | null>(null);
  const [cdpError, setCdpError] = useState<string | null>(null);
  const [ma, setMa] = useState<MaLevels | null>(null);

  useEffect(() => {
    // 切 symbol 時先清舊 CDP — 避免新圖上殘留舊 CDP 線
    setCdp(null);
    setCdpError(null);
    if (!showCdp) return;
    api.cdp(symbol).then(setCdp).catch((e) =>
      setCdpError(e instanceof Error ? e.message : String(e))
    );
  }, [symbol, showCdp]);

  useEffect(() => {
    // 切 symbol 時先清舊 MA — 避免新圖上殘留舊值
    setMa(null);
    if (!showMa) return;
    api.ma(symbol).then(setMa).catch((e) => {
      console.warn("MA fetch failed:", e);
    });
  }, [symbol, showMa]);

  const {
    yMin, yMax, scaleX, scaleY,
    polyClose, polyVwap, visibleCdpKeys, visibleMaKeys,
    todayHigh, todayHighIdx, todayLow, todayLowIdx,
    maxVolume, scaleVolY, volBarW,
    resolvedLabels,
    minutesByIdx,
    filteredCandles,
  } = useMemo(() => {
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
    const visibleCdpKeys: Array<typeof allCdpKeys[number]> = (showCdp && cdp)
      ? allCdpKeys.filter((k) => cdp[k] >= refMin && cdp[k] <= refMax)
      : [];

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
    const visibleMaKeys: Array<typeof allMaKeys[number]> = (showMa && ma)
      ? allMaKeys.filter((k) => {
          const v = ma[k];
          return v !== null && v >= refMin && v <= refMax;
        })
      : [];

    // 收集右邊 margin 所有 label,做碰撞撐開。
    // 顏色用 hex 直填(Tailwind theme colors 沒當 CSS var 暴露,inline style 解析會失敗)
    const labelInputs: LabelInput[] = [];
    if (showCdp && cdp) {
      for (const k of visibleCdpKeys) {
        labelInputs.push({
          originalY: scaleY(cdp[k]),
          text: formatTickPrice(cdp[k]),
          color: "#e85a4f",  // accent 紅
        });
      }
    }
    if (showMa && ma) {
      for (const k of visibleMaKeys) {
        const v = roundToNearestTick(ma[k]!);
        labelInputs.push({
          originalY: scaleY(v),
          text: formatTickPrice(v),
          color: k === "sma_5" ? "#f0b429" : "#b794f4",  // ma5 黃 / ma20 紫
        });
      }
    }
    if (showVwap && filteredCandles.length > 0) {
      const lastAvg = filteredCandles[filteredCandles.length - 1].average;
      labelInputs.push({
        originalY: scaleY(lastAvg),
        text: formatTickPrice(lastAvg),
        color: "#8a8273",  // ink-dim
      });
    }
    const resolvedLabels = resolveCollisions(
      labelInputs,
      16,
      [PAD_T, CHART_H - PAD_B],
    );

    return {
      yMin, yMax, scaleX, scaleY,
      polyClose, polyVwap, visibleCdpKeys, visibleMaKeys,
      todayHigh, todayHighIdx, todayLow, todayLowIdx,
      maxVolume, scaleVolY, volBarW,
      resolvedLabels,
      minutesByIdx,
      filteredCandles,
    };
  }, [candles, cdp, showCdp, ma, showMa, showVwap, prevClose]);

  // hover crosshair state — 只跟 candle idx 走（價位由 candle.close 決定）
  const [hover, setHover] = useState<{ idx: number } | null>(null);

  function handleMouseMove(e: React.MouseEvent<SVGSVGElement>) {
    if (candles.length === 0 || minutesByIdx.length === 0) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const svgX = ((e.clientX - rect.left) / rect.width) * CHART_W;
    const svgY = ((e.clientY - rect.top) / rect.height) * CHART_H;
    // 只在 chart area 內才有 crosshair
    if (svgX < PAD_L || svgX > CHART_W - PAD_R || svgY < PAD_T || svgY > CHART_H - PAD_B) {
      setHover(null);
      return;
    }
    // svgX → 對應的分鐘 of day
    const ratio = (svgX - PAD_L) / (CHART_W - PAD_L - PAD_R);
    const mAtCursor = MARKET_OPEN_MIN + ratio * TRADING_MINUTES;
    // 超過最新 candle 的時間(未來區) → 不顯示 crosshair
    const latestM = minutesByIdx[minutesByIdx.length - 1];
    if (mAtCursor > latestM) {
      setHover(null);
      return;
    }
    // 找時間最接近的 candle(線性 scan 270 筆 OK)
    let bestIdx = 0;
    let bestDist = Math.abs(minutesByIdx[0] - mAtCursor);
    for (let i = 1; i < minutesByIdx.length; i++) {
      const d = Math.abs(minutesByIdx[i] - mAtCursor);
      if (d < bestDist) { bestDist = d; bestIdx = i; }
    }
    setHover({ idx: bestIdx });
  }

  function handleMouseLeave() {
    setHover(null);
  }

  const latest = filteredCandles[filteredCandles.length - 1];
  const first = filteredCandles[0];
  // 漲跌基準：昨日收盤；prev_close 沒拿到時 fallback 今日開盤
  const baseline = prevClose ?? (first ? first.open : 0);
  const change = latest && baseline ? latest.close - baseline : 0;
  const changePct = latest && baseline ? (change / baseline) * 100 : 0;
  const isUp = change > 0;
  const dirCls = isUp ? "text-bull" : change < 0 ? "text-bear" : "text-ink-muted";

  return (
    <div>
      {/* 股票資訊 header — 名稱 · 代號 + 大字股價 + 漲跌百分比 + 加入書籤按鈕 */}
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <div className="font-serif text-[22px] tracking-tight text-ink leading-tight">
            <span className="font-medium">{symbol}</span>
            <span className="ml-2 text-ink-muted">{name ?? "—"}</span>
          </div>
          <div className="flex items-baseline gap-4 mt-1">
            <span className={`font-serif italic text-[44px] tabular-nums leading-none ${dirCls}`}>
              {latest ? latest.close.toFixed(2) : "—"}
            </span>
            {latest && (
              <span className={`text-[18px] tabular-nums ${dirCls}`}>
                {isUp ? "▲" : change < 0 ? "▾" : "—"} {Math.abs(change).toFixed(2)} ({changePct >= 0 ? "+" : ""}{changePct.toFixed(2)}%)
              </span>
            )}
          </div>
        </div>
        <button
          type="button"
          onClick={onOpenBookmarkDialog}
          className={[
            "shrink-0 inline-flex items-center gap-1.5 px-3.5 py-1.5 text-2xs uppercase tracking-[1.5px] transition-all duration-150 cursor-pointer",
            inAnyBookmark
              ? "border border-bear/40 text-bear hover:border-bear"
              : "border border-ink-dim text-ink-muted hover:border-accent hover:text-accent",
          ].join(" ")}
          aria-label="加入或編輯書籤"
        >
          {inAnyBookmark ? "✎ 編輯書籤" : "+ 加入書籤"}
        </button>
      </div>

      {filteredCandles.length === 0 ? (
        <div className="h-[360px] flex items-center justify-center text-ink-dim font-serif italic">
          載入中…
        </div>
      ) : (
        <svg
          viewBox={`0 0 ${CHART_W} ${showVolume ? TOTAL_H : CHART_H}`}
          className="w-full h-auto cursor-crosshair"
          onMouseMove={handleMouseMove}
          onMouseLeave={handleMouseLeave}
        >
          {/* 紅綠背景填色 — 走勢線跟平盤之間,漲紅跌綠(看盤軟體典型樣式)
              一個多邊形(走勢→baseline 封閉區),clipPath 切上下兩半分別塗色 */}
          {baseline > 0 && (() => {
            const baselineY = scaleY(baseline);
            const lastIdx = filteredCandles.length - 1;
            const points = [
              `${scaleX(minutesByIdx[0])},${baselineY}`,
              ...filteredCandles.map((c, i) => `${scaleX(minutesByIdx[i])},${scaleY(c.close)}`),
              `${scaleX(minutesByIdx[lastIdx])},${baselineY}`,
            ].join(" ");
            return (
              <>
                <defs>
                  <clipPath id="above-baseline">
                    <rect x={PAD_L} y={PAD_T}
                      width={CHART_W - PAD_L - PAD_R}
                      height={Math.max(0, baselineY - PAD_T)} />
                  </clipPath>
                  <clipPath id="below-baseline">
                    <rect x={PAD_L} y={baselineY}
                      width={CHART_W - PAD_L - PAD_R}
                      height={Math.max(0, CHART_H - PAD_B - baselineY)} />
                  </clipPath>
                </defs>
                <polygon points={points} className="fill-bull" fillOpacity="0.15"
                  clipPath="url(#above-baseline)" />
                <polygon points={points} className="fill-bear" fillOpacity="0.15"
                  clipPath="url(#below-baseline)" />
              </>
            );
          })()}

          {/* Y 軸格線 — 以昨收 baseline 為中心 (0%)，每 ±2% 一條 (±0/±2/±4/.../±10)
              每條 snap 到合法台股 tick，超出 [yMin, yMax] 範圍的不畫、tick 重複的 dedupe */}
          {(() => {
            if (!baseline) return null;
            const baselineTick = roundToNearestTick(baseline);
            const linePrices = Array.from(new Set(
              [-10, -8, -6, -4, -2, 0, 2, 4, 6, 8, 10].map((pct) =>
                roundToNearestTick(baseline * (1 + pct / 100))
              )
            )).filter((v) => v >= yMin && v <= yMax);
            return linePrices.map((vTick) => {
              const y = scaleY(vTick);
              const isBaseline = vTick === baselineTick;
              return (
                <g key={vTick}>
                  <line x1={PAD_L} y1={y} x2={CHART_W - PAD_R} y2={y}
                    stroke="var(--color-line, #2e2a22)"
                    strokeWidth={isBaseline ? 0.8 : 0.5}
                    opacity={isBaseline ? 1 : 0.55} />
                  <text x={PAD_L - 4} y={y + 3} textAnchor="end"
                    className={`text-[12px] tabular-nums ${isBaseline ? "fill-ink" : "fill-ink-dim"}`}>
                    {formatTickPrice(vTick)}
                  </text>
                </g>
              );
            });
          })()}

          {/* CDP 5 線(超出 ±10% 範圍的隱藏)— label 留給統一 resolvedLabels render */}
          {showCdp && cdp && visibleCdpKeys.length > 0 && (
            <>
              {visibleCdpKeys.map((k) => (
                <line key={k}
                  x1={PAD_L} y1={scaleY(cdp[k])} x2={CHART_W - PAD_R} y2={scaleY(cdp[k])}
                  stroke="var(--color-accent, #e85a4f)" strokeWidth="0.6"
                  strokeDasharray="4 3" opacity="0.6" />
              ))}
            </>
          )}

          {/* MA5(黃) / MA20(紫) 兩條水平線。raw SMA 常落在非合法 tick — visibleMaKeys
              已 snap 過,這裡只畫線(label 由 resolvedLabels 統一處理碰撞撐開)。 */}
          {showMa && ma && visibleMaKeys.length > 0 && (
            <>
              {visibleMaKeys.map((k) => {
                const v = roundToNearestTick(ma[k]!);
                const isShort = k === "sma_5";
                const colorCls = isShort ? "stroke-ma5" : "stroke-ma20";
                return (
                  <line key={k}
                    x1={PAD_L} y1={scaleY(v)} x2={CHART_W - PAD_R} y2={scaleY(v)}
                    className={colorCls} strokeWidth="0.6"
                    strokeDasharray="2 4" opacity="0.7" />
                );
              })}
            </>
          )}

          {/* VWAP 線(label 由 resolvedLabels 處理) */}
          {showVwap && polyVwap && (
            <polyline points={polyVwap} fill="none"
              stroke="var(--color-ink-dim, #8a8273)" strokeWidth="1" strokeDasharray="3 2" />
          )}

          {/* 右邊 margin 統一 label render — 自動碰撞撐開,需要時加引導線 */}
          {resolvedLabels.map((lbl, i) => (
            <g key={`lbl-${i}`}>
              {lbl.y !== lbl.originalY && (
                <line x1={CHART_W - PAD_R} y1={lbl.originalY}
                  x2={CHART_W - PAD_R + 4} y2={lbl.y}
                  stroke={lbl.color} strokeWidth="0.7" opacity="0.5" />
              )}
              <text x={CHART_W - PAD_R + 6} y={lbl.y + 3} textAnchor="start"
                style={{ fill: lbl.color }}
                className="text-[12px] tabular-nums">
                {lbl.text}
              </text>
            </g>
          ))}

          {/* 主價線 — 用 clipPath 切上下兩段,平盤上紅、平盤下綠(跟 fill 同步) */}
          {polyClose && baseline > 0 && (
            <>
              <polyline points={polyClose} fill="none"
                className="stroke-bull" strokeWidth="1"
                clipPath="url(#above-baseline)" />
              <polyline points={polyClose} fill="none"
                className="stroke-bear" strokeWidth="1"
                clipPath="url(#below-baseline)" />
            </>
          )}
          {/* baseline 沒值時(極罕見)fallback 單條白線 */}
          {polyClose && !(baseline > 0) && (
            <polyline points={polyClose} fill="none"
              stroke="var(--color-ink, #ede4d3)" strokeWidth="1" />
          )}

          {/* 今日最高 / 最低 標記 + 數字
              顏色跟著漲跌走 — 高/低點在平盤上紅、平盤下綠、剛好等於平盤中性
              (例:低點若仍在平盤上,代表整天都漲,低點也用紅色) */}
          {todayHighIdx >= 0 && (() => {
            const cls = priceColorClass(todayHigh, baseline);
            return (
              <g>
                <circle cx={scaleX(minutesByIdx[todayHighIdx])} cy={scaleY(todayHigh)} r="2.5"
                  className={cls} />
                <text
                  x={scaleX(minutesByIdx[todayHighIdx])}
                  y={scaleY(todayHigh) - 6}
                  textAnchor="middle"
                  className={`${cls} text-[12px] tabular-nums font-medium`}
                >
                  {formatTickPrice(todayHigh)}
                </text>
              </g>
            );
          })()}
          {todayLowIdx >= 0 && (() => {
            const cls = priceColorClass(todayLow, baseline);
            return (
              <g>
                <circle cx={scaleX(minutesByIdx[todayLowIdx])} cy={scaleY(todayLow)} r="2.5"
                  className={cls} />
                <text
                  x={scaleX(minutesByIdx[todayLowIdx])}
                  y={scaleY(todayLow) + 13}
                  textAnchor="middle"
                  className={`${cls} text-[12px] tabular-nums font-medium`}
                >
                  {formatTickPrice(todayLow)}
                </text>
              </g>
            );
          })()}

          {/* 成交量子圖 — bar 顏色用 candle 本身的方向（close vs open） */}
          {showVolume && filteredCandles.length > 0 && (
            <g>
              {/* 分隔線：主圖底部與成交量 pane 之間 */}
              <line x1={PAD_L} y1={CHART_H + VOL_GAP / 2}
                x2={CHART_W - PAD_R} y2={CHART_H + VOL_GAP / 2}
                stroke="var(--color-line, #2e2a22)" strokeWidth="0.5" opacity="0.6" />
              {/* 最大值 label（top-right of pane）*/}
              <text x={CHART_W - PAD_R - 2} y={CHART_H + VOL_GAP + VOL_PAD_T + 8}
                textAnchor="end" className="fill-ink-dim text-[11px] tabular-nums">
                {formatVolume(maxVolume)}
              </text>
              {/* "VOL" label（top-left）*/}
              <text x={PAD_L - 4} y={CHART_H + VOL_GAP + VOL_PAD_T + 8}
                textAnchor="end" className="fill-ink-dim text-[11px] uppercase tracking-wide">
                Vol
              </text>
              {/* bars */}
              {filteredCandles.map((c, i) => {
                const x = scaleX(minutesByIdx[i]) - volBarW / 2;
                const y = scaleVolY(c.volume);
                const h = TOTAL_H - y;
                const cls = c.close > c.open
                  ? "fill-bull"
                  : c.close < c.open
                  ? "fill-bear"
                  : "fill-ink-dim";
                return (
                  <rect key={i} x={x} y={y} width={volBarW} height={Math.max(0, h)}
                    className={cls} fillOpacity="0.7" />
                );
              })}
            </g>
          )}

          {/* X 軸時間 label — 固定 6 個整點(9/10/11/12/13/13:30),
              不隨 candle 數量變動 */}
          {[
            { min: 540, label: "9:00" },
            { min: 600, label: "10:00" },
            { min: 660, label: "11:00" },
            { min: 720, label: "12:00" },
            { min: 780, label: "13:00" },
            { min: 810, label: "13:30" },
          ].map(({ min, label }) => (
            <text key={min} x={scaleX(min)} y={CHART_H - 8} textAnchor="middle"
              className="fill-ink-dim text-[12px] tabular-nums">{label}</text>
          ))}

          {/* Hover crosshair — 線跟價位 snap 到走勢線本身（不是 cursor 自由位置）*/}
          {hover && (() => {
            const candle = filteredCandles[hover.idx];
            const m = minutesByIdx[hover.idx];
            const lineX = scaleX(m);
            const lineY = scaleY(candle.close);
            // 直接從 minutesByIdx 拆出小時分鐘，不依賴 runtime locale
            const hh = String(Math.floor(m / 60)).padStart(2, "0");
            const mm = String(m % 60).padStart(2, "0");
            return (
              <g>
                {/* 垂直虛線（snap 到最近 candle）— 開啟成交量時延伸到子圖底部 */}
                <line x1={lineX} y1={PAD_T}
                  x2={lineX} y2={showVolume ? TOTAL_H : CHART_H - PAD_B}
                  stroke="var(--color-ink-dim, #8a8273)" strokeWidth="0.5"
                  strokeDasharray="3 3" opacity="0.7" />
                {/* 水平虛線（snap 到 candle close 的 y） */}
                <line x1={PAD_L} y1={lineY} x2={CHART_W - PAD_R} y2={lineY}
                  stroke="var(--color-ink-dim, #8a8273)" strokeWidth="0.5"
                  strokeDasharray="3 3" opacity="0.7" />
                {/* hover 對應的成交量 label（顯示在子圖右側 margin 外）*/}
                {showVolume && (
                  <text x={CHART_W - PAD_R + 4}
                    y={scaleVolY(candle.volume) + 3}
                    textAnchor="start"
                    className="fill-ink text-[11px] tabular-nums">
                    {formatVolume(candle.volume)}
                  </text>
                )}
                {/* candle close 上的 marker */}
                <circle cx={lineX} cy={lineY} r="2.5"
                  className="fill-ink" />
                {/* 左側 Y 軸：該 candle 的 close 價位（小色塊高亮） */}
                <rect x={2} y={lineY - 7} width={PAD_L - 6} height="14" rx="1.5"
                  fill="var(--color-line, #2e2a22)"
                  stroke="var(--color-ink-dim, #8a8273)" strokeWidth="0.5" />
                <text x={PAD_L - 4} y={lineY + 3} textAnchor="end"
                  className="fill-ink text-[12px] tabular-nums font-medium">
                  {formatTickPrice(candle.close)}
                </text>
                {/* 底部 X 軸：該 candle 的時間（小色塊高亮） */}
                <rect x={lineX - 18} y={CHART_H - PAD_B + 2} width="36" height="14" rx="1.5"
                  fill="var(--color-line, #2e2a22)"
                  stroke="var(--color-ink-dim, #8a8273)" strokeWidth="0.5" />
                <text x={lineX} y={CHART_H - PAD_B + 12} textAnchor="middle"
                  className="fill-ink text-[12px] tabular-nums font-medium">
                  {hh}:{mm}
                </text>
              </g>
            );
          })()}
        </svg>
      )}

      {/* Toggle 按鈕（VWAP / CDP / VOL / MA） */}
      <div className="mt-2 flex justify-end gap-2 border-t border-line pt-2 text-xs">
        <button
          type="button"
          onClick={() => setShowVwap((v) => !v)}
          className={`px-2 py-1 border ${showVwap ? "border-accent text-accent" : "border-line text-ink-dim"}`}
        >{showVwap ? "✓" : ""} VWAP</button>
        <button
          type="button"
          onClick={() => setShowCdp((v) => !v)}
          className={`px-2 py-1 border ${showCdp ? "border-accent text-accent" : "border-line text-ink-dim"}`}
        >{showCdp ? "✓" : ""} CDP</button>
        <button
          type="button"
          onClick={() => setShowVolume((v) => !v)}
          className={`px-2 py-1 border ${showVolume ? "border-accent text-accent" : "border-line text-ink-dim"}`}
        >{showVolume ? "✓" : ""} VOL</button>
        <button
          type="button"
          onClick={() => setShowMa((v) => !v)}
          className={`px-2 py-1 border ${showMa ? "border-accent text-accent" : "border-line text-ink-dim"}`}
        >{showMa ? "✓" : ""} MA</button>
      </div>
      {showCdp && cdpError && (
        <div className="mt-1 text-xs text-bear">CDP 無資料：{cdpError}</div>
      )}
    </div>
  );
}
