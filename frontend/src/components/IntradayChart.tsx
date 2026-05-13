import { useEffect, useMemo, useState } from "react";
import { api, type CdpLevels, type IntradayCandle } from "../lib/api";
import { useLocalToggle } from "../hooks/useLocalToggle";
import { formatTickPrice, roundToNearestTick } from "../lib/tick";

interface Props {
  symbol: string;
  name: string | null;
  candles: IntradayCandle[];
  prevClose: number | null;  // 昨日收盤，給漲跌% / Y 軸 ±10% 用
}

const CHART_W = 720;
const CHART_H = 360;
const PAD_L = 56;
const PAD_R = 12;
const PAD_T = 12;
const PAD_B = 28;

export function IntradayChart({ symbol, name, candles, prevClose }: Props) {
  const [showVwap, setShowVwap] = useState(true);
  const [showCdp, setShowCdp] = useLocalToggle("tk:chart:cdp", false);
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

    // 基準價 = 昨日收盤（台股漲跌停以昨收為基準）；prev_close 沒有時 fallback 今日開盤
    const refPrice = prevClose ?? candles[0].open;
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
  }, [candles, cdp, showCdp, prevClose]);

  const latest = candles[candles.length - 1];
  const first = candles[0];
  // 漲跌基準：昨日收盤；prev_close 沒拿到時 fallback 今日開盤
  const baseline = prevClose ?? (first ? first.open : 0);
  const change = latest && baseline ? latest.close - baseline : 0;
  const changePct = latest && baseline ? (change / baseline) * 100 : 0;
  const isUp = change > 0;
  const dirCls = isUp ? "text-bull" : change < 0 ? "text-bear" : "text-ink-muted";

  return (
    <div>
      {/* 股票資訊 header — 名稱 · 代號 + 大字股價 + 漲跌百分比 */}
      <div className="mb-4">
        <div className="font-serif text-[22px] tracking-tight text-ink leading-tight">
          {name ?? "—"} · {symbol}
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

      {candles.length === 0 ? (
        <div className="h-[360px] flex items-center justify-center text-ink-dim font-serif italic">
          載入中…
        </div>
      ) : (
        <svg viewBox={`0 0 ${CHART_W} ${CHART_H}`} className="w-full h-auto">
          {/* Y 軸格線 + label — 5 條等距取樣後 snap 到最近台股 tick，重複值 dedupe */}
          {Array.from(new Set(
            [0, 0.25, 0.5, 0.75, 1].map((p) =>
              roundToNearestTick(yMin + (yMax - yMin) * (1 - p))
            )
          )).map((vTick) => {
            const y = scaleY(vTick);
            return (
              <g key={vTick}>
                <line x1={PAD_L} y1={y} x2={CHART_W - PAD_R} y2={y}
                  stroke="var(--color-line, #2e2a22)" strokeWidth="0.5" />
                <text x={PAD_L - 4} y={y + 3} textAnchor="end"
                  className="fill-ink-dim text-[10px] tabular-nums">{formatTickPrice(vTick)}</text>
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
                    {k.toUpperCase()} {formatTickPrice(cdp[k])}
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

      {/* Toggle 按鈕（VWAP / CDP） */}
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
      </div>
      {showCdp && cdpError && (
        <div className="mt-1 text-xs text-bear">CDP 無資料：{cdpError}</div>
      )}
    </div>
  );
}
