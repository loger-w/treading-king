import { useEffect, useRef, useState } from "react";
import { api, type QuoteResponse } from "../lib/api";

const POLL_MS = 1000;

export interface QuoteBookData {
  bids: Array<{ price: number; size: number }>;
  asks: Array<{ price: number; size: number }>;
  isLimitUp: boolean;
  isLimitDown: boolean;
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
  const [isLimitUp, setIsLimitUp] = useState(false);
  const [isLimitDown, setIsLimitDown] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (!symbol) {
      setBids([]); setAsks([]);
      setIsLimitUp(false); setIsLimitDown(false);
      setError(null);
      return;
    }

    // 切 symbol：清舊資料 + cancel pending
    abortRef.current?.abort();
    setBids([]); setAsks([]);
    setIsLimitUp(false); setIsLimitDown(false);
    setError(null);

    // 上一請求還在飛就跳過本 tick:改成「每 tick 先 abort」的話,
    // 延遲一旦超過 poll 間隔,每個回應都會在抵達前被下一個 tick 取消,
    // 五檔永遠停在舊資料且 error 維持 null
    let inFlight = false;
    async function fetchOnce() {
      if (document.hidden || inFlight) return;
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      inFlight = true;
      try {
        const r: QuoteResponse = await api.quote(symbol!, ctrl.signal);
        if (ctrl.signal.aborted) return;
        setBids(r.bids ?? []);
        setAsks(r.asks ?? []);
        setIsLimitUp(Boolean(r.is_limit_up_bid || r.is_limit_up_ask));
        setIsLimitDown(Boolean(r.is_limit_down_bid || r.is_limit_down_ask));
        setError(null);
      } catch (e) {
        if (ctrl.signal.aborted) return;
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        inFlight = false;
      }
    }

    fetchOnce();
    timerRef.current = setInterval(fetchOnce, POLL_MS);
    return () => {
      abortRef.current?.abort();
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [symbol]);

  return { bids, asks, isLimitUp, isLimitDown, error };
}
