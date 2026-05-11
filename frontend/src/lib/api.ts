// Plan §Phase 1: 一律用 relative path（vite.config.ts proxy 會接到 :8000）

const BFF_API_KEY = import.meta.env.VITE_BFF_API_KEY ?? "";

async function fetchJSON<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set("Content-Type", "application/json");
  if (BFF_API_KEY) headers.set("X-API-Key", BFF_API_KEY);

  const res = await fetch(path, { ...init, headers });
  if (!res.ok) {
    // Body 只能 consume 一次：先讀 text，再 try parse JSON
    const text = await res.text();
    let detail: unknown = text;
    try {
      detail = JSON.parse(text);
    } catch {
      /* keep as raw text */
    }
    throw new ApiError(res.status, detail);
  }
  // 204 No Content 沒 body
  if (res.status === 204) return undefined as T;
  return res.json();
}

export class ApiError extends Error {
  constructor(public status: number, public detail: unknown) {
    super(`API ${status}: ${JSON.stringify(detail)}`);
  }
}

// ---------------------------------------------------------------------------
// Health
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Quote
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Cache
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Screener DSL — 對應 backend models/condition.py
// ---------------------------------------------------------------------------

export const ALL_FIELDS = [
  "close", "change_pct", "volume", "amount",
  "rsi_14", "macd", "macd_signal",
  "kdj_k", "kdj_d", "kdj_j",
  "sma_5", "sma_20", "sma_60",
  "bbands_upper", "bbands_middle", "bbands_lower",
] as const;
export type ConditionField = typeof ALL_FIELDS[number];

export const ALL_OPERATORS = ["gt", "gte", "lt", "lte", "eq"] as const;
export type ConditionOperator = typeof ALL_OPERATORS[number];

export interface Condition {
  field: ConditionField;
  operator: ConditionOperator;
  value: number | ConditionField;
  days_ago?: number;
}

export interface Filter {
  schema_version?: number;
  market: Array<"TWSE" | "OTC">;
  exclude_etf: boolean;
  conditions: Condition[];
  logic: "AND" | "OR";
  limit?: number;
}

export interface ScreenResultRow {
  symbol: string;
  name: string | null;
  market: string | null;
  is_etf: boolean | null;
  close: number | null;
  change_pct: number | null;
  volume: number | null;
  amount: number | null;
  rsi_14: number | null;
  macd: number | null;
  macd_signal: number | null;
  kdj_k: number | null;
  kdj_d: number | null;
  kdj_j: number | null;
  sma_5: number | null;
  sma_20: number | null;
  sma_60: number | null;
  bbands_upper: number | null;
  bbands_middle: number | null;
  bbands_lower: number | null;
}

export interface ScreenResponse {
  as_of_date: string;
  total_scanned: number;
  count: number;
  results: ScreenResultRow[];
  filter: Filter;
  // Legacy phase 2a wrapper 才有：
  rule?: string;
  criteria?: string;
}

// ---------------------------------------------------------------------------
// Strategies
// ---------------------------------------------------------------------------

export interface Strategy {
  id: string;
  name: string;
  description: string | null;
  filter_json: Filter;
  created_at: string;
}

export interface StrategiesResponse {
  strategies: Strategy[];
}

// ---------------------------------------------------------------------------
// Symbol search
// ---------------------------------------------------------------------------

export interface SymbolSearchRow {
  symbol: string;
  name: string;
  market: "TWSE" | "OTC";
  is_etf: boolean;
}

export interface SymbolSearchResponse {
  results: SymbolSearchRow[];
}

// ---------------------------------------------------------------------------
// API surface
// ---------------------------------------------------------------------------

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

  // 新版（Phase 2b）
  screen: (filter: Filter) =>
    fetchJSON<ScreenResponse>("/api/screen", {
      method: "POST",
      body: JSON.stringify(filter),
    }),

  // 舊版（Phase 2a 兼容）
  screenRsiOversold: (limit = 200) =>
    fetchJSON<ScreenResponse>(`/api/screen/rsi-oversold?limit=${limit}`),

  strategies: {
    list: () => fetchJSON<StrategiesResponse>("/api/strategies"),
    create: (s: { name: string; description?: string | null; filter_json: Filter }) =>
      fetchJSON<Strategy>("/api/strategies", {
        method: "POST",
        body: JSON.stringify(s),
      }),
    delete: (id: string) =>
      fetchJSON<void>(`/api/strategies/${encodeURIComponent(id)}`, {
        method: "DELETE",
      }),
  },

  symbols: (search = "", limit = 20) =>
    fetchJSON<SymbolSearchResponse>(
      `/api/symbols?search=${encodeURIComponent(search)}&limit=${limit}`,
    ),
};
