import { useEffect, useMemo, useState } from "react";
import { api, type CdpLevels, type IntradayCandle } from "../lib/api";

interface Props {
  symbol: string;
  candles: IntradayCandle[];
  loading: boolean;
}

const CHART_W = 720;
const CHART_H = 360;
const PAD_L = 56;
const PAD_R = 12;
const PAD_T = 12;
const PAD_B = 28;

export function IntradayChart({ symbol, candles }: Props) {
  const [showVwap, setShowVwap] = useState(true);
  const [showCdp, setShowCdp] = useState(false);
  const [cdp, setCdp] = useState<CdpLevels | null>(null);
  const [cdpError, setCdpError] = useState<string | null>(null);

  useEffect(() => {
    // 切 symbol 時先清舊 CDP — 避免新圖上殘留舊 CDP 線
    setCdp(null);
    setCdpError(null);
    if (!showCdp) return;
    api.cdp(symbol).then(setCdp).catch((e) =>
      setCdpError(e instanceof Error ? e.message : String(e))
    );
  }, [symbol, showCdp]);

  const { yMin, yMax, scaleX, scaleY, polyClose, polyVwap, visibleCdpKeys } = useMemo(() => {
    if (candles.length === 0) {
      return {
        yMin: 0, yMax: 0,
        scaleX: () => 0, scaleY: () => 0,
        polyClose: "", polyVwap: "",
        visibleCdpKeys: [] as Array<"ah" | "nh" | "cdp" | "nl" | "al">,
      };
    }
    const closes = candles.map((c) => c.close);
    const vwaps = candles.map((c) => c.average);

    // 基準價 = 今天開盤（≈ 昨日收盤，差距理論 ≤ 10%）
    const refPrice = candles[0].open;
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

    const xRange = CHART_W - PAD_L - PAD_R;
    const yRange = CHART_H - PAD_T - PAD_B;
    const scaleX = (i: number) => PAD_L + (i / Math.max(candles.length - 1, 1)) * xRange;
    const scaleY = (v: number) => PAD_T + (1 - (v - yMin) / (yMax - yMin || 1)) * yRange;
    const polyClose = candles.map((c, i) => `${scaleX(i)},${scaleY(c.close)}`).join(" ");
    const polyVwap = candles.map((c, i) => `${scaleX(i)},${scaleY(c.average)}`).join(" ");
    return { yMin, yMax, scaleX, scaleY, polyClose, polyVwap, visibleCdpKeys };
  }, [candles, cdp, showCdp]);

  const latest = candles[candles.length - 1];
  const first = candles[0];
  const change = latest && first ? latest.close - first.open : 0;
  const isUp = change > 0;
  const dirCls = isUp ? "text-bull" : change < 0 ? "text-bear" : "text-ink-muted";

  return (
    <div>
      {candles.length === 0 ? (
        <div className="h-[360px] flex items-center justify-center text-ink-dim font-serif italic">
          載入中…
        </div>
      ) : (
        <svg viewBox={`0 0 ${CHART_W} ${CHART_H}`} className="w-full h-auto">
          {/* Y 軸格線 + label (簡單 5 條) */}
          {[0, 0.25, 0.5, 0.75, 1].map((p) => {
            const v = yMin + (yMax - yMin) * (1 - p);
            const y = PAD_T + p * (CHART_H - PAD_T - PAD_B);
            return (
              <g key={p}>
                <line x1={PAD_L} y1={y} x2={CHART_W - PAD_R} y2={y}
                  stroke="var(--color-line, #2e2a22)" strokeWidth="0.5" />
                <text x={PAD_L - 4} y={y + 3} textAnchor="end"
                  className="fill-ink-dim text-[10px] tabular-nums">{v.toFixed(1)}</text>
              </g>
            );
          })}

          {/* CDP 5 線（超出 ±10% 範圍的隱藏） */}
          {showCdp && cdp && visibleCdpKeys.length > 0 && (
            <>
              {visibleCdpKeys.map((k) => (
                <g key={k}>
                  <line x1={PAD_L} y1={scaleY(cdp[k])} x2={CHART_W - PAD_R} y2={scaleY(cdp[k])}
                    stroke="var(--color-accent, #e85a4f)" strokeWidth="0.6"
                    strokeDasharray="4 3" opacity="0.6" />
                  <text x={CHART_W - PAD_R - 2} y={scaleY(cdp[k]) - 2} textAnchor="end"
                    className="fill-accent text-[10px] uppercase">
                    {k.toUpperCase()} {cdp[k].toFixed(1)}
                  </text>
                </g>
              ))}
            </>
          )}

          {/* VWAP */}
          {showVwap && polyVwap && (
            <polyline points={polyVwap} fill="none"
              stroke="var(--color-ink-dim, #8a8273)" strokeWidth="1" strokeDasharray="3 2" />
          )}

          {/* 主價線 */}
          {polyClose && (
            <polyline points={polyClose} fill="none"
              stroke="var(--color-ink, #ede4d3)" strokeWidth="1.5" />
          )}

          {/* X 軸時間 label */}
          {[0, 0.25, 0.5, 0.75, 1].map((p) => {
            if (candles.length === 0) return null;
            const idx = Math.floor((candles.length - 1) * p);
            const x = scaleX(idx);
            const t = new Date(candles[idx].date);
            const hh = String(t.getHours()).padStart(2, "0");
            const mm = String(t.getMinutes()).padStart(2, "0");
            return (
              <text key={p} x={x} y={CHART_H - 8} textAnchor="middle"
                className="fill-ink-dim text-[10px] tabular-nums">{hh}:{mm}</text>
            );
          })}
        </svg>
      )}

      {/* 報價 + toggle */}
      {latest && (
        <div className="mt-2 flex items-baseline justify-between border-t border-line pt-2">
          <div className="flex items-baseline gap-3">
            <span className={`font-serif italic text-xl ${dirCls} tabular-nums`}>
              {latest.close.toFixed(2)}
            </span>
            <span className={`text-sm ${dirCls} tabular-nums`}>
              {isUp ? "▲" : change < 0 ? "▾" : "—"} {Math.abs(change).toFixed(2)}
            </span>
          </div>
          <div className="flex gap-2 text-xs">
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
          </div>
        </div>
      )}
      {showCdp && cdpError && (
        <div className="mt-1 text-xs text-bear">CDP 無資料：{cdpError}</div>
      )}
    </div>
  );
}
