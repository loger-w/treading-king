import { useMemo, useState } from "react";
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

const TIMEFRAMES = [1, 5, 10, 15, 30, 60];
const CHART_W = 1000;
const CHART_H = 460;
const VOL_H = 80;
const PAD_L = 56;
const PAD_R = 56;
const PAD_T = 12;
const PAD_B = 28;

type ChartMode = "candle" | "line";

export function MXFIntradayChart() {
  const [tf, setTf] = useState(5);
  const [mode, setMode] = useState<ChartMode>("candle");
  const [showVwap, setShowVwap] = useState(true);
  const [showMa, setShowMa] = useState(true);
  const [showVolume, setShowVolume] = useState(true);
  const [showHighLow, setShowHighLow] = useState(true);

  const { symbol, candles, currentSession, loading, error } = useMXFCandles(tf);

  const { sessions, yMin, yMax, ma5, ma20, todayHigh, todayLow } = useMemo(() => {
    if (candles.length === 0) {
      return {
        sessions: [] as ChartSession[],
        yMin: 0, yMax: 0,
        ma5: [] as number[], ma20: [] as number[],
        todayHigh: 0, todayLow: 0,
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
    return { sessions: sess, yMin, yMax, ma5, ma20, todayHigh, todayLow };
  }, [candles]);

  const innerW = CHART_W - PAD_L - PAD_R;
  const innerH = CHART_H - PAD_T - PAD_B - VOL_H - 8;

  const sx = (iso: string) => PAD_L + scaleX_compressed(iso, sessions, innerW);
  const sy = (v: number) => PAD_T + scaleY_clamped(v, yMin, yMax, innerH);

  if (loading) return <div className="p-8 text-center text-ink-muted">載入中…</div>;
  if (error) return <div className="p-8 text-center text-bear">{error}</div>;
  if (!symbol) return <div className="p-8 text-center text-ink-muted">無法取得 MXF 近月合約</div>;

  return (
    <div className="flex flex-col gap-3">
      {/* Toolbar */}
      <div className="flex items-center gap-4 text-sm flex-wrap">
        <span className="font-mono">{symbol}</span>
        <span className="label-tiny">{currentSession === "closed" ? "目前休市" : currentSession === "day" ? "日盤中" : "夜盤中"}</span>
        <div className="flex gap-1">
          {TIMEFRAMES.map((t) => (
            <button
              key={t}
              type="button"
              className={`px-2 py-0.5 rounded ${tf === t ? "bg-ink text-paper" : "hover:bg-line"}`}
              onClick={() => setTf(t)}
            >
              {t}m
            </button>
          ))}
        </div>
        <div className="flex gap-1">
          <button type="button" className={`px-2 py-0.5 rounded ${mode === "candle" ? "bg-ink text-paper" : "hover:bg-line"}`} onClick={() => setMode("candle")}>K 線</button>
          <button type="button" className={`px-2 py-0.5 rounded ${mode === "line" ? "bg-ink text-paper" : "hover:bg-line"}`} onClick={() => setMode("line")}>走勢線</button>
        </div>
        <label className="flex gap-1 items-center"><input type="checkbox" checked={showVwap} onChange={(e) => setShowVwap(e.target.checked)} /> VWAP</label>
        <label className="flex gap-1 items-center"><input type="checkbox" checked={showMa} onChange={(e) => setShowMa(e.target.checked)} /> MA</label>
        <label className="flex gap-1 items-center"><input type="checkbox" checked={showVolume} onChange={(e) => setShowVolume(e.target.checked)} /> 量</label>
        <label className="flex gap-1 items-center"><input type="checkbox" checked={showHighLow} onChange={(e) => setShowHighLow(e.target.checked)} /> 高/低</label>
      </div>

      {/* SVG */}
      <svg viewBox={`0 0 ${CHART_W} ${CHART_H}`} style={{ width: "100%", height: "auto" }}>
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
        {mode === "candle" ? (
          <CandlestickSeries candles={candles} scaleX={sx} scaleY={sy} width={innerW} />
        ) : (
          <LineSeries candles={candles} scaleX={sx} scaleY={sy} field="close" stroke="#d9534f" />
        )}

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
