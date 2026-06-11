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

  // WS push → 合進 candles。後端 WS 固定推 1 分 K(富邦 candles channel 訂閱不帶 timeframe),
  // tf≠1 時 1 分 K 的 date 不等於聚合 bucket 的 date,直接 merge 會在右緣長出假 K 棒:
  // 只把推送投影到最後一根聚合 bar(close/高低),volume/average 等 30s 輪詢校正
  useEffect(() => {
    const unsub = subscribeMxfCandles(({ symbol, candle }) => {
      if (symbol !== symbolRef.current) return;
      setState((prev) => {
        const arr = prev.candles;
        if (timeframe !== 1) {
          if (arr.length === 0) return prev;   // 沒有聚合基底可投影,等輪詢
          const last = arr[arr.length - 1];
          if (candle.date < last.date) return prev;   // 舊資料
          const merged = {
            ...last,
            close: candle.close,
            high: Math.max(last.high, candle.high),
            low: Math.min(last.low, candle.low),
          };
          return { ...prev, candles: [...arr.slice(0, -1), merged] };
        }
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
  }, [timeframe]);

  return state;
}
