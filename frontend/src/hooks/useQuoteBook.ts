import { useEffect, useRef, useState } from "react";
import { api, type QuoteResponse } from "../lib/api";

const POLL_MS = 1000;

export interface QuoteBookData {
  bids: Array<{ price: number; size: number }>;
  asks: Array<{ price: number; size: number }>;
  innerVolume: number;   // 內盤累積成交量(賣方主動)
  outerVolume: number;   // 外盤累積成交量(買方主動)
  lastSuccessAt: Date | null;
  error: string | null;
}

/**
 * Poll /api/quote/{symbol} 每 1 秒，背景 tab 時暫停。
 *
 * Symbol 切換時：
 *  - 立即 fetch
 *  - 取消前一個未完成的 request（AbortController）
 *  - 清空舊資料 + 清 error
 *
 * 失敗時：保留前一次成功的 bids/asks（不閃白），只更新 error。
 */
export function useQuoteBook(symbol: string | null): QuoteBookData {
  const [bids, setBids] = useState<QuoteBookData["bids"]>([]);
  const [asks, setAsks] = useState<QuoteBookData["asks"]>([]);
  const [innerVolume, setInnerVolume] = useState(0);
  const [outerVolume, setOuterVolume] = useState(0);
  const [lastSuccessAt, setLastSuccessAt] = useState<Date | null>(null);
  const [error, setError] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (!symbol) {
      setBids([]); setAsks([]);
      setInnerVolume(0); setOuterVolume(0);
      setLastSuccessAt(null); setError(null);
      return;
    }

    // 切 symbol：清舊資料 + cancel pending
    abortRef.current?.abort();
    setBids([]); setAsks([]);
    setInnerVolume(0); setOuterVolume(0);
    setError(null);

    async function fetchOnce() {
      if (document.hidden) return;  // tab 背景時跳過
      abortRef.current?.abort();
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      try {
        const r: QuoteResponse = await api.quote(symbol!);
        if (ctrl.signal.aborted) return;
        setBids(r.bids ?? []);
        setAsks(r.asks ?? []);
        setInnerVolume(r.total?.tradeVolumeAtBid ?? 0);
        setOuterVolume(r.total?.tradeVolumeAtAsk ?? 0);
        setLastSuccessAt(new Date());
        setError(null);
      } catch (e) {
        if (ctrl.signal.aborted) return;
        setError(e instanceof Error ? e.message : String(e));
      }
    }

    fetchOnce();
    timerRef.current = setInterval(fetchOnce, POLL_MS);
    return () => {
      abortRef.current?.abort();
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [symbol]);

  return { bids, asks, innerVolume, outerVolume, lastSuccessAt, error };
}
