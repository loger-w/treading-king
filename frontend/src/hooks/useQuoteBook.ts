import { useEffect, useState } from "react";
import { api, type QuoteResponse } from "../lib/api";

const POLL_MS = 1000;

export interface QuoteBookData {
  bids: Array<{ price: number; size: number }>;
  asks: Array<{ price: number; size: number }>;
  isLimitUp: boolean;
  isLimitDown: boolean;
  referencePrice: number | null;   // 平盤參考價,1Hz 輪詢天然帶重試
  error: string | null;
}

const EMPTY: QuoteBookData = {
  bids: [], asks: [], isLimitUp: false, isLimitDown: false, referencePrice: null, error: null,
};

/**
 * Poll /api/quote/{symbol} 每 1 秒,背景 tab 時暫停。
 *
 * Module-level per-symbol 共用 poller:QuoteBook 與 FlashPanel 對同一 symbol
 * 各自 useQuoteBook 時共用同一條輪詢(後端 /api/quote 直打富邦、無 cache,
 * 雙 instance 各開一條會白耗全 app 共用的富邦 rate limit)。最後一個訂閱者
 * 退訂才停輪詢並取消在途請求。
 *
 * 內容沒變時保留舊 ref 且不通知:下游 useMemo(閃電梯)/effect(scrollIntoView)
 * 才不會每秒空轉。失敗時保留前一次成功的五檔(不閃白),只更新 error。
 */
interface Poller {
  data: QuoteBookData;
  listeners: Set<(d: QuoteBookData) => void>;
  timer: ReturnType<typeof setInterval>;
  ctrl: AbortController | null;
  inFlight: boolean;
}

const pollers = new Map<string, Poller>();

function rowsEqual(a: QuoteBookData["bids"], b: QuoteBookData["bids"]): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (a[i].price !== b[i].price || a[i].size !== b[i].size) return false;
  }
  return true;
}

async function pollOnce(symbol: string, p: Poller) {
  // 上一請求還在飛就跳過本 tick(延遲超過 poll 間隔時,改成「先 abort」
  // 會讓每個回應都在抵達前被取消,五檔永遠停在舊資料)
  if (document.hidden || p.inFlight) return;
  const ctrl = new AbortController();
  p.ctrl = ctrl;
  p.inFlight = true;
  try {
    const r: QuoteResponse = await api.quote(symbol, ctrl.signal);
    if (ctrl.signal.aborted) return;
    const next: QuoteBookData = {
      bids: r.bids ?? [],
      asks: r.asks ?? [],
      isLimitUp: Boolean(r.is_limit_up_bid || r.is_limit_up_ask),
      isLimitDown: Boolean(r.is_limit_down_bid || r.is_limit_down_ask),
      referencePrice: r.reference_price ?? null,
      error: null,
    };
    const prev = p.data;
    if (
      prev.error === null
      && prev.isLimitUp === next.isLimitUp && prev.isLimitDown === next.isLimitDown
      && prev.referencePrice === next.referencePrice
      && rowsEqual(prev.bids, next.bids) && rowsEqual(prev.asks, next.asks)
    ) return;
    p.data = next;
  } catch (e) {
    if (ctrl.signal.aborted) return;
    const msg = e instanceof Error ? e.message : String(e);
    if (p.data.error === msg) return;
    p.data = { ...p.data, error: msg };
  } finally {
    p.inFlight = false;
  }
  for (const l of p.listeners) l(p.data);
}

function subscribeQuoteBook(symbol: string, cb: (d: QuoteBookData) => void): () => void {
  let p = pollers.get(symbol);
  if (!p) {
    const created: Poller = {
      data: EMPTY,
      listeners: new Set(),
      timer: setInterval(() => pollOnce(symbol, created), POLL_MS),
      ctrl: null,
      inFlight: false,
    };
    p = created;
    pollers.set(symbol, p);
    pollOnce(symbol, p);
  } else if (p.data !== EMPTY) {
    cb(p.data);   // 已有資料直接帶入,第二個訂閱者不用等下一輪
  }
  p.listeners.add(cb);
  return () => {
    p.listeners.delete(cb);
    if (p.listeners.size === 0) {
      clearInterval(p.timer);
      p.ctrl?.abort();
      pollers.delete(symbol);
    }
  };
}

export function useQuoteBook(symbol: string | null): QuoteBookData {
  const [data, setData] = useState<QuoteBookData>(EMPTY);
  useEffect(() => {
    setData(EMPTY);   // 切 symbol 清舊資料
    if (!symbol) return;
    return subscribeQuoteBook(symbol, setData);
  }, [symbol]);
  return data;
}
