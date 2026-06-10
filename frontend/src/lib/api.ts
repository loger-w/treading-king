// 一律用 relative path（vite.config.ts proxy 會接到 :8000）
import type { CapitalOrder } from "./capital-orders";

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
// Quote — QuoteBook 用的五檔欄位
// ---------------------------------------------------------------------------

export interface QuoteResponse {
  bids?: Array<{ price: number; size: number }>;
  asks?: Array<{ price: number; size: number }>;
  is_limit_up_bid?: boolean;
  is_limit_up_ask?: boolean;
  is_limit_down_bid?: boolean;
  is_limit_down_ask?: boolean;
}

// ---------------------------------------------------------------------------
// Condition DSL — 對應 backend models/condition.py,供 ActiveFilter 繼承用
// ---------------------------------------------------------------------------

export const ALL_FIELDS = [
  "close",
  "cdp_ah", "cdp_nh", "cdp", "cdp_nl", "cdp_al",
  "sma_5", "sma_20",
  "day_change_pct", "day_volume",
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
  conditions: Condition[];
  logic: "AND" | "OR";
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
// WindowCondition / ActiveSignal / Watchlist / Candles / CDP
// ---------------------------------------------------------------------------

export type WindowConditionType = "price_change_pct" | "volume_burst" | "trade_count";
export type WindowSeconds = 60 | 180 | 300 | 600 | 1800;

export interface WindowCondition {
  type: WindowConditionType;
  window_seconds: WindowSeconds;
  operator: "gt" | "gte" | "lt" | "lte";
  value: number;
}

export interface CdpProximity {
  levels: Array<"ah" | "nh" | "cdp" | "nl" | "al">;
  tolerance_ticks: number;
}

export interface MAProximity {
  levels: Array<"sma_5" | "sma_20">;
  tolerance_ticks: number;
}

export type CdpLevel = "ah" | "nh" | "cdp" | "nl" | "al";

export interface LimitUpOpenTouchStrategy {
  type: "limit_up_open_touch";
  lock_seconds: number;
  levels: CdpLevel[];
  tolerance_ticks: number;
}

export interface BreakoutRetestStrategy {
  type: "breakout_retest";
  early_window_minutes: number;
  surge_pct: number;
  surge_window_seconds: WindowSeconds;
  retest_within_minutes: number;
  levels: CdpLevel[];
  tolerance_ticks: number;
}

export type StrategyConfig = LimitUpOpenTouchStrategy | BreakoutRetestStrategy;

export interface ActiveFilter extends Filter {
  // schema_version 已從 Filter inherit,不重複宣告
  window_conditions?: WindowCondition[];
  cdp_proximity?: CdpProximity | null;
  ma_proximity?: MAProximity | null;
  strategy?: StrategyConfig | null;
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
  enabled: boolean;
  notify_discord: boolean;
  created_at: string;
}

export interface ActiveSignalsResponse {
  active_signals: ActiveSignal[];
}

export interface MonitorListItem {
  symbol: string;
  added_at: string;
  name: string | null;
  market: string | null;
  is_etf: boolean | null;
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

// ---------------------------------------------------------------------------
// Bookmarks(取代單一 watchlist)
// ---------------------------------------------------------------------------

export interface BookmarkGroup {
  id: string;
  name: string;
  sort_order: number;
  is_system: boolean;
  source_type: string | null;   // 'top_gainers' for 大漲股
  count: number;
}

export interface BookmarksResponse {
  groups: BookmarkGroup[];
  count: number;
}

export interface BookmarkItem extends WatchlistRow {
  // 系統書籤(大漲股)的額外欄位
  change_pct?: number | null;
  volume_lots?: number | null;
  captured_at?: string | null;
}

export interface BookmarkItemsResponse {
  items: BookmarkItem[];
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

export interface MXFCandle {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  average: number;
}

export interface MXFActiveSymbolResponse {
  symbol: string;
}

export type CurrentSession = "day" | "night" | "closed";

export interface MXFCandlesResponse {
  symbol: string;
  timeframe: number;
  candles: MXFCandle[];
  current_session: CurrentSession;
}

export interface IntradayCandlesResponse {
  date: string;
  symbol: string;
  data: IntradayCandle[];
  prev_close: number | null;  // 昨日收盤，給前端算漲跌% / Y 軸 ±10% 用
}

export interface CdpLevels {
  ah: number;
  nh: number;
  cdp: number;
  nl: number;
  al: number;
  as_of_date: string;
}

export interface CamarillaLevels {
  h4: number;
  h3: number;
  h2: number;
  h1: number;
  l1: number;
  l2: number;
  l3: number;
  l4: number;
  as_of_date: string;
  prev_close: number;
}

export interface MaLevels {
  symbol: string;
  sma_5: number | null;
  sma_20: number | null;
  as_of_date: string | null;
}

export interface SnapshotRow {
  symbol: string;
  prev_close: number | null;
  last_price: number | null;
}

export interface SnapshotResponse {
  quotes: SnapshotRow[];
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

export interface TouchMeta {
  level: string;  // "ah" / "nh" / "cdp" / "nl" / "al" 或 "sma_5" / "sma_20"
  direction: "from_below" | "from_above" | "horizontal";
  role: "resistance" | "support" | "touch";
  touch_index: number;
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
    cdp_touch?: TouchMeta;
    ma_touch?: TouchMeta;
  };
}

// ---------------------------------------------------------------------------
// Capital 下單面板
// ---------------------------------------------------------------------------

export type { CapitalOrder } from "./capital-orders";
export interface CapitalPosition {
  stock_no: string; name: string; qty: number; avg_price: number;
}
export interface CapitalStockOrderReq {
  stock_no: string; buy_sell: "buy" | "sell"; price: number; qty: number;
  price_type?: "limit" | "market"; time_in_force?: "ROD" | "IOC" | "FOK";
  trade_kind?: "cash" | "margin" | "short";
}
export interface CapitalOrderResult {
  ok: boolean; code: number; message: string; seq_no: string | null;
}

// ---------------------------------------------------------------------------
// API surface
// ---------------------------------------------------------------------------

export const api = {
  quote: (symbol: string) =>
    fetchJSON<QuoteResponse>(`/api/quote/${encodeURIComponent(symbol)}`),

  preview: (symbol: string | null) =>
    fetchJSON<{ ok: boolean; current: string | null }>("/api/preview", {
      method: "POST",
      body: JSON.stringify({ symbol }),
    }),

  symbols: (search = "", limit = 20) =>
    fetchJSON<SymbolSearchResponse>(
      `/api/symbols?search=${encodeURIComponent(search)}&limit=${limit}`,
    ),

  monitorList: {
    list: () => fetchJSON<{ items: MonitorListItem[]; count: number }>("/api/monitor_list"),
    add: (symbol: string) =>
      fetchJSON("/api/monitor_list", {
        method: "POST",
        body: JSON.stringify({ symbol }),
      }),
    remove: (symbol: string) =>
      fetchJSON(`/api/monitor_list/${encodeURIComponent(symbol)}`, {
        method: "DELETE",
      }),
  },

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

  bookmarks: {
    list: () => fetchJSON<BookmarksResponse>("/api/bookmarks"),
    create: (name: string) =>
      fetchJSON<BookmarkGroup>("/api/bookmarks", {
        method: "POST",
        body: JSON.stringify({ name }),
      }),
    patch: (id: string, payload: { name?: string; sort_order?: number }) =>
      fetchJSON<BookmarkGroup>(`/api/bookmarks/${encodeURIComponent(id)}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      }),
    delete: (id: string) =>
      fetchJSON<void>(`/api/bookmarks/${encodeURIComponent(id)}`, {
        method: "DELETE",
      }),
    items: (id: string) =>
      fetchJSON<BookmarkItemsResponse>(`/api/bookmarks/${encodeURIComponent(id)}/items`),
    addItems: (id: string, symbols: string[]) =>
      fetchJSON<{added: string[]; skipped: string[]; count: number}>(
        `/api/bookmarks/${encodeURIComponent(id)}/items`,
        { method: "POST", body: JSON.stringify({ symbols }) },
      ),
    removeItem: (id: string, symbol: string) =>
      fetchJSON<void>(
        `/api/bookmarks/${encodeURIComponent(id)}/items/${encodeURIComponent(symbol)}`,
        { method: "DELETE" },
      ),
    move: (payload: {
      symbols: string[];
      from_group_id: string;
      to_group_ids: string[];
      op: "move" | "copy";
    }) =>
      fetchJSON<{status: string}>("/api/bookmarks/items/move", {
        method: "PATCH",
        body: JSON.stringify(payload),
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

  mxfSymbolActive: () =>
    fetchJSON<MXFActiveSymbolResponse>("/api/mxf/symbol/active"),

  mxfCandles: (tf: number, symbol?: string) =>
    fetchJSON<MXFCandlesResponse>(
      `/api/mxf/candles?tf=${tf}${symbol ? `&symbol=${encodeURIComponent(symbol)}` : ""}`,
    ),

  cdp: (symbol: string) =>
    fetchJSON<CdpLevels>(`/api/cdp/${encodeURIComponent(symbol)}`),

  camarilla: (symbol: string) =>
    fetchJSON<CamarillaLevels>(`/api/camarilla/${encodeURIComponent(symbol)}`),

  ma: (symbol: string) =>
    fetchJSON<MaLevels>(`/api/ma/${encodeURIComponent(symbol)}`),

  quotesSnapshot: (symbols: string[]) =>
    fetchJSON<SnapshotResponse>("/api/quotes/snapshot", {
      method: "POST",
      body: JSON.stringify({ symbols }),
    }),

  // 本機設定可攜檔(書籤 / 訊號規則 / 監聽清單)
  config: {
    // 不走 fetchJSON:要拿 Blob 直接下載,但 API key header 機制照舊
    export: async (): Promise<Blob> => {
      const headers = new Headers();
      if (BFF_API_KEY) headers.set("X-API-Key", BFF_API_KEY);
      const res = await fetch("/api/config/export", { headers });
      if (!res.ok) throw new ApiError(res.status, await res.text());
      return res.blob();
    },
    import: (data: unknown) =>
      fetchJSON<{ status: string }>("/api/config/import", {
        method: "POST",
        body: JSON.stringify(data),
      }),
  },

  capitalStatus: () =>
    fetchJSON<{ status: string; last_error?: string | null }>("/api/capital/status"),
  capitalOrders: () =>
    fetchJSON<{ orders: CapitalOrder[] }>("/api/capital/orders"),
  capitalPositions: () =>
    fetchJSON<{ positions: CapitalPosition[] }>("/api/capital/positions"),
  capitalSubmitStock: (req: CapitalStockOrderReq) =>
    fetchJSON<CapitalOrderResult>("/api/capital/order/stock", {
      method: "POST",
      body: JSON.stringify(req),
    }),
  capitalCancelOrder: (req: { seq_no: string }) =>
    fetchJSON<CapitalOrderResult>("/api/capital/order/cancel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    }),
  capitalCorrectPrice: (req: { seq_no: string; price: number }) =>
    fetchJSON<CapitalOrderResult>("/api/capital/order/correct-price", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    }),
  capitalDecreaseQty: (req: { seq_no: string; qty: number }) =>
    fetchJSON<CapitalOrderResult>("/api/capital/order/decrease", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    }),
};
