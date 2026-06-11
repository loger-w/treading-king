import { useCallback, useEffect, useRef, useState } from "react";
import { api, type IntradayCandle } from "../lib/api";
import { resolveCandleUpdate } from "../lib/intraday-candle-update";

const REFRESH_MS = 30_000;

export function useIntradayCandles(symbol: string | null) {
  const [candles, setCandles] = useState<IntradayCandle[]>([]);
  const [prevClose, setPrevClose] = useState<number | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // 追蹤當前選中的 symbol,讓飛行中的舊請求回來時辨識出自己已過期。
  const symbolRef = useRef(symbol);
  symbolRef.current = symbol;

  const fetchOnce = useCallback(async (s: string) => {
    try {
      const r = await api.candlesIntraday(s);
      const next = resolveCandleUpdate(s, symbolRef.current, r.data);
      if (next) setCandles(next);
      // prevClose 同樣只在非過期回應時更新(過期回應整筆丟棄)。
      // null 也擋:富邦降級回應會帶 prev_close=null(useSnapshotCache 有同款防護),
      // 蓋掉已知昨收會讓基準線/漲跌%/紅綠填色整個換成「今開」口徑 30 秒以上
      if (s === symbolRef.current && r.prev_close !== null) setPrevClose(r.prev_close);
    } catch (e) {
      // 失敗不清掉已顯示的圖(免得變回載入中);下一輪輪詢會再試
      console.warn("useIntradayCandles fetch failed:", e);
    }
  }, []);

  useEffect(() => {
    if (!symbol) { setCandles([]); setPrevClose(null); return; }
    setCandles([]);  // 先清空 — 避免新 symbol 載入過程中舊資料殘留視覺奇怪
    setPrevClose(null);
    fetchOnce(symbol);
    timerRef.current = setInterval(() => fetchOnce(symbol), REFRESH_MS);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [symbol, fetchOnce]);

  // WS tick 更新最後一根 candle close（不重算 average）
  const onTick = useCallback((tickSymbol: string, price: number) => {
    if (tickSymbol !== symbol) return;
    setCandles((prev) => {
      if (prev.length === 0) return prev;
      const last = prev[prev.length - 1];
      const updated = { ...last, close: price };
      if (price > last.high) updated.high = price;
      if (price < last.low) updated.low = price;
      return [...prev.slice(0, -1), updated];
    });
  }, [symbol]);

  return { candles, prevClose, onTick };
}
