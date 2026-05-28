import { useCallback, useEffect, useRef, useState } from "react";
import { api, type CurrentSession, type MXFCandle } from "../lib/api";
import { subscribeMxfCandles } from "./useSignalsStream";

const REFRESH_MS = 30_000;

export interface UseMXFCandlesState {
  symbol: string | null;
  candles: MXFCandle[];
  currentSession: CurrentSession | null;
  loading: boolean;
  error: string | null;
}

export function useMXFCandles(timeframe: number) {
  const [state, setState] = useState<UseMXFCandlesState>({
    symbol: null,
    candles: [],
    currentSession: null,
    loading: true,
    error: null,
  });
  const symbolRef = useRef<string | null>(null);
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchCandles = useCallback(async (sym: string | null) => {
    try {
      const r = await api.mxfCandles(timeframe, sym ?? undefined);
      symbolRef.current = r.symbol;
      setState((prev) => ({
        ...prev,
        symbol: r.symbol,
        candles: r.candles,
        currentSession: r.current_session,
        loading: false,
        error: null,
      }));
    } catch (e) {
      setState((prev) => ({
        ...prev,
        loading: false,
        error: e instanceof Error ? e.message : String(e),
      }));
    }
  }, [timeframe]);

  // 初始化 + timeframe 變動 → 重新拉
  useEffect(() => {
    setState((prev) => ({ ...prev, loading: true, candles: [] }));
    fetchCandles(symbolRef.current);
    pollTimer.current = setInterval(() => fetchCandles(symbolRef.current), REFRESH_MS);
    return () => {
      if (pollTimer.current) clearInterval(pollTimer.current);
    };
  }, [fetchCandles]);

  // WS push → 合進 candles
  useEffect(() => {
    const unsub = subscribeMxfCandles(({ symbol, candle }) => {
      if (symbol !== symbolRef.current) return;
      setState((prev) => {
        const arr = prev.candles;
        if (arr.length === 0) return { ...prev, candles: [candle] };
        const last = arr[arr.length - 1];
        if (last.date === candle.date) {
          // 同一根 bar → 更新最後一筆
          return { ...prev, candles: [...arr.slice(0, -1), candle] };
        }
        if (candle.date > last.date) {
          // 新 bar → append
          return { ...prev, candles: [...arr, candle] };
        }
        // candle.date < last.date → 丟棄(舊資料)
        return prev;
      });
    });
    return unsub;
  }, []);

  return state;
}
