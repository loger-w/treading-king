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
  // Phase 3 (optional — present if signal engine started)
  ws_connections?: {
    active: number;
    subscribed_symbols: number;
    max_capacity: number;
    status: string;
  };
  signal_engine?: {
    queue_depth: number;
    lag_ms: number;
    dropped_today: number;
    degraded: boolean;
    active_count: number;
    writer_buffer: number;
  };
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
  // Phase 3
  "cdp_ah", "cdp_nh", "cdp", "cdp_nl", "cdp_al",
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
  // Phase 3 CDP fields — Screener 目前不回，留 optional 給未來/UI 顯示「—」
  cdp_ah?: number | null;
  cdp_nh?: number | null;
  cdp?: number | null;
  cdp_nl?: number | null;
  cdp_al?: number | null;
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
// Phase 3: WindowCondition / ActiveSignal / Watchlist / Candles / CDP
// ---------------------------------------------------------------------------

export type WindowConditionType = "price_change_pct" | "volume_burst" | "trade_count";
export type WindowSeconds = 60 | 180 | 300 | 600 | 1800;

export interface WindowCondition {
  type: WindowConditionType;
  window_seconds: WindowSeconds;
  operator: "gt" | "gte" | "lt" | "lte";
  value: number;
}

export interface ActiveFilter extends Filter {
  window_conditions?: WindowCondition[];
}

export type Scope =
  | { type: "watchlist" }
  | { type: "symbols"; symbols: string[] };

export interface ActiveSignal {
  id: string;
  name: string;
  filter_json: ActiveFilter;
  scope: Scope;
  cooldown_seconds: number;
  ignore_auctions: boolean;
  enabled: boolean;
  created_at: string;
}

export interface ActiveSignalsResponse {
  active_signals: ActiveSignal[];
}

export interface WatchlistRow {
  symbol: string;
  name: string | null;
  market: string | null;
  is_etf: boolean | null;
  added_at: string | null;
  note: string | null;
}

export interface WatchlistResponse {
  watchlist: WatchlistRow[];
  count: number;
}

export interface IntradayCandle {
  date: string;       // ISO with offset, e.g. "2026-05-12T09:00:00.000+08:00"
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  average: number;    // 富邦給的 minute VWAP
}

export interface IntradayCandlesResponse {
  date: string;
  symbol: string;
  data: IntradayCandle[];
}

export interface CdpLevels {
  ah: number;
  nh: number;
  cdp: number;
  nl: number;
  al: number;
  as_of_date: string;
}

export interface SignalLogRow {
  id: number;
  active_signal_id: string | null;
  symbol: string;
  triggered_at: string;
  trigger_price: number | null;
  trigger_volume: number | null;
  context_json: Record<string, unknown> | null;
}

export interface SignalsHistoryResponse {
  signals: SignalLogRow[];
  count: number;
}

export interface TodayCountsRow {
  symbol: string;
  active_signal_id: string;
}

export interface TodayCountsResponse {
  as_of: string;
  today_start: string;
  counts: TodayCountsRow[];
}

// Realtime WS payload
export interface SignalEvent {
  event: "signal";
  data: {
    active_signal_id: string;
    active_signal_name: string;
    symbol: string;
    triggered_at: string;
    trigger_price: number;
    trigger_volume: number;
  };
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

  watchlist: {
    list: () => fetchJSON<WatchlistResponse>("/api/watchlist"),
    add: (symbol: string, note?: string) =>
      fetchJSON<{symbol: string; status: string}>("/api/watchlist", {
        method: "POST",
        body: JSON.stringify({ symbol, note: note ?? null }),
      }),
    remove: (symbol: string) =>
      fetchJSON<void>(`/api/watchlist/${encodeURIComponent(symbol)}`, {
        method: "DELETE",
      }),
  },

  activeSignals: {
    list: () => fetchJSON<ActiveSignalsResponse>("/api/active_signals"),
    create: (payload: Omit<ActiveSignal, "id" | "created_at">) =>
      fetchJSON<ActiveSignal>("/api/active_signals", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    update: (id: string, payload: Omit<ActiveSignal, "id" | "created_at">) =>
      fetchJSON<ActiveSignal>(`/api/active_signals/${encodeURIComponent(id)}`, {
        method: "PUT",
        body: JSON.stringify(payload),
      }),
    delete: (id: string) =>
      fetchJSON<void>(`/api/active_signals/${encodeURIComponent(id)}`, {
        method: "DELETE",
      }),
  },

  signalsHistory: (params: {
    symbol?: string; active_signal_id?: string;
    since?: string; limit?: number;
  } = {}) => {
    const qs = new URLSearchParams();
    if (params.symbol) qs.set("symbol", params.symbol);
    if (params.active_signal_id) qs.set("active_signal_id", params.active_signal_id);
    if (params.since) qs.set("since", params.since);
    if (params.limit) qs.set("limit", String(params.limit));
    return fetchJSON<SignalsHistoryResponse>(`/api/signals/history?${qs.toString()}`);
  },

  signalsTodayCounts: () =>
    fetchJSON<TodayCountsResponse>("/api/signals/today_counts"),

  candlesIntraday: (symbol: string) =>
    fetchJSON<IntradayCandlesResponse>(
      `/api/candles/${encodeURIComponent(symbol)}/intraday`,
    ),

  cdp: (symbol: string) =>
    fetchJSON<CdpLevels>(`/api/cdp/${encodeURIComponent(symbol)}`),
};
