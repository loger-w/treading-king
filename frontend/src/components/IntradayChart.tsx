import { useEffect, useMemo, useState } from "react";
import { api, type CdpLevels, type IntradayCandle } from "../lib/api";
import { useLocalToggle } from "../hooks/useLocalToggle";
import { formatTickPrice, roundToNearestTick } from "../lib/tick";

interface Props {
  symbol: string;
  name: string | null;
  candles: IntradayCandle[];
  prevClose: number | null;  // 昨日收盤，給漲跌% / Y 軸 ±10% 用
  inWatchlist: boolean;
  onAddToWatchlist: () => void;
}

const CHART_W = 820;
const CHART_H = 460;
const PAD_L = 56;
const PAD_R = 56;  // 寬一點，把 CDP label 擠到外面去（不被即時走勢價線蓋）
const PAD_T = 12;
const PAD_B = 28;

export function IntradayChart({ symbol, name, candles, prevClose, inWatchlist, onAddToWatchlist }: Props) {
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

  const {
    yMin, yMax, scaleX, scaleY,
    polyClose, polyVwap, visibleCdpKeys,
    todayHigh, todayHighIdx, todayLow, todayLowIdx,
  } = useMemo(() => {
    if (candles.length === 0) {
      return {
        yMin: 0, yMax: 0,
        scaleX: () => 0, scaleY: () => 0,
        polyClose: "", polyVwap: "",
        visibleCdpKeys: [] as Array<"ah" | "nh" | "cdp" | "nl" | "al">,
        todayHigh: 0, todayHighIdx: -1, todayLow: 0, todayLowIdx: -1,
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

    // 今日最高/最低 — 用 candle.high/low（聚合，含同分鐘 tick 波動），不是 close
    let todayHigh = candles[0].high;
    let todayHighIdx = 0;
    let todayLow = candles[0].low;
    let todayLowIdx = 0;
    for (let i = 1; i < candles.length; i++) {
      if (candles[i].high > todayHigh) { todayHigh = candles[i].high; todayHighIdx = i; }
      if (candles[i].low < todayLow) { todayLow = candles[i].low; todayLowIdx = i; }
    }

    const xRange = CHART_W - PAD_L - PAD_R;
    const yRange = CHART_H - PAD_T - PAD_B;
    const scaleX = (i: number) => PAD_L + (i / Math.max(candles.length - 1, 1)) * xRange;
    const scaleY = (v: number) => PAD_T + (1 - (v - yMin) / (yMax - yMin || 1)) * yRange;
    const polyClose = candles.map((c, i) => `${scaleX(i)},${scaleY(c.close)}`).join(" ");
    const polyVwap = candles.map((c, i) => `${scaleX(i)},${scaleY(c.average)}`).join(" ");
    return {
      yMin, yMax, scaleX, scaleY,
      polyClose, polyVwap, visibleCdpKeys,
      todayHigh, todayHighIdx, todayLow, todayLowIdx,
    };
  }, [candles, cdp, showCdp, prevClose]);

  // hover crosshair state — 只跟 candle idx 走（價位由 candle.close 決定）
  const [hover, setHover] = useState<{ idx: number } | null>(null);

  function handleMouseMove(e: React.MouseEvent<SVGSVGElement>) {
    if (candles.length === 0) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const svgX = ((e.clientX - rect.left) / rect.width) * CHART_W;
    const svgY = ((e.clientY - rect.top) / rect.height) * CHART_H;
    // 只在 chart area 內才有 crosshair
    if (svgX < PAD_L || svgX > CHART_W - PAD_R || svgY < PAD_T || svgY > CHART_H - PAD_B) {
      setHover(null);
      return;
    }
    const ratio = (svgX - PAD_L) / (CHART_W - PAD_L - PAD_R);
    const idx = Math.max(0, Math.min(candles.length - 1, Math.round(ratio * (candles.length - 1))));
    setHover({ idx });
  }

  function handleMouseLeave() {
    setHover(null);
  }

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
      {/* 股票資訊 header — 名稱 · 代號 + 大字股價 + 漲跌百分比 + 加入自選按鈕 */}
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
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
        <button
          type="button"
          onClick={onAddToWatchlist}
          disabled={inWatchlist}
          className={[
            "shrink-0 inline-flex items-center gap-1.5 px-3.5 py-1.5 text-2xs uppercase tracking-[1.5px] transition-all duration-150",
            inWatchlist
              ? "border border-line text-ink-dim cursor-default"
              : "border border-ink-dim text-ink-muted hover:border-accent hover:text-accent cursor-pointer",
          ].join(" ")}
          aria-label={inWatchlist ? "已在自選清單" : "加入自選清單"}
        >
          {inWatchlist ? "已在自選 ✓" : "+ 加入自選"}
        </button>
      </div>

      {candles.length === 0 ? (
        <div className="h-[360px] flex items-center justify-center text-ink-dim font-serif italic">
          載入中…
        </div>
      ) : (
        <svg
          viewBox={`0 0 ${CHART_W} ${CHART_H}`}
          className="w-full h-auto cursor-crosshair"
          onMouseMove={handleMouseMove}
          onMouseLeave={handleMouseLeave}
        >
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
                    className={`text-[10px] tabular-nums ${isBaseline ? "fill-ink" : "fill-ink-dim"}`}>
                    {formatTickPrice(vTick)}
                  </text>
                </g>
              );
            });
          })()}

          {/* CDP 5 線（超出 ±10% 範圍的隱藏） */}
          {showCdp && cdp && visibleCdpKeys.length > 0 && (
            <>
              {visibleCdpKeys.map((k) => (
                <g key={k}>
                  <line x1={PAD_L} y1={scaleY(cdp[k])} x2={CHART_W - PAD_R} y2={scaleY(cdp[k])}
                    stroke="var(--color-accent, #e85a4f)" strokeWidth="0.6"
                    strokeDasharray="4 3" opacity="0.6" />
                  {/* label 放在 chart 右側 margin 外，避免被即時價線遮住 */}
                  <text x={CHART_W - PAD_R + 4} y={scaleY(cdp[k]) + 3} textAnchor="start"
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

          {/* 今日最高 / 最低 標記 + 數字 */}
          {todayHighIdx >= 0 && (
            <g>
              <circle cx={scaleX(todayHighIdx)} cy={scaleY(todayHigh)} r="2.5"
                className="fill-bull" />
              <text
                x={scaleX(todayHighIdx)}
                y={scaleY(todayHigh) - 6}
                textAnchor="middle"
                className="fill-bull text-[10px] tabular-nums font-medium"
              >
                H {formatTickPrice(todayHigh)}
              </text>
            </g>
          )}
          {todayLowIdx >= 0 && (
            <g>
              <circle cx={scaleX(todayLowIdx)} cy={scaleY(todayLow)} r="2.5"
                className="fill-bear" />
              <text
                x={scaleX(todayLowIdx)}
                y={scaleY(todayLow) + 13}
                textAnchor="middle"
                className="fill-bear text-[10px] tabular-nums font-medium"
              >
                L {formatTickPrice(todayLow)}
              </text>
            </g>
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

          {/* Hover crosshair — 線跟價位 snap 到走勢線本身（不是 cursor 自由位置）*/}
          {hover && (() => {
            const candle = candles[hover.idx];
            const lineX = scaleX(hover.idx);
            const lineY = scaleY(candle.close);
            const t = new Date(candle.date);
            const hh = String(t.getHours()).padStart(2, "0");
            const mm = String(t.getMinutes()).padStart(2, "0");
            return (
              <g>
                {/* 垂直虛線（snap 到最近 candle） */}
                <line x1={lineX} y1={PAD_T} x2={lineX} y2={CHART_H - PAD_B}
                  stroke="var(--color-ink-dim, #8a8273)" strokeWidth="0.5"
                  strokeDasharray="3 3" opacity="0.7" />
                {/* 水平虛線（snap 到 candle close 的 y） */}
                <line x1={PAD_L} y1={lineY} x2={CHART_W - PAD_R} y2={lineY}
                  stroke="var(--color-ink-dim, #8a8273)" strokeWidth="0.5"
                  strokeDasharray="3 3" opacity="0.7" />
                {/* candle close 上的 marker */}
                <circle cx={lineX} cy={lineY} r="2.5"
                  className="fill-ink" />
                {/* 左側 Y 軸：該 candle 的 close 價位（小色塊高亮） */}
                <rect x={2} y={lineY - 7} width={PAD_L - 6} height="14" rx="1.5"
                  fill="var(--color-line, #2e2a22)"
                  stroke="var(--color-ink-dim, #8a8273)" strokeWidth="0.5" />
                <text x={PAD_L - 4} y={lineY + 3} textAnchor="end"
                  className="fill-ink text-[10px] tabular-nums font-medium">
                  {formatTickPrice(candle.close)}
                </text>
                {/* 底部 X 軸：該 candle 的時間（小色塊高亮） */}
                <rect x={lineX - 18} y={CHART_H - PAD_B + 2} width="36" height="14" rx="1.5"
                  fill="var(--color-line, #2e2a22)"
                  stroke="var(--color-ink-dim, #8a8273)" strokeWidth="0.5" />
                <text x={lineX} y={CHART_H - PAD_B + 12} textAnchor="middle"
                  className="fill-ink text-[10px] tabular-nums font-medium">
                  {hh}:{mm}
                </text>
              </g>
            );
          })()}
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
