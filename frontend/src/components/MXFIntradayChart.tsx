import { useEffect, useMemo, useRef, useState } from "react";
import { useMXFCandles } from "../hooks/useMXFCandles";
import {
  buildSpans,
  scaleXFromSpans,
  scaleY_clamped,
  boundariesFromSpans,
  computeMA,
  CandlestickSeries,
  LineSeries,
  MALine,
  VolumeSubChart,
  type ChartSession,
} from "../lib/chart-svg";
import { dayOpenBaseline, computeNewViewRange, pickInterval, type ViewRange } from "../lib/mxf-chart";
import { minuteOfDay } from "../lib/intraday-time";

const TIMEFRAMES = [1, 5, 10, 15, 30, 60];
const CHART_W = 1000;
const CHART_H = 460;
const VOL_H = 80;
const PAD_L = 56;
const PAD_R = 56;
const PAD_T = 12;
const PAD_B = 28;
const MIN_CANDLE_PX = 6;

export function MXFIntradayChart({ active = true }: { active?: boolean }) {
  const [tf, setTf] = useState(5);
  const [showVwap, setShowVwap] = useState(true);
  const [showMa, setShowMa] = useState(true);
  const [showVolume, setShowVolume] = useState(true);
  const [showHighLow, setShowHighLow] = useState(true);
  const [hover, setHover] = useState<{ idx: number } | null>(null);
  const [viewRange, setViewRange] = useState<ViewRange | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const dragStartX = useRef(0);
  const dragStartRange = useRef<ViewRange | null>(null);
  const prevLenRef = useRef(0);

  const { symbol, candles, currentSession, loading, error } = useMXFCandles(tf, active);

  const { ma5, ma20 } = useMemo(() => {
    if (candles.length === 0) {
      return { ma5: [] as number[], ma20: [] as number[] };
    }
    const closes = candles.map((c) => c.close);
    const ma5 = computeMA(closes, 5);
    const ma20 = computeMA(closes, 20);
    return { ma5, ma20 };
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
      return;
    }
    // candles 縮水時(每天 15:00 REST 的 afterhours 換成剛開始的新夜盤)殘留的
    // viewRange 越界 → slice 空陣列 → y 軸 ±Infinity 整片空白且不自癒,重新右錨夾回
    if (viewRange.endIdx > candles.length - 1) {
      const span = viewRange.endIdx - viewRange.startIdx;
      const endIdx = candles.length - 1;
      setViewRange({ startIdx: Math.max(0, endIdx - span), endIdx });
    }
    // viewRange not in deps to avoid re-run when zoom/pan updates it
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candles.length, innerW]);

  // WS push 新 candle: 貼右就跟、否則凍結
  useEffect(() => {
    const prevLen = prevLenRef.current;
    prevLenRef.current = candles.length;
    if (!viewRange) return;
    if (candles.length <= prevLen) return;  // not a push (or reset)
    const anchoredRight = viewRange.endIdx === prevLen - 1;
    if (!anchoredRight) return;
    const shift = candles.length - prevLen;
    setViewRange({ startIdx: viewRange.startIdx + shift, endIdx: viewRange.endIdx + shift });
    // viewRange not in deps to avoid loop — we only react to length growth
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candles.length]);

  const {
    visibleCandles, visibleSessions, visibleMa5, visibleMa20,
    yMinView, yMaxView, todayHighInView, todayLowInView, maxVolumeInView,
    labelPoints,
  } = useMemo(() => {
    if (!viewRange || candles.length === 0) {
      return {
        visibleCandles: [] as typeof candles,
        visibleSessions: [] as ChartSession[],
        visibleMa5: [] as number[],
        visibleMa20: [] as number[],
        yMinView: 0, yMaxView: 0,
        todayHighInView: { value: 0, idx: -1 },
        todayLowInView: { value: 0, idx: -1 },
        maxVolumeInView: 1,
        labelPoints: [] as { iso: string; label: string }[],
      };
    }
    const start = viewRange.startIdx;
    const end = viewRange.endIdx + 1;
    const visibleCandles = candles.slice(start, end);
    const visibleSessions = inferSessions(visibleCandles);
    const visibleMa5 = ma5.slice(start, end);
    const visibleMa20 = ma20.slice(start, end);

    const lows = visibleCandles.map((c) => c.low);
    const highs = visibleCandles.map((c) => c.high);
    const yMinView = Math.min(...lows) * 0.998;
    const yMaxView = Math.max(...highs) * 1.002;

    // 今日高低 marker — 從完整 candles 算，但只在 idx 落在 viewRange 內時才顯示
    let todayHighIdx = 0;
    let todayLowIdx = 0;
    for (let i = 1; i < candles.length; i++) {
      if (candles[i].high > candles[todayHighIdx].high) todayHighIdx = i;
      if (candles[i].low < candles[todayLowIdx].low) todayLowIdx = i;
    }
    const todayHighInView = todayHighIdx >= start && todayHighIdx < end
      ? { value: candles[todayHighIdx].high, idx: todayHighIdx - start }
      : { value: 0, idx: -1 };
    const todayLowInView = todayLowIdx >= start && todayLowIdx < end
      ? { value: candles[todayLowIdx].low, idx: todayLowIdx - start }
      : { value: 0, idx: -1 };

    const maxVolumeInView = Math.max(1, ...visibleCandles.map((c) => c.volume));

    // 1. visibleMinutesSum = sum of each visible session's duration in minutes
    const MIN_MS = 60 * 1000;
    const visibleMinutesSum = visibleSessions.reduce((acc, s) => {
      return acc + (new Date(s.endIso).getTime() - new Date(s.startIso).getTime()) / MIN_MS;
    }, 0);
    const interval = pickInterval(visibleMinutesSum);

    // 2. For each visible session, generate HH:MM points at interval marks
    //    A point at HH:MM means: (hour * 60 + min) % interval === 0
    interface TimeLabel { iso: string; label: string; }
    const labelPoints: TimeLabel[] = [];
    for (const s of visibleSessions) {
      const sStart = new Date(s.startIso);
      const sEnd = new Date(s.endIso);
      // round sStart up to next interval boundary (UTC+8 explicit)
      const startMin = minuteOfDay(s.startIso);
      const remainder = startMin % interval;
      const firstAddMin = remainder === 0 ? 0 : interval - remainder;
      const cursor = new Date(sStart.getTime() + firstAddMin * MIN_MS);
      while (cursor <= sEnd) {
        // UTC+8 explicit minute-of-day for label text
        const utcMin = cursor.getUTCHours() * 60 + cursor.getUTCMinutes();
        const taipeiMin = (utcMin + 8 * 60) % (24 * 60);
        const hh = String(Math.floor(taipeiMin / 60)).padStart(2, "0");
        const mm = String(taipeiMin % 60).padStart(2, "0");
        labelPoints.push({ iso: cursor.toISOString(), label: `${hh}:${mm}` });
        cursor.setTime(cursor.getTime() + interval * MIN_MS);
      }
    }

    return {
      visibleCandles, visibleSessions, visibleMa5, visibleMa20,
      yMinView, yMaxView, todayHighInView, todayLowInView, maxVolumeInView,
      labelPoints,
    };
  }, [candles, viewRange, ma5, ma20]);

  // candles 才會改變結果(每 30s/每分鐘),hover/drag 的每個 mousemove 都 re-render,
  // 不 memo 的話每次都對全量 candles(1m 約 1100 根)做 O(n) Date 解析
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const baselineOpen = useMemo(() => dayOpenBaseline(candles, new Date()), [candles]);
  const latest = candles.length > 0 ? candles[candles.length - 1] : null;
  const change = latest && baselineOpen ? latest.close - baselineOpen : 0;
  const changePct = latest && baselineOpen ? (change / baselineOpen) * 100 : 0;
  const isUp = change > 0;
  const dirCls = isUp ? "text-bull" : change < 0 ? "text-bear" : "text-ink-muted";

  // spans 預建一次:sx 每根 K 棒/每個 mousemove hit-test 都呼叫,
  // 不緩存的話每次都重新解析全部 session 的 ISO 日期
  const spans = useMemo(() => buildSpans(visibleSessions, innerW), [visibleSessions, innerW]);
  const gapBoundaries = useMemo(() => boundariesFromSpans(spans), [spans]);
  const sx = (iso: string) => PAD_L + scaleXFromSpans(iso, spans);
  const sy = (v: number) => PAD_T + scaleY_clamped(v, yMinView, yMaxView, innerH);

  function handleMouseDown(e: React.MouseEvent<SVGSVGElement>) {
    if (!viewRange) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const svgX = ((e.clientX - rect.left) / rect.width) * CHART_W;
    if (svgX < PAD_L || svgX > CHART_W - PAD_R) return;
    // 擋掉預設的文字選取 (拖曳時瀏覽器原生 selection 會把畫面外的文字框起來)
    e.preventDefault();
    setIsDragging(true);
    setHover(null);
    dragStartX.current = e.clientX;
    dragStartRange.current = viewRange;
  }

  function handleMouseUp() {
    setIsDragging(false);
  }

  function handleMouseMove(e: React.MouseEvent<SVGSVGElement>) {
    if (isDragging && dragStartRange.current) {
      const rect = e.currentTarget.getBoundingClientRect();
      const dx = e.clientX - dragStartX.current;
      const size = dragStartRange.current.endIdx - dragStartRange.current.startIdx + 1;
      // pxPerCandle in actual viewport pixels
      const viewportPxPerCandle = (rect.width * (innerW / CHART_W)) / size;
      const deltaIdx = -Math.round(dx / viewportPxPerCandle);
      let newStart = dragStartRange.current.startIdx + deltaIdx;
      newStart = Math.max(0, Math.min(candles.length - size, newStart));
      setViewRange({ startIdx: newStart, endIdx: newStart + size - 1 });
      return;
    }
    // Crosshair hit-test — iterates visibleCandles
    if (visibleCandles.length === 0) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const svgX = ((e.clientX - rect.left) / rect.width) * CHART_W;
    const svgY = ((e.clientY - rect.top) / rect.height) * CHART_H;
    const chartBottomY = PAD_T + innerH;
    if (svgX < PAD_L || svgX > CHART_W - PAD_R || svgY < PAD_T || svgY > chartBottomY) {
      setHover(null);
      return;
    }
    let bestIdx = 0;
    let bestDist = Math.abs(sx(visibleCandles[0].date) - svgX);
    for (let i = 1; i < visibleCandles.length; i++) {
      const cx = sx(visibleCandles[i].date);
      if (Number.isNaN(cx)) continue;
      const d = Math.abs(cx - svgX);
      if (d < bestDist) { bestDist = d; bestIdx = i; }
    }
    setHover({ idx: bestIdx });
  }

  function handleMouseLeave() {
    setHover(null);
    setIsDragging(false);
  }

  // wheel 縮放走原生 non-passive listener:React 17+ 把 wheel 掛成 passive,
  // synthetic onWheel 裡 preventDefault 無效——頁面跟著捲動、console 噴 intervention 錯誤
  const svgRef = useRef<SVGSVGElement | null>(null);
  const wheelRef = useRef<(e: WheelEvent) => void>(() => {});
  wheelRef.current = (e: WheelEvent) => {
    if (!viewRange || !svgRef.current) return;
    const rect = svgRef.current.getBoundingClientRect();
    const svgX = ((e.clientX - rect.left) / rect.width) * CHART_W;
    if (svgX < PAD_L || svgX > CHART_W - PAD_R) return;
    const mouseRatio = (svgX - PAD_L) / innerW;
    setViewRange(computeNewViewRange({
      prevRange: viewRange,
      mouseRatio,
      deltaY: e.deltaY,
      candlesLen: candles.length,
      innerW,
      minCandlePx: MIN_CANDLE_PX,
    }));
  };

  // 顯示 chart 還是 placeholder 由 loading/error/!symbol 決定，但 header + toolbar 永遠 render
  // 避免切 timeframe 時整個面板閃成「載入中...」。
  // error 只在沒有任何資料時整塊替換——hook 失敗時刻意保留舊 candles(不閃白),
  // 元件不能用 error placeholder 把還能顯示的圖蓋掉 30 秒
  const placeholder = loading
    ? { text: "載入中…", className: "text-ink-muted" }
    : error && candles.length === 0
    ? { text: error, className: "text-bear" }
    : !symbol
    ? { text: "無法取得 MXF 近月合約", className: "text-ink-muted" }
    : null;

  const chartVisible = placeholder == null;
  useEffect(() => {
    const el = svgRef.current;
    if (!chartVisible || !el) return;
    const onWheel = (e: WheelEvent) => { e.preventDefault(); wheelRef.current(e); };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [chartVisible]);

  return (
    <div className="flex flex-col gap-3">
      {/* Top-left header — symbol + 中文名 + 大字現價 + 漲跌% */}
      <div className="mb-4">
        <div className="font-serif text-[22px] tracking-tight text-ink leading-tight">
          <span className="font-medium">{symbol ?? "—"}</span>
          <span className="ml-2 text-ink-muted">小型台指</span>
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

      {/* Chart area — placeholder OR SVG (header+toolbar 上面已 render，這裡只切換 chart 內容) */}
      {placeholder ? (
        <div className={`aspect-[1000/460] w-full flex items-center justify-center font-serif italic ${placeholder.className}`}>
          {placeholder.text}
        </div>
      ) : (
      <>
      {error && <div className="text-2xs text-bear">⚠ 更新失敗(顯示前次資料):{error}</div>}
      <svg
        ref={svgRef}
        viewBox={`0 0 ${CHART_W} ${CHART_H}`}
        style={{ width: "100%", height: "auto" }}
        className={isDragging ? "cursor-grabbing" : "cursor-crosshair"}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseLeave}
      >
        {/* Y 軸格線 */}
        {[0, 0.25, 0.5, 0.75, 1].map((r) => {
          const y = PAD_T + r * innerH;
          return <line key={r} x1={PAD_L} x2={CHART_W - PAD_R} y1={y} y2={y} stroke="#eee" strokeDasharray="2 4" />;
        })}

        {/* session gap 虛線 */}
        {gapBoundaries.map((g, i) => (
          <g key={i}>
            <line x1={PAD_L + g.gapStartPx} x2={PAD_L + g.gapStartPx} y1={PAD_T} y2={PAD_T + innerH} stroke="#bbb" strokeDasharray="3 3" />
            <line x1={PAD_L + g.gapEndPx} x2={PAD_L + g.gapEndPx} y1={PAD_T} y2={PAD_T + innerH} stroke="#bbb" strokeDasharray="3 3" />
          </g>
        ))}

        {/* Session boundary markers: 第一個 session + 每個 gap 之後的新 session */}
        {visibleSessions.map((s, i) => {
          const startX = i === 0
            ? sx(visibleCandles[0]?.date ?? s.startIso)
            : PAD_L + gapBoundaries[i - 1].gapEndPx;
          if (Number.isNaN(startX)) return null;
          // Taipei-time date + sessionType derived from session start
          const startEpoch = new Date(s.startIso).getTime();
          const tpe = new Date(startEpoch + 8 * 60 * 60 * 1000);
          const month = tpe.getUTCMonth() + 1;
          const day = tpe.getUTCDate();
          const hour = Math.floor(minuteOfDay(s.startIso) / 60);
          const sessionType = (hour < 8 || hour >= 14) ? "夜盤" : "日盤";
          return (
            <text key={`sm-${i}`} x={startX + 4} y={PAD_T - 4} textAnchor="start"
              className="fill-ink-dim text-[10px]">
              {`${month}/${day} ${sessionType}`}
            </text>
          );
        })}

        {/* 主圖 */}
        <CandlestickSeries candles={visibleCandles} scaleX={sx} scaleY={sy} />

        {/* VWAP */}
        {showVwap && <LineSeries candles={visibleCandles} scaleX={sx} scaleY={sy} field="average" stroke="#9aa0a6" dashed />}

        {/* MA */}
        {showMa && (
          <>
            <MALine candles={visibleCandles} maValues={visibleMa5} scaleX={sx} scaleY={sy} stroke="#f59e0b" label="MA5" />
            <MALine candles={visibleCandles} maValues={visibleMa20} scaleX={sx} scaleY={sy} stroke="#3b82f6" label="MA20" />
          </>
        )}

        {/* 今日高低標記 */}
        {showHighLow && todayHighInView.idx >= 0 && (
          <g>
            <line x1={PAD_L} x2={CHART_W - PAD_R} y1={sy(todayHighInView.value)} y2={sy(todayHighInView.value)}
              stroke="#d9534f" strokeWidth={0.5} strokeDasharray="1 3" />
            <text x={CHART_W - PAD_R + 4} y={sy(todayHighInView.value) + 3} fontSize={10} fill="#d9534f">
              H {todayHighInView.value}
            </text>
          </g>
        )}
        {showHighLow && todayLowInView.idx >= 0 && (
          <g>
            <line x1={PAD_L} x2={CHART_W - PAD_R} y1={sy(todayLowInView.value)} y2={sy(todayLowInView.value)}
              stroke="#2e7d32" strokeWidth={0.5} strokeDasharray="1 3" />
            <text x={CHART_W - PAD_R + 4} y={sy(todayLowInView.value) + 3} fontSize={10} fill="#2e7d32">
              L {todayLowInView.value}
            </text>
          </g>
        )}

        {/* 量子圖 */}
        {showVolume && visibleCandles.length > 1 && (
          <VolumeSubChart
            candles={visibleCandles}
            scaleX={sx}
            yTop={PAD_T + innerH + 8}
            height={VOL_H}
            barWidth={Math.max(1, (innerW / visibleCandles.length) * 0.6)}
          />
        )}

        {/* Time axis labels — adaptive interval, drop overlaps < 40px */}
        {(() => {
          if (labelPoints.length === 0) return null;
          const rendered: { x: number; label: string }[] = [];
          for (const lp of labelPoints) {
            const x = sx(lp.iso);
            if (Number.isNaN(x)) continue;
            if (rendered.length > 0 && Math.abs(x - rendered[rendered.length - 1].x) < 40) continue;
            rendered.push({ x, label: lp.label });
          }
          return rendered.map((r, i) => (
            <text key={i} x={r.x} y={CHART_H - PAD_B + 14} textAnchor="middle"
              className="fill-ink-dim text-[11px] tabular-nums">
              {r.label}
            </text>
          ));
        })()}

        {/* Hover crosshair — snap 到最近 candle 的 close */}
        {hover && visibleCandles[hover.idx] && (() => {
          const c = visibleCandles[hover.idx];
          const lineX = sx(c.date);
          const lineY = sy(c.close);
          if (Number.isNaN(lineX)) return null;
          const verticalEndY = showVolume
            ? PAD_T + innerH + 8 + VOL_H
            : PAD_T + innerH;
          const d = new Date(c.date);
          const hh = String(d.getHours()).padStart(2, "0");
          const mm = String(d.getMinutes()).padStart(2, "0");
          const volBarY = PAD_T + innerH + 8 + VOL_H - (c.volume / maxVolumeInView) * VOL_H;
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
      </>
      )}
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
