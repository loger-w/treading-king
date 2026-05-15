import { useEffect, useRef, useState } from "react";
import { subscribeTicks } from "./useSignalsStream";

const MAX_TICKS = 50;

export type TradeSide = "buy" | "sell" | "neutral";

export interface TradeRow {
  time: Date;
  price: number;
  size: number;
  side: TradeSide;
}

/**
 * 累積 selected symbol 的最近 50 筆 tick，並判定內外盤。
 *
 * 內外盤判定（優先用 bid/ask）：
 *  - tick 帶 bid/ask（成交當下的最佳買賣價）：price ≥ ask → 外盤 (buy)；
 *    price ≤ bid → 內盤 (sell)；介於中間 → neutral
 *  - 缺 bid/ask 時 fallback 到 uptick rule：與前一筆比較
 */
export function useTradeTape(symbol: string | null): TradeRow[] {
  const [rows, setRows] = useState<TradeRow[]>([]);
  const lastPriceRef = useRef<number | null>(null);
  const lastSideRef = useRef<TradeSide>("neutral");

  useEffect(() => {
    if (!symbol) {
      setRows([]);
      lastPriceRef.current = null;
      lastSideRef.current = "neutral";
      return;
    }

    // Symbol 切換時清空
    setRows([]);
    lastPriceRef.current = null;
    lastSideRef.current = "neutral";

    const unsub = subscribeTicks((t) => {
      if (t.symbol !== symbol) return;

      let side: TradeSide = "neutral";
      if (t.bid != null && t.ask != null && t.bid > 0 && t.ask > 0) {
        if (t.price >= t.ask) side = "buy";
        else if (t.price <= t.bid) side = "sell";
        else side = "neutral";
      } else if (lastPriceRef.current !== null) {
        if (t.price > lastPriceRef.current) side = "buy";
        else if (t.price < lastPriceRef.current) side = "sell";
        else side = lastSideRef.current;
      }
      lastPriceRef.current = t.price;
      lastSideRef.current = side;

      setRows((prev) => [{ time: new Date(), price: t.price, size: t.size, side }, ...prev].slice(0, MAX_TICKS));
    });
    return unsub;
  }, [symbol]);

  return rows;
}
