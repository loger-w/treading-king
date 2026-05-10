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

export const api = {
  health: () => fetchJSON<HealthResponse>("/api/health"),
  quote: (symbol: string) =>
    fetchJSON<QuoteResponse>(`/api/quote/${encodeURIComponent(symbol)}`),
};
