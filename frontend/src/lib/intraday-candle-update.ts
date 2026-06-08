import type { IntradayCandle } from "./api";

/**
 * 決定收到一筆 intraday candles 回應時,圖表的 candles state 該換成什麼。
 * 回 null 代表「維持現狀、不要 setState」。
 *
 * 兩個防護:
 * 1. 過期回應:發起請求時的 requestSymbol 已不是當前選中的 currentSymbol —— 使用者
 *    在請求飛行途中切了股票。較慢的舊請求若回來蓋畫面,會顯示錯誤股票,或把剛切到
 *    的股票清成載入中。
 * 2. 空資料:富邦對某些股票/時段會回空(或後端缺 data 欄位 → undefined)。拿空去蓋
 *    掉已顯示的圖,會讓 30 秒輪詢每隔一次就把好好的圖清成載入中。
 */
export function resolveCandleUpdate(
  requestSymbol: string,
  currentSymbol: string | null,
  incoming: IntradayCandle[] | null | undefined,
): IntradayCandle[] | null {
  if (requestSymbol !== currentSymbol) return null;
  if (!incoming || incoming.length === 0) return null;
  return incoming;
}
