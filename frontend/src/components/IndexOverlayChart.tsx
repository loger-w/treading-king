import { useMemo, useState } from "react";
import { useIntradayCandles } from "../hooks/useIntradayCandles";
import { CHART_W, CHART_H } from "../lib/intraday-chart-svg";
import { computeOverlayGeometry, IndexOverlayStatic, type OverlaySeries } from "../lib/index-overlay-svg";
import { INDEX_SYMBOLS } from "../lib/index-symbols";
import { MARKET_OPEN_MIN, TRADING_MINUTES } from "../lib/intraday-time";

export function IndexOverlayChart({ active = true }: { active?: boolean }) {
  const a = INDEX_SYMBOLS[0];
  const b = INDEX_SYMBOLS[1];
  // 頁面隱藏時借 null 短路暫停輪詢
  const ca = useIntradayCandles(active ? a.code : null);
  const cb = useIntradayCandles(active ? b.code : null);
  const [hover, setHover] = useState<number | null>(null); // minute of day

  const seriesA: OverlaySeries = { code: a.code, short: a.short, color: a.color, candles: ca.candles, prevClose: ca.prevClose };
  const seriesB: OverlaySeries = { code: b.code, short: b.short, color: b.color, candles: cb.candles, prevClose: cb.prevClose };
  const geometry = useMemo(
    () => computeOverlayGeometry(seriesA, seriesB),
    [ca.candles, ca.prevClose, cb.candles, cb.prevClose], // eslint-disable-line react-hooks/exhaustive-deps
  );

  const hasData = geometry.lines.some((l) => l.poly !== "");

  function handleMove(e: React.MouseEvent<SVGSVGElement>) {
    const rect = e.currentTarget.getBoundingClientRect();
    const svgX = ((e.clientX - rect.left) / rect.width) * CHART_W;
    const ratio = (svgX - geometry.padL) / (CHART_W - geometry.padL - geometry.padR);
    if (ratio < 0 || ratio > 1) { setHover(null); return; }
    setHover(MARKET_OPEN_MIN + ratio * TRADING_MINUTES);
  }

  return (
    <div>
      <div className="flex items-center gap-5 mb-3 text-sm">
        {INDEX_SYMBOLS.map((s) => {
          const line = geometry.lines.find((l) => l.code === s.code);
          const pct = hover != null ? geometry.pctByCodeAtMinute(s.code, hover) : line?.lastPct ?? null;
          return (
            <span key={s.code} style={{ color: s.color }} className="tabular-nums">
              ● {s.short} {pct == null ? "—" : `${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%`}
            </span>
          );
        })}
      </div>
      {!hasData ? (
        <div className="h-[300px] flex items-center justify-center text-ink-dim font-serif italic">無資料</div>
      ) : (
        <svg
          viewBox={`0 0 ${CHART_W} ${CHART_H}`}
          className="w-full h-auto cursor-crosshair"
          onMouseMove={handleMove}
          onMouseLeave={() => setHover(null)}
        >
          <IndexOverlayStatic geometry={geometry} />
          {hover != null && (
            <line x1={geometry.scaleX(hover)} y1={geometry.padT} x2={geometry.scaleX(hover)} y2={CHART_H - geometry.padB}
              stroke="#8a8273" strokeWidth="0.5" strokeDasharray="3 3" opacity="0.7" pointerEvents="none" />
          )}
        </svg>
      )}
    </div>
  );
}
