// Plan §Phase 1: 一律用 relative path（vite.config.ts proxy 會接到 :8000）
// 不要寫 absolute URL，否則繞過 proxy → CORS

const BFF_API_KEY = import.meta.env.VITE_BFF_API_KEY ?? "";

async function fetchJSON<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set("Content-Type", "application/json");
  if (BFF_API_KEY) headers.set("X-API-Key", BFF_API_KEY);

  const res = await fetch(path, { ...init, headers });
  if (!res.ok) {
    let detail: unknown = null;
    try {
      detail = await res.json();
    } catch {
      detail = await res.text();
    }
    throw new ApiError(res.status, detail);
  }
  return res.json();
}

export class ApiError extends Error {
  constructor(public status: number, public detail: unknown) {
    super(`API ${status}: ${JSON.stringify(detail)}`);
  }
}

export interface HealthResponse {
  status: "ok" | "degraded" | "error";
  fubon_status: "ok" | "error" | "degraded";
  fubon_last_error: string | null;
  supabase_status: "ok" | "error";
  supabase_last_error: string | null;
  is_trading_day: boolean;
  cache_last_success_at: string | null;
  cache_last_run_status: "running" | "done" | "failed" | "skipped" | null;
}

export interface QuoteResponse {
  date?: string;
  type?: string;
  exchange?: string;
  market?: string;
  symbol: string;
  name?: string;
  referencePrice?: number;
  previousClose?: number;
  openPrice?: number;
  highPrice?: number;
  lowPrice?: number;
  closePrice?: number;
  avgPrice?: number;
  change?: number;
  changePercent?: number;
  amplitude?: number;
  lastPrice?: number;
  bids?: Array<{ price: number; size: number }>;
  asks?: Array<{ price: number; size: number }>;
  total?: { tradeValue?: number; tradeVolume?: number };
  isClose?: boolean;
}

export interface CacheRunRow {
  run_date: string;
  is_trading_day: boolean;
  started_at: string | null;
  finished_at: string | null;
  symbols_total: number | null;
  symbols_done: number | null;
  status: "running" | "done" | "failed" | "skipped";
  error_text: string | null;
}

export interface CacheStatusResponse {
  running: boolean;
  latest_run: CacheRunRow | null;
}

export interface CacheRefreshResponse {
  status: "accepted";
  limit: number | null;
  message: string;
}

export interface RsiOversoldResult {
  symbol: string;
  name: string | null;
  market: string | null;
  is_etf: boolean | null;
  close: number | null;
  change_pct: number | null;
  volume: number | null;
  rsi_14: number | null;
}

export interface RsiOversoldResponse {
  rule: "rsi-oversold";
  criteria: string;
  as_of_date: string;
  count: number;
  results: RsiOversoldResult[];
}

export const api = {
  health: () => fetchJSON<HealthResponse>("/api/health"),
  quote: (symbol: string) =>
    fetchJSON<QuoteResponse>(`/api/quote/${encodeURIComponent(symbol)}`),

  cacheStatus: () => fetchJSON<CacheStatusResponse>("/api/cache/status"),
  cacheRefresh: (limit?: number) => {
    const qs = limit ? `?limit=${limit}` : "";
    return fetchJSON<CacheRefreshResponse>(`/api/cache/refresh${qs}`, {
      method: "POST",
    });
  },

  screenRsiOversold: (limit = 200) =>
    fetchJSON<RsiOversoldResponse>(
      `/api/screen/rsi-oversold?limit=${limit}`,
    ),
};
