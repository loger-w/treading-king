import { useMemo, useState } from "react";
import { useIntradayCandles } from "../hooks/useIntradayCandles";
import { CHART_W, CHART_H, TOTAL_H } from "../lib/intraday-chart-svg";
import { IndexIntradayStatic, computeIndexGeometry, fmtIndex, fmtIndexVol, indexAmplitude } from "../lib/index-intraday-svg";
import { MARKET_OPEN_MIN, TRADING_MINUTES } from "../lib/intraday-time";

export function IndexIntradayChart({ code, name, active = true }: { code: string; name: string; active?: boolean }) {
  // 頁面隱藏時借 null 短路暫停輪詢(回到頁面再重抓,30s 一輪不值得在背景跑)
  const { candles, prevClose } = useIntradayCandles(active ? code : null);
  const geometry = useMemo(() => computeIndexGeometry({ candles, prevClose }), [candles, prevClose]);
  const { scaleX, scaleY, minutesByIdx, filteredCandles } = geometry;
  const [hover, setHover] = useState<{ idx: number } | null>(null);

  const latest = filteredCandles[filteredCandles.length - 1];
  const baseline = prevClose ?? (filteredCandles[0]?.open ?? 0);
  const change = latest && baseline ? latest.close - baseline : 0;
  const changePct = latest && baseline ? (change / baseline) * 100 : 0;
  const isUp = change > 0;
  const dirCls = isUp ? "text-bull" : change < 0 ? "text-bear" : "text-ink-muted";
  const amp = latest ? indexAmplitude(geometry.todayHigh, geometry.todayLow, prevClose) : null;
  const totalVal = filteredCandles.reduce((n, cd) => n + cd.volume, 0);

  function handleMove(e: React.MouseEvent<SVGSVGElement>) {
    if (minutesByIdx.length === 0) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const svgX = ((e.clientX - rect.left) / rect.width) * CHART_W;
    const ratio = (svgX - geometry.padL) / (CHART_W - geometry.padL - geometry.padR);
    const mAtCursor = MARKET_OPEN_MIN + ratio * TRADING_MINUTES;
    const latestM = minutesByIdx[minutesByIdx.length - 1];
    if (ratio < 0 || mAtCursor > latestM) { setHover(null); return; }
    let best = 0, bestDist = Math.abs(minutesByIdx[0] - mAtCursor);
    for (let i = 1; i < minutesByIdx.length; i++) {
      const d = Math.abs(minutesByIdx[i] - mAtCursor);
      if (d < bestDist) { bestDist = d; best = i; }
    }
    setHover({ idx: best });
  }

  return (
    <div>
      <div className="mb-3">
        <div className="text-sm text-ink-muted">
          {code} <span className="ml-1">{name}</span>
        </div>
        <div className={`font-semibold text-[34px] tabular-nums leading-none mt-1 ${dirCls}`}>
          {latest ? fmtIndex(latest.close) : "—"}
        </div>
        {latest && (
          <div className={`text-[17px] font-medium tabular-nums mt-1.5 ${dirCls}`}>
            {isUp ? "▲" : change < 0 ? "▾" : "—"} {Math.abs(change).toFixed(2)}　{changePct >= 0 ? "+" : ""}{changePct.toFixed(2)}%
          </div>
        )}
        {latest && (
          <div className="text-[13px] text-ink-muted tabular-nums mt-1">
            振幅 {amp != null ? `${amp.toFixed(2)}%` : "—"}　成交值 {fmtIndexVol(totalVal)}
          </div>
        )}
      </div>

      {filteredCandles.length === 0 ? (
        <div className="h-[300px] flex items-center justify-center text-ink-dim font-serif italic">無資料</div>
      ) : (
        <svg
          viewBox={`0 0 ${CHART_W} ${TOTAL_H}`}
          className="w-full h-auto cursor-crosshair"
          onMouseMove={handleMove}
          onMouseLeave={() => setHover(null)}
        >
          <IndexIntradayStatic prevClose={prevClose} geometry={geometry} idPrefix={code} />
          {hover && filteredCandles[hover.idx] && (() => {
            const cd = filteredCandles[hover.idx];
            const x = scaleX(minutesByIdx[hover.idx]);
            const y = scaleY(cd.close);
            const m = minutesByIdx[hover.idx];
            const hh = String(Math.floor(m / 60)).padStart(2, "0");
            const mm = String(m % 60).padStart(2, "0");
            return (
              <g pointerEvents="none">
                <line x1={x} y1={geometry.padT} x2={x} y2={CHART_H - geometry.padB} stroke="#8a8273" strokeWidth="0.5" strokeDasharray="3 3" opacity="0.7" />
                <line x1={geometry.padL} y1={y} x2={CHART_W - geometry.padR} y2={y} stroke="#8a8273" strokeWidth="0.5" strokeDasharray="3 3" opacity="0.7" />
                <circle cx={x} cy={y} r="2.5" className="fill-ink" />
                <text x={geometry.padL + 2} y={y - 4} className="fill-ink text-[12px] tabular-nums">{fmtIndex(cd.close)}　{hh}:{mm}</text>
              </g>
            );
          })()}
        </svg>
      )}
    </div>
  );
}
