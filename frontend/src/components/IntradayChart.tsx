import { useEffect, useMemo, useState } from "react";
import { api, type CamarillaLevels, type CdpLevels, type MaLevels } from "../lib/api";
import { useIntradayCandles } from "../hooks/useIntradayCandles";
import { subscribeTicks } from "../hooks/useSignalsStream";
import { useLocalToggle } from "../hooks/useLocalToggle";
import { formatTickPrice } from "../lib/tick";
import { MARKET_OPEN_MIN, TRADING_MINUTES } from "../lib/intraday-time";
import {
  CHART_W, CHART_H, PAD_L, PAD_R, PAD_T, PAD_B, TOTAL_H,
  computeIntradayGeometry, IntradayChartStatic, formatVolume,
} from "../lib/intraday-chart-svg";

interface Props {
  symbol: string;
  name: string | null;
  inAnyBookmark: boolean;
  onOpenBookmarkDialog: () => void;
  inMonitor: boolean;
  onAddToMonitor: () => void;
  onRemoveFromMonitor: () => void;
}


export function IntradayChart({ symbol, name, inAnyBookmark, onOpenBookmarkDialog, inMonitor, onAddToMonitor, onRemoveFromMonitor }: Props) {
  // candles state 養在本元件而非頁根:選中股是 tick 最密的一檔,
  // state 在 Monitor 的話每筆成交都讓整頁四欄(含下單面板)重繪
  const { candles, prevClose, onTick } = useIntradayCandles(symbol);
  useEffect(() => subscribeTicks((t) => onTick(t.symbol, t.price)), [onTick]);

  const [showVwap, setShowVwap] = useState(true);
  const [showCdp, setShowCdp] = useLocalToggle("tk:chart:cdp", false);
  const [showCamarilla, setShowCamarilla] = useLocalToggle("tk:chart:camarilla", false);
  const [showVolume, setShowVolume] = useLocalToggle("tk:chart:volume", true);
  const [showMa, setShowMa] = useLocalToggle("tk:chart:ma", false);
  const [cdp, setCdp] = useState<CdpLevels | null>(null);
  const [cdpError, setCdpError] = useState<string | null>(null);
  const [camarilla, setCamarilla] = useState<CamarillaLevels | null>(null);
  const [camarillaError, setCamarillaError] = useState<string | null>(null);
  const [ma, setMa] = useState<MaLevels | null>(null);

  useEffect(() => {
    // 切 symbol 時先清舊 CDP — 避免新圖上殘留舊 CDP 線。
    // A→B 快速切換時 A 的回應可能比 B 慢到達,stale 旗標擋下飛行中的舊回應,
    // 否則別檔的價位線會畫在當前圖上(同 candles 路徑的 resolveCandleUpdate)
    setCdp(null);
    setCdpError(null);
    if (!showCdp) return;
    let stale = false;
    api.cdp(symbol).then((r) => { if (!stale) setCdp(r); }).catch((e) => {
      if (!stale) setCdpError(e instanceof Error ? e.message : String(e));
    });
    return () => { stale = true; };
  }, [symbol, showCdp]);

  useEffect(() => {
    // 切 symbol 時先清舊 Camarilla — 避免新圖上殘留舊線(stale 旗標同 CDP)
    setCamarilla(null);
    setCamarillaError(null);
    if (!showCamarilla) return;
    let stale = false;
    api.camarilla(symbol).then((r) => { if (!stale) setCamarilla(r); }).catch((e) => {
      if (!stale) setCamarillaError(e instanceof Error ? e.message : String(e));
    });
    return () => { stale = true; };
  }, [symbol, showCamarilla]);

  useEffect(() => {
    // 切 symbol 時先清舊 MA — 避免新圖上殘留舊值(stale 旗標同 CDP)
    setMa(null);
    if (!showMa) return;
    let stale = false;
    api.ma(symbol).then((r) => { if (!stale) setMa(r); }).catch((e) => {
      console.warn("MA fetch failed:", e);
    });
    return () => { stale = true; };
  }, [symbol, showMa]);

  // flags 物件穩定化:inline literal 會打穿 IntradayChartStatic 的 memo
  const flags = useMemo(
    () => ({ vwap: showVwap, cdp: showCdp, camarilla: showCamarilla, volume: showVolume, ma: showMa }),
    [showVwap, showCdp, showCamarilla, showVolume, showMa],
  );
  const geometry = useMemo(
    () => computeIntradayGeometry({ candles, prevClose, cdp, camarilla, ma, flags }),
    [candles, cdp, camarilla, ma, prevClose, flags],
  );
  const { scaleX, scaleY, scaleVolY, minutesByIdx, filteredCandles } = geometry;

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
    // 同一根 candle 回傳 prev 讓 React bail out — mousemove 每動一下都進來
    setHover((prev) => (prev?.idx === bestIdx ? prev : { idx: bestIdx }));
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
        <div className="shrink-0 flex items-center gap-2">
          <button
            type="button"
            onClick={onOpenBookmarkDialog}
            className={[
              "inline-flex items-center gap-1.5 px-3.5 py-1.5 text-2xs uppercase tracking-[1.5px] transition-all duration-150 cursor-pointer",
              inAnyBookmark
                ? "border border-bear/40 text-bear hover:border-bear"
                : "border border-ink-dim text-ink-muted hover:border-accent hover:text-accent",
            ].join(" ")}
            aria-label="加入或編輯書籤"
          >
            {inAnyBookmark ? "✎ 編輯書籤" : "+ 加入書籤"}
          </button>
          {inMonitor ? (
            <button
              type="button"
              onClick={onRemoveFromMonitor}
              className="text-xs text-ink-dim hover:text-bear px-2 py-1 border border-line"
              title="從監聽清單移除"
            >
              已在監聽 ✓
            </button>
          ) : (
            <button
              type="button"
              onClick={onAddToMonitor}
              className="text-xs text-ink-dim hover:text-accent px-2 py-1 border border-dashed border-line"
            >
              + 加入監聽
            </button>
          )}
        </div>
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
          {/* 所有靜態圖層改由共用模組 IntradayChartStatic 渲染 —
              hover crosshair(下方)留在本元件以保持互動狀態 */}
          <IntradayChartStatic
            candles={candles} prevClose={prevClose} cdp={cdp} camarilla={camarilla} ma={ma}
            flags={flags}
            geometry={geometry}
          />

          {/* Hover crosshair — 線跟價位 snap 到走勢線本身（不是 cursor 自由位置）。
              切股後 candles 變短時殘留的 hover.idx 會越界(svg 卸載不觸發 mouseleave),
              必須守門否則 render throw 整頁白屏 — 同 MXFIntradayChart 的防護 */}
          {hover && filteredCandles[hover.idx] && (() => {
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
          onClick={() => setShowCamarilla((v) => !v)}
          className={`px-2 py-1 border ${showCamarilla ? "border-accent text-accent" : "border-line text-ink-dim"}`}
        >{showCamarilla ? "✓" : ""} CAM</button>
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
      {showCamarilla && camarillaError && (
        <div className="mt-1 text-xs text-bear">Camarilla 無資料:{camarillaError}</div>
      )}
    </div>
  );
}
