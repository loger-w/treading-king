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
 * 內外盤判定（簡化）：
 *  - 比前一筆價高 → buy (外盤，紅)
 *  - 比前一筆價低 → sell (內盤，綠)
 *  - 平盤 → 沿用上一筆方向（首筆預設 neutral）
 *
 * 注意：本期不顯示單量（WS tick payload 目前不含 size — fubon_ws.py broadcast
 * payload 只有 symbol/price）。等 backend broadcast 加 size 後 hook 補上 vol。
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
      if (lastPriceRef.current !== null) {
        if (t.price > lastPriceRef.current) side = "buy";
        else if (t.price < lastPriceRef.current) side = "sell";
        else side = lastSideRef.current;  // 平盤沿用上次
      }
      lastPriceRef.current = t.price;
      lastSideRef.current = side;

      setRows((prev) => [{ time: new Date(), price: t.price, size: t.size, side }, ...prev].slice(0, MAX_TICKS));
    });
    return unsub;
  }, [symbol]);

  return rows;
}
