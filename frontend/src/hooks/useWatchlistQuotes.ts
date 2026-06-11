import { useEffect, useMemo, useRef, useState } from "react";
import { subscribeTicks } from "./useSignalsStream";
import { useSnapshotCache } from "./useSnapshotCache";

export interface WatchlistQuote {
  price: number | null;     // 最新 price（snapshot.last_price 或 WS tick）
  prevClose: number | null;
  changePct: number | null; // (price - prevClose) / prevClose * 100
}

/**
 * Watchlist 即時報價聚合：
 *  - prevClose / 初始 price 從 useSnapshotCache 拿
 *  - 後續 price 由 module-level tickBus（useSignalsStream 已導出）即時推
 *  - changePct 由前兩者算
 *
 * Symbols 變動時：自動拉新檔 snapshot；舊檔的 livePrice 保留。
 */
export function useWatchlistQuotes(symbols: string[]): Record<string, WatchlistQuote> {
  const snapshot = useSnapshotCache(symbols);
  const [livePrices, setLivePrices] = useState<Record<string, number>>({});

  // 後端推的是全部訂閱 symbol 的逐筆 tick(行情尖峰每秒數十次):
  // 無關 symbol 與同價 tick 都回傳 prev 讓 React bail out,不打 re-render
  const symbolSet = useMemo(() => new Set(symbols), [symbols]);
  const symbolSetRef = useRef(symbolSet);
  symbolSetRef.current = symbolSet;

  useEffect(() => {
    const unsub = subscribeTicks((t) => {
      if (!symbolSetRef.current.has(t.symbol)) return;
      setLivePrices((prev) => (prev[t.symbol] === t.price ? prev : { ...prev, [t.symbol]: t.price }));
    });
    return unsub;
  }, []);

  const out: Record<string, WatchlistQuote> = {};
  for (const sym of symbols) {
    const entry = snapshot[sym] ?? { prevClose: null, lastPrice: null };
    const price = livePrices[sym] ?? entry.lastPrice;
    const prevClose = entry.prevClose;
    const changePct =
      price !== null && prevClose !== null && prevClose !== 0
        ? ((price - prevClose) / prevClose) * 100
        : null;
    out[sym] = { price, prevClose, changePct };
  }
  return out;
}
