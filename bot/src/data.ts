import { config } from "./config";
import type { IntradayCandle, CdpLevels, MaLevels } from "../../frontend/src/lib/api";
import { isIndexCode, indexName } from "../../frontend/src/lib/index-symbols";

async function get<T>(path: string): Promise<T> {
  const headers: Record<string, string> = {};
  if (config.bffApiKey) headers["X-API-Key"] = config.bffApiKey;
  // 後端/富邦 hang 時不要無限等;逾時丟錯,由上層 catch 成「查詢失敗」(spec §8 不靜默)
  const res = await fetch(`${config.backendBaseUrl}${path}`, { headers, signal: AbortSignal.timeout(8000) });
  if (!res.ok) throw new Error(`${path} → ${res.status}`);
  return res.json() as Promise<T>;
}

export interface QuoteLevel { price: number; size: number; }
export interface QuoteResp {
  bids: QuoteLevel[]; asks: QuoteLevel[];
  is_limit_up_bid: boolean; is_limit_up_ask: boolean;
  is_limit_down_bid: boolean; is_limit_down_ask: boolean;
}
export interface CandlesResp {
  date: string; symbol: string; data: IntradayCandle[]; prev_close: number | null;
}

export const getQuote = (s: string) => get<QuoteResp>(`/api/quote/${encodeURIComponent(s)}`);
export const getCandles = (s: string) => get<CandlesResp>(`/api/candles/${encodeURIComponent(s)}/intraday`);
export const getCdp = (s: string) => get<CdpLevels>(`/api/cdp/${encodeURIComponent(s)}`);
export const getMa = (s: string) => get<MaLevels>(`/api/ma/${encodeURIComponent(s)}`);

interface SymbolRow { symbol: string; name: string; }
export async function getName(s: string): Promise<string | null> {
  if (isIndexCode(s)) return indexName(s); // 指數不在 symbols 表,走常數
  try {
    const r = await get<{ results: SymbolRow[] }>(`/api/symbols?search=${encodeURIComponent(s)}&limit=20`);
    return r.results.find((row) => row.symbol === s)?.name ?? null;
  } catch { return null; }
}
