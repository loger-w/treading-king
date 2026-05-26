import { useEffect, useMemo, useState } from "react";
import { useMXFCandles } from "../hooks/useMXFCandles";
import {
  scaleX_compressed,
  scaleY_clamped,
  sessionBoundaries,
  computeMA,
  CandlestickSeries,
  LineSeries,
  MALine,
  VolumeSubChart,
  type ChartSession,
} from "../lib/chart-svg";
import { dayOpenBaseline, type ViewRange } from "../lib/mxf-chart";

const TIMEFRAMES = [1, 5, 10, 15, 30, 60];
const CHART_W = 1000;
const CHART_H = 460;
const VOL_H = 80;
const PAD_L = 56;
const PAD_R = 56;
const PAD_T = 12;
const PAD_B = 28;
const MIN_CANDLE_PX = 6;

export function MXFIntradayChart() {
  const [tf, setTf] = useState(5);
  const [showVwap, setShowVwap] = useState(true);
  const [showMa, setShowMa] = useState(true);
  const [showVolume, setShowVolume] = useState(true);
  const [showHighLow, setShowHighLow] = useState(true);
  const [hover, setHover] = useState<{ idx: number } | null>(null);
  const [viewRange, setViewRange] = useState<ViewRange | null>(null);

  const { symbol, candles, currentSession, loading, error } = useMXFCandles(tf);

  const { sessions, yMin, yMax, ma5, ma20, todayHigh, todayLow, maxVolume } = useMemo(() => {
    if (candles.length === 0) {
      return {
        sessions: [] as ChartSession[],
        yMin: 0, yMax: 0,
        ma5: [] as number[], ma20: [] as number[],
        todayHigh: 0, todayLow: 0,
        maxVolume: 1,
      };
    }
    const sess: ChartSession[] = inferSessions(candles);

    const lows = candles.map((c) => c.low);
    const highs = candles.map((c) => c.high);
    const yMin = Math.min(...lows) * 0.998;
    const yMax = Math.max(...highs) * 1.002;

    const closes = candles.map((c) => c.close);
    const ma5 = computeMA(closes, 5);
    const ma20 = computeMA(closes, 20);

    const todayHigh = Math.max(...highs);
    const todayLow = Math.min(...lows);
    const maxVolume = Math.max(1, ...candles.map((c) => c.volume));
    return { sessions: sess, yMin, yMax, ma5, ma20, todayHigh, todayLow, maxVolume };
  }, [candles]);

  const innerW = CHART_W - PAD_L - PAD_R;
  const innerH = CHART_H - PAD_T - PAD_B - VOL_H - 8;

  // Init / reset viewRange when candles arrive or are cleared
  useEffect(() => {
    if (candles.length === 0) {
      setViewRange(null);
      return;
    }
    if (viewRange === null) {
      const maxVisible = Math.floor(innerW / MIN_CANDLE_PX);
      const startIdx = Math.max(0, candles.length - maxVisible);
      setViewRange({ startIdx, endIdx: candles.length - 1 });
    }
    // viewRange not in deps to avoid re-run when zoom/pan updates it
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candles.length, innerW]);

  const baselineOpen = dayOpenBaseline(candles, new Date());
  const latest = candles.length > 0 ? candles[candles.length - 1] : null;
  const change = latest && baselineOpen ? latest.close - baselineOpen : 0;
  const changePct = latest && baselineOpen ? (change / baselineOpen) * 100 : 0;
  const isUp = change > 0;
  const dirCls = isUp ? "text-bull" : change < 0 ? "text-bear" : "text-ink-muted";

  const sx = (iso: string) => PAD_L + scaleX_compressed(iso, sessions, innerW);
  const sy = (v: number) => PAD_T + scaleY_clamped(v, yMin, yMax, innerH);

  function handleMouseMove(e: React.MouseEvent<SVGSVGElement>) {
    if (candles.length === 0) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const svgX = ((e.clientX - rect.left) / rect.width) * CHART_W;
    const svgY = ((e.clientY - rect.top) / rect.height) * CHART_H;
    const chartBottomY = PAD_T + innerH;
    if (svgX < PAD_L || svgX > CHART_W - PAD_R || svgY < PAD_T || svgY > chartBottomY) {
      setHover(null);
      return;
    }
    // compressed X scale 下找最近 candle 改用 pixel 距離(非 minute-of-day)
    let bestIdx = 0;
    let bestDist = Math.abs(sx(candles[0].date) - svgX);
    for (let i = 1; i < candles.length; i++) {
      const cx = sx(candles[i].date);
      if (Number.isNaN(cx)) continue;
      const d = Math.abs(cx - svgX);
      if (d < bestDist) { bestDist = d; bestIdx = i; }
    }
    setHover({ idx: bestIdx });
  }

  function handleMouseLeave() {
    setHover(null);
  }

  if (loading) return <div className="p-8 text-center text-ink-muted">載入中…</div>;
  if (error) return <div className="p-8 text-center text-bear">{error}</div>;
  if (!symbol) return <div className="p-8 text-center text-ink-muted">無法取得 MXF 近月合約</div>;

  return (
    <div className="flex flex-col gap-3">
      {/* Top-left header — symbol + 大字現價 + 漲跌% */}
      <div className="mb-4">
        <div className="font-serif text-[22px] tracking-tight text-ink leading-tight font-medium">
          {symbol}
        </div>
        <div className="flex items-baseline gap-4 mt-1">
          <span className={`font-serif italic text-[44px] tabular-nums leading-none ${dirCls}`}>
            {latest ? latest.close.toFixed(0) : "—"}
          </span>
          {latest && baselineOpen && (
            <span className={`text-[18px] tabular-nums ${dirCls}`}>
              {isUp ? "▲" : change < 0 ? "▾" : "—"} {Math.abs(change).toFixed(0)} ({changePct >= 0 ? "+" : ""}{changePct.toFixed(2)}%)
            </span>
          )}
        </div>
      </div>

      {/* Toolbar */}
      <div className="flex items-center gap-6 text-sm flex-wrap">
        <span className="font-mono text-ink">{symbol}</span>
        <span className="label-tiny">
          {currentSession === "closed" ? "目前休市" : currentSession === "day" ? "日盤中" : "夜盤中"}
        </span>

        {/* Timeframe — underline tabs */}
        <div className="flex">
          {TIMEFRAMES.map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => setTf(t)}
              className={`px-3 py-1.5 border-b-2 transition-colors ${
                tf === t
                  ? "text-accent border-accent font-medium"
                  : "text-ink-dim border-transparent hover:text-ink"
              }`}
            >
              {t}m
            </button>
          ))}
        </div>

        {/* Overlay toggles — bordered chips */}
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => setShowVwap((v) => !v)}
            className={`px-3 py-1 border text-xs transition-colors ${
              showVwap
                ? "border-accent text-accent"
                : "border-line-strong text-ink-dim hover:text-ink hover:border-ink-dim"
            }`}
          >VWAP</button>
          <button
            type="button"
            onClick={() => setShowMa((v) => !v)}
            className={`px-3 py-1 border text-xs transition-colors ${
              showMa
                ? "border-accent text-accent"
                : "border-line-strong text-ink-dim hover:text-ink hover:border-ink-dim"
            }`}
          >MA</button>
          <button
            type="button"
            onClick={() => setShowVolume((v) => !v)}
            className={`px-3 py-1 border text-xs transition-colors ${
              showVolume
                ? "border-accent text-accent"
                : "border-line-strong text-ink-dim hover:text-ink hover:border-ink-dim"
            }`}
          >VOL</button>
          <button
            type="button"
            onClick={() => setShowHighLow((v) => !v)}
            className={`px-3 py-1 border text-xs transition-colors ${
              showHighLow
                ? "border-accent text-accent"
                : "border-line-strong text-ink-dim hover:text-ink hover:border-ink-dim"
            }`}
          >高/低</button>
        </div>
      </div>

      {/* SVG */}
      <svg
        viewBox={`0 0 ${CHART_W} ${CHART_H}`}
        style={{ width: "100%", height: "auto" }}
        className="cursor-crosshair"
        onMouseMove={handleMouseMove}
        onMouseLeave={handleMouseLeave}
      >
        {/* Y 軸格線 */}
        {[0, 0.25, 0.5, 0.75, 1].map((r) => {
          const y = PAD_T + r * innerH;
          return <line key={r} x1={PAD_L} x2={CHART_W - PAD_R} y1={y} y2={y} stroke="#eee" strokeDasharray="2 4" />;
        })}

        {/* session gap 虛線 */}
        {sessionBoundaries(sessions, innerW).map((g, i) => (
          <g key={i}>
            <line x1={PAD_L + g.gapStartPx} x2={PAD_L + g.gapStartPx} y1={PAD_T} y2={PAD_T + innerH} stroke="#bbb" strokeDasharray="3 3" />
            <line x1={PAD_L + g.gapEndPx} x2={PAD_L + g.gapEndPx} y1={PAD_T} y2={PAD_T + innerH} stroke="#bbb" strokeDasharray="3 3" />
          </g>
        ))}

        {/* 主圖 */}
        <CandlestickSeries candles={candles} scaleX={sx} scaleY={sy} width={innerW} />

        {/* VWAP */}
        {showVwap && <LineSeries candles={candles} scaleX={sx} scaleY={sy} field="average" stroke="#9aa0a6" dashed />}

        {/* MA */}
        {showMa && (
          <>
            <MALine candles={candles} maValues={ma5} scaleX={sx} scaleY={sy} stroke="#f59e0b" label="MA5" />
            <MALine candles={candles} maValues={ma20} scaleX={sx} scaleY={sy} stroke="#3b82f6" label="MA20" />
          </>
        )}

        {/* 今日高低標記 */}
        {showHighLow && candles.length > 0 && (
          <g>
            <line x1={PAD_L} x2={CHART_W - PAD_R} y1={sy(todayHigh)} y2={sy(todayHigh)} stroke="#d9534f" strokeWidth={0.5} strokeDasharray="1 3" />
            <text x={CHART_W - PAD_R + 4} y={sy(todayHigh) + 3} fontSize={10} fill="#d9534f">H {todayHigh}</text>
            <line x1={PAD_L} x2={CHART_W - PAD_R} y1={sy(todayLow)} y2={sy(todayLow)} stroke="#2e7d32" strokeWidth={0.5} strokeDasharray="1 3" />
            <text x={CHART_W - PAD_R + 4} y={sy(todayLow) + 3} fontSize={10} fill="#2e7d32">L {todayLow}</text>
          </g>
        )}

        {/* 量子圖 */}
        {showVolume && candles.length > 1 && (
          <VolumeSubChart
            candles={candles}
            scaleX={sx}
            yTop={PAD_T + innerH + 8}
            height={VOL_H}
            barWidth={Math.max(1, (innerW / candles.length) * 0.6)}
          />
        )}

        {/* Hover crosshair — snap 到最近 candle 的 close */}
        {hover && candles[hover.idx] && (() => {
          const c = candles[hover.idx];
          const lineX = sx(c.date);
          const lineY = sy(c.close);
          if (Number.isNaN(lineX)) return null;
          const verticalEndY = showVolume
            ? PAD_T + innerH + 8 + VOL_H
            : PAD_T + innerH;
          const d = new Date(c.date);
          const hh = String(d.getHours()).padStart(2, "0");
          const mm = String(d.getMinutes()).padStart(2, "0");
          const volBarY = PAD_T + innerH + 8 + VOL_H - (c.volume / maxVolume) * VOL_H;
          return (
            <g pointerEvents="none">
              <line x1={lineX} y1={PAD_T} x2={lineX} y2={verticalEndY}
                stroke="#8a8273" strokeWidth="0.5" strokeDasharray="3 3" opacity="0.7" />
              <line x1={PAD_L} y1={lineY} x2={CHART_W - PAD_R} y2={lineY}
                stroke="#8a8273" strokeWidth="0.5" strokeDasharray="3 3" opacity="0.7" />
              <circle cx={lineX} cy={lineY} r="2.5" fill="#ede4d3" />
              <rect x={2} y={lineY - 7} width={PAD_L - 6} height="14" rx="1.5"
                fill="#2e2a22" stroke="#8a8273" strokeWidth="0.5" />
              <text x={PAD_L - 4} y={lineY + 3} textAnchor="end"
                fill="#ede4d3" fontSize="12" className="tabular-nums font-medium">
                {c.close}
              </text>
              <rect x={lineX - 18} y={CHART_H - PAD_B + 2} width="36" height="14" rx="1.5"
                fill="#2e2a22" stroke="#8a8273" strokeWidth="0.5" />
              <text x={lineX} y={CHART_H - PAD_B + 12} textAnchor="middle"
                fill="#ede4d3" fontSize="12" className="tabular-nums font-medium">
                {hh}:{mm}
              </text>
              {showVolume && (
                <text x={CHART_W - PAD_R + 4} y={volBarY + 3} textAnchor="start"
                  fill="#ede4d3" fontSize="11" className="tabular-nums">
                  {c.volume}
                </text>
              )}
            </g>
          );
        })()}
      </svg>
    </div>
  );
}

// 從 candles 推 sessions(夜盤段 + 日盤段)
// 規則:遇到時間 gap > 1 小時的相鄰 candle = session 邊界
function inferSessions(candles: { date: string }[]): ChartSession[] {
  if (candles.length === 0) return [];
  const sess: ChartSession[] = [];
  let curStart = candles[0].date;
  for (let i = 1; i < candles.length; i++) {
    const prev = new Date(candles[i - 1].date).getTime();
    const cur = new Date(candles[i].date).getTime();
    if (cur - prev > 60 * 60 * 1000) {
      sess.push({ startIso: curStart, endIso: candles[i - 1].date });
      curStart = candles[i].date;
    }
  }
  sess.push({ startIso: curStart, endIso: candles[candles.length - 1].date });
  return sess;
}
