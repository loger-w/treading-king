import { useCallback, useEffect, useRef, useState } from "react";
import { api, type IntradayCandle } from "../lib/api";

const REFRESH_MS = 30_000;

export function useIntradayCandles(symbol: string | null) {
  const [candles, setCandles] = useState<IntradayCandle[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchOnce = useCallback(async (s: string) => {
    setLoading(true);
    setError(null);
    try {
      const r = await api.candlesIntraday(s);
      setCandles(r.data ?? []);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!symbol) { setCandles([]); return; }
    setCandles([]);  // 先清空 — 避免新 symbol 載入過程中舊資料殘留視覺奇怪
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

  return { candles, loading, error, onTick, refetch: () => symbol && fetchOnce(symbol) };
}
