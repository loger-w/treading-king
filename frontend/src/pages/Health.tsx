import { useEffect, useState } from "react";
import { api, ApiError, type HealthResponse, type QuoteResponse } from "../lib/api";

export function Health() {
  return (
    <div className="mx-auto grid max-w-[1200px] grid-cols-[1fr_1.6fr] gap-20 px-12 py-16 max-lg:grid-cols-1 max-lg:gap-12 max-lg:px-6 max-lg:py-8">
      <SystemStatus />
      <QuoteLookup />
    </div>
  );
}

function SystemStatus() {
  const [data, setData] = useState<HealthResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;

    async function load() {
      try {
        const res = await api.health();
        if (alive) {
          setData(res);
          setError(null);
        }
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      }
    }

    load();
    const interval = setInterval(load, 5000);
    return () => {
      alive = false;
      clearInterval(interval);
    };
  }, []);

  return (
    <section>
      <div className="label-small text-accent mb-2.5">壹</div>
      <h2 className="h-display text-[36px] mb-6">系統狀態</h2>

      <p className="font-serif text-[19px] leading-[1.5] text-ink mb-6">
        後端報告兩個外部相依：
        <em className="font-serif italic text-accent">富邦</em>{" "}
        是行情資料來源，
        <em className="font-serif italic text-accent">Supabase</em>{" "}
        是 Postgres 持久層，存放股票主檔、策略與訊號歷史。
      </p>

      {error && (
        <div className="border border-accent/40 bg-accent/10 px-4 py-3 mb-4 text-sm text-ink">
          <span className="font-medium text-accent">後端無法連線。</span>{" "}
          <span className="text-ink-muted">{error}</span>
        </div>
      )}

      <div className="border-section pt-4">
        <StatusRow
          name="富邦 Neo SDK"
          desc="DMA 登入 · 即時 channel 支援 trades / books / ticks / snapshot / tickers"
          status={data?.fubon_status}
          lastError={data?.fubon_last_error}
        />
        <StatusRow
          name="Supabase"
          desc="Postgres — symbols / strategies / signals_log 等表"
          status={data?.supabase_status}
          lastError={data?.supabase_last_error}
        />
        <StatusRow
          name="交易日"
          desc="目前用週一至五判斷（Phase 2.5 才接 TWSE 行事曆）"
          status={data?.is_trading_day ? "ok" : "degraded"}
          customLabel={data?.is_trading_day ? "是" : "否"}
        />
        <StatusRow
          name="指標快取"
          desc={cacheDesc(data)}
          status={cacheStatusColor(data?.cache_last_run_status)}
          customLabel={cacheLabel(data?.cache_last_run_status)}
        />
      </div>

      <p className="mt-6 text-xs text-ink-dim italic font-serif">
        每五秒更新一次。後端{" "}
        <code className="font-sans not-italic">/api/health</code>。
      </p>
    </section>
  );
}

function cacheDesc(data: HealthResponse | null): string {
  if (!data) return "檢查中…";
  if (!data.cache_last_success_at) return "尚未跑過成功的 cache — 切到「篩股」分頁";
  const when = new Date(data.cache_last_success_at);
  const formatted = when.toLocaleString("zh-TW", {
    month: "long",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  return `上次成功於 ${formatted}`;
}

function cacheStatusColor(
  s: HealthResponse["cache_last_run_status"] | undefined,
): "ok" | "degraded" | "error" | undefined {
  if (s === "done") return "ok";
  if (s === "running") return "degraded";
  if (s === "failed") return "error";
  return undefined;
}

function cacheLabel(
  s: HealthResponse["cache_last_run_status"] | undefined,
): string {
  switch (s) {
    case "done":
      return "完成";
    case "running":
      return "執行中";
    case "failed":
      return "失敗";
    case "skipped":
      return "略過";
    default:
      return "尚無";
  }
}

function StatusRow(props: {
  name: string;
  desc: string;
  status: string | undefined;
  lastError?: string | null;
  customLabel?: string;
}) {
  const { name, desc, status, lastError, customLabel } = props;
  const label =
    customLabel ??
    (status === "ok"
      ? "運作中"
      : status === "degraded"
      ? "降級"
      : status === "error"
      ? "離線"
      : "檢查中…");
  const klass =
    status === "ok"
      ? "text-bull"
      : status === "error"
      ? "text-bear"
      : status === "degraded"
      ? "text-accent"
      : "text-ink-dim";

  return (
    <div className="flex items-baseline justify-between border-b border-line py-3.5">
      <div>
        <div className="text-base font-medium text-ink">{name}</div>
        <div className="mt-0.5 text-xs text-ink-dim">
          {desc}
          {lastError && (
            <>
              {" — "}
              <span className="text-bear">{lastError}</span>
            </>
          )}
        </div>
      </div>
      <div className={`font-serif italic font-semibold text-lg ${klass}`}>
        {label}
      </div>
    </div>
  );
}

function QuoteLookup() {
  const [symbol, setSymbol] = useState("2330");
  const [pending, setPending] = useState("2330");
  const [data, setData] = useState<QuoteResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function load(sym: string) {
    setLoading(true);
    setError(null);
    try {
      const res = await api.quote(sym);
      setData(res);
    } catch (e) {
      setData(null);
      if (e instanceof ApiError) {
        const detail = typeof e.detail === "object" && e.detail !== null
          ? JSON.stringify(e.detail)
          : String(e.detail);
        setError(`${e.status} — ${detail}`);
      } else {
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load(symbol);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSymbol(pending);
    load(pending);
  }

  return (
    <section>
      <div className="label-small text-accent mb-2.5">貳</div>
      <h2 className="h-display text-[36px] mb-6">查詢報價</h2>

      <form onSubmit={onSubmit} className="bg-bg-card border border-line p-8 max-md:p-6">
        <div className="flex items-center border-b-2 border-line-strong pb-2 mb-6">
          <span className="text-2xs uppercase tracking-[2px] text-ink-dim mr-4">代號</span>
          <input
            type="text"
            value={pending}
            onChange={(e) => setPending(e.target.value.trim())}
            className="flex-1 bg-transparent border-none outline-none font-serif text-xl text-ink placeholder:text-ink-dim/50"
            placeholder="2330"
            autoFocus
          />
          <button
            type="submit"
            disabled={loading}
            className="bg-transparent border-none text-2xl text-accent hover:text-accent-hover px-2 disabled:opacity-40"
          >
            {loading ? "…" : "→"}
          </button>
        </div>

        {error && (
          <div className="border border-accent/40 bg-accent/10 px-4 py-3 text-sm">
            <div className="font-medium text-accent mb-1">查詢失敗</div>
            <div className="text-ink-muted text-xs font-mono">{error}</div>
          </div>
        )}

        {data && !error && <QuoteDisplay q={data} />}
      </form>

      <p className="mt-6 text-xs text-ink-dim italic font-serif">
        手動更新。Phase 3 會接上 WebSocket 即時推播。
      </p>
    </section>
  );
}

function QuoteDisplay({ q }: { q: QuoteResponse }) {
  const change = q.change ?? 0;
  const pct = q.changePercent ?? 0;
  const isUp = change > 0;
  const isDown = change < 0;
  const dirGlyph = isUp ? "▴" : isDown ? "▾" : "—";
  const dirClass = isUp ? "text-bull" : isDown ? "text-bear" : "text-ink-muted";

  const fmtN = (n: number | undefined, digits = 0) =>
    n === undefined ? "—" : n.toLocaleString("en-US", { maximumFractionDigits: digits });
  const fmtPct = (n: number | undefined) =>
    n === undefined ? "—" : `${Math.abs(n).toFixed(2)}%`;
  const lastPrice = q.lastPrice ?? q.closePrice;

  return (
    <>
      <div className="font-serif italic text-sm text-ink-dim mb-1">{q.name ?? ""}</div>
      <h3 className="h-display text-[42px] mb-1">{q.symbol}</h3>
      <div className="text-xs text-ink-dim tracking-[1.5px] mb-6">
        {q.exchange} {q.type} · NT$
        {q.isClose && <span className="ml-3 text-accent">[ 已收盤 ]</span>}
      </div>

      <div className="flex items-baseline gap-4 border-y border-line py-4 mb-6">
        <span className="label-tiny">收盤</span>
        <span className="h-display text-[56px] tabular-nums">{fmtN(lastPrice)}</span>
        <span className={`font-serif italic text-[22px] ml-auto ${dirClass} tabular-nums`}>
          <em>{dirGlyph}</em> {fmtN(Math.abs(change))} ({fmtPct(pct)})
        </span>
      </div>

      <div className="grid grid-cols-3 max-md:grid-cols-2 gap-y-4 gap-x-8 border-b border-line pb-6">
        <Stat label="開盤" value={fmtN(q.openPrice)} />
        <Stat label="最高" value={fmtN(q.highPrice)} className="text-bull" />
        <Stat label="最低" value={fmtN(q.lowPrice)} className="text-bear" />
        <Stat label="成交量" value={fmtN(q.total?.tradeVolume)} />
        <Stat
          label="成交金額"
          value={
            q.total?.tradeValue
              ? `${(q.total.tradeValue / 1e9).toFixed(1)} B`
              : "—"
          }
        />
        <Stat label="振幅" value={fmtPct(q.amplitude)} />
      </div>

      {(q.bids?.length || q.asks?.length) && (
        <>
          <div className="mt-6 mb-2 font-serif italic text-sm text-ink-dim">
            委買賣 五檔
          </div>
          <div className="grid grid-cols-2 gap-6">
            <Book rows={q.bids ?? []} side="bid" />
            <Book rows={q.asks ?? []} side="ask" />
          </div>
        </>
      )}
    </>
  );
}

function Stat({ label, value, className = "" }: { label: string; value: string; className?: string }) {
  return (
    <div>
      <div className="label-tiny">{label}</div>
      <div className={`font-serif text-xl font-medium mt-0.5 tabular-nums ${className}`}>
        {value}
      </div>
    </div>
  );
}

function Book({ rows, side }: { rows: Array<{ price: number; size: number }>; side: "bid" | "ask" }) {
  return (
    <table className="w-full">
      <tbody>
        {rows.slice(0, 5).map((r, i) => (
          <tr key={i} className="border-b border-line/50">
            <td
              className={`py-1.5 text-sm tabular-nums font-medium ${
                side === "bid" ? "text-bull" : "text-bear"
              }`}
            >
              {r.price.toLocaleString()}
            </td>
            <td className="py-1.5 text-right text-sm tabular-nums text-ink-dim">
              {r.size.toLocaleString()}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
