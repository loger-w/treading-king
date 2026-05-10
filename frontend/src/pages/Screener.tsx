import { useEffect, useState } from "react";
import {
  api,
  ApiError,
  type CacheStatusResponse,
  type RsiOversoldResponse,
} from "../lib/api";

export function Screener() {
  return (
    <div className="mx-auto grid max-w-[1200px] grid-cols-[1fr_1.6fr] gap-20 px-12 py-16 max-lg:grid-cols-1 max-lg:gap-12 max-lg:px-6 max-lg:py-8">
      <CachePanel />
      <RsiOversoldPanel />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Cache job control
// ---------------------------------------------------------------------------

function CachePanel() {
  const [status, setStatus] = useState<CacheStatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function load() {
    try {
      const res = await api.cacheStatus();
      setStatus(res);
      setError(null);
    } catch (e) {
      setError(formatError(e));
    }
  }

  // Poll: 3s while running, 15s when idle.
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function tick() {
      if (!alive) return;
      await load();
      const interval = status?.running ? 3000 : 15000;
      timer = setTimeout(tick, interval);
    }

    tick();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status?.running]);

  async function trigger(limit?: number) {
    setSubmitting(true);
    setError(null);
    try {
      await api.cacheRefresh(limit);
      // Immediate refresh of status so the UI flips to "running"
      await load();
    } catch (e) {
      setError(formatError(e));
    } finally {
      setSubmitting(false);
    }
  }

  const run = status?.latest_run;
  const isRunning = status?.running ?? false;

  return (
    <section>
      <div className="label-small text-accent mb-2.5">SECTION ONE</div>
      <h2 className="h-display text-[36px] mb-6">Cache</h2>

      <p className="font-serif text-[19px] leading-[1.5] text-ink mb-6">
        The screener reads a daily{" "}
        <em className="font-serif italic text-accent">indicator cache</em> —
        every active symbol's price plus five technicals (RSI, MACD, KDJ, SMA,
        Bollinger). A full run pulls ~2,300 symbols × 8 calls under a 5 req/s
        rate limit; expect roughly an hour.
      </p>

      {error && (
        <div className="border border-accent/40 bg-accent/10 px-4 py-3 mb-4 text-sm text-ink">
          <span className="font-medium text-accent">Cache request failed.</span>{" "}
          <span className="text-ink-muted">{error}</span>
        </div>
      )}

      <div className="border-section pt-4">
        <CacheStatusRow run={run} running={isRunning} />
      </div>

      <div className="mt-6 flex flex-wrap gap-3">
        <button
          type="button"
          onClick={() => trigger(undefined)}
          disabled={submitting || isRunning}
          className="border-2 border-accent bg-transparent px-5 py-2 text-sm uppercase tracking-[2px] text-accent hover:bg-accent/10 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          Run full cache
        </button>
        <button
          type="button"
          onClick={() => trigger(10)}
          disabled={submitting || isRunning}
          className="border border-line-strong bg-transparent px-5 py-2 text-sm uppercase tracking-[2px] text-ink-dim hover:text-ink hover:border-ink-muted disabled:opacity-40 disabled:cursor-not-allowed"
        >
          Dev: limit 10
        </button>
      </div>

      <p className="mt-4 text-xs text-ink-dim italic font-serif">
        Status polled every {isRunning ? "3" : "15"} seconds. Backend at{" "}
        <code className="font-sans not-italic">/api/cache/status</code>.
      </p>
    </section>
  );
}

function CacheStatusRow({
  run,
  running,
}: {
  run: CacheStatusResponse["latest_run"];
  running: boolean;
}) {
  if (!run) {
    return (
      <div className="flex items-baseline justify-between border-b border-line py-3.5">
        <div>
          <div className="text-base font-medium text-ink">No cache yet</div>
          <div className="mt-0.5 text-xs text-ink-dim">
            Run cache job to populate <code>indicator_cache</code>.
          </div>
        </div>
        <div className="font-serif italic font-semibold text-lg text-ink-dim">
          empty
        </div>
      </div>
    );
  }

  const total = run.symbols_total ?? 0;
  const done = run.symbols_done ?? 0;
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;

  const statusColor =
    running || run.status === "running"
      ? "text-accent"
      : run.status === "done"
      ? "text-bull"
      : run.status === "failed"
      ? "text-bear"
      : "text-ink-dim";

  const elapsed =
    run.started_at && run.finished_at
      ? minutesBetween(run.started_at, run.finished_at)
      : null;

  return (
    <div className="border-b border-line py-3.5">
      <div className="flex items-baseline justify-between">
        <div>
          <div className="text-base font-medium text-ink">
            {run.run_date}{" "}
            {!run.is_trading_day && (
              <span className="text-xs text-ink-dim ml-2">
                (non-trading day)
              </span>
            )}
          </div>
          <div className="mt-0.5 text-xs text-ink-dim">
            {done.toLocaleString()} / {total.toLocaleString()} symbols
            {elapsed !== null && (
              <>
                {" — "}
                {elapsed} min
              </>
            )}
            {run.error_text && (
              <>
                {" — "}
                <span className="text-bear">{run.error_text}</span>
              </>
            )}
          </div>
        </div>
        <div className={`font-serif italic font-semibold text-lg ${statusColor}`}>
          {running ? `running ${pct}%` : run.status}
        </div>
      </div>
      {(running || run.status === "running") && (
        <div className="mt-2 h-0.5 bg-line/40 overflow-hidden">
          <div
            className="h-full bg-accent transition-[width] duration-500"
            style={{ width: `${pct}%` }}
          />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// RSI oversold screener
// ---------------------------------------------------------------------------

function RsiOversoldPanel() {
  const [data, setData] = useState<RsiOversoldResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function search() {
    setLoading(true);
    setError(null);
    try {
      const res = await api.screenRsiOversold(200);
      setData(res);
    } catch (e) {
      setData(null);
      setError(formatError(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <section>
      <div className="label-small text-accent mb-2.5">SECTION TWO</div>
      <h2 className="h-display text-[36px] mb-6">Find oversold</h2>

      <p className="font-serif text-[19px] leading-[1.5] text-ink mb-6">
        A first hard-coded rule:{" "}
        <em className="font-serif italic text-accent">
          RSI(14) below 30 with a flat-or-up day
        </em>
        . Reads from the latest successful cache run; nothing live.
      </p>

      <div className="bg-bg-card border border-line p-8 max-md:p-6 mb-4">
        <div className="flex items-center justify-between border-b-2 border-line-strong pb-4 mb-4">
          <div>
            <div className="label-tiny mb-1">Rule</div>
            <div className="font-serif text-base text-ink">
              rsi_14 &lt; 30 <span className="text-ink-dim">AND</span>{" "}
              change_pct ≥ 0
            </div>
          </div>
          <button
            type="button"
            onClick={search}
            disabled={loading}
            className="border-2 border-accent bg-transparent px-5 py-2 text-sm uppercase tracking-[2px] text-accent hover:bg-accent/10 disabled:opacity-40"
          >
            {loading ? "…" : "Find →"}
          </button>
        </div>

        {error && (
          <div className="border border-accent/40 bg-accent/10 px-4 py-3 text-sm">
            <div className="font-medium text-accent mb-1">Screen failed</div>
            <div className="text-ink-muted text-xs font-mono break-all">
              {error}
            </div>
          </div>
        )}

        {data && !error && <ResultTable data={data} />}
      </div>

      <p className="text-xs text-ink-dim italic font-serif">
        Phase 2b will replace this with a free-form condition DSL and saved
        strategies.
      </p>
    </section>
  );
}

function ResultTable({ data }: { data: RsiOversoldResponse }) {
  if (data.count === 0) {
    return (
      <div className="text-center py-8 text-ink-dim font-serif italic">
        No symbols matched <code className="font-sans not-italic">{data.criteria}</code>{" "}
        as of {data.as_of_date}.
      </div>
    );
  }

  return (
    <>
      <div className="flex items-baseline justify-between mb-3">
        <div className="text-xs text-ink-dim">
          As of <span className="text-ink">{data.as_of_date}</span> ·{" "}
          <span className="text-ink">{data.count}</span> matches
        </div>
      </div>
      <table className="w-full">
        <thead>
          <tr className="border-b border-line-strong">
            <th className="text-left label-tiny py-2">Symbol</th>
            <th className="text-left label-tiny py-2">Name</th>
            <th className="text-right label-tiny py-2">Close</th>
            <th className="text-right label-tiny py-2">Δ%</th>
            <th className="text-right label-tiny py-2">RSI</th>
          </tr>
        </thead>
        <tbody>
          {data.results.map((r) => {
            const change = r.change_pct ?? 0;
            const dir = change > 0 ? "text-bull" : change < 0 ? "text-bear" : "text-ink-muted";
            return (
              <tr key={r.symbol} className="border-b border-line/50">
                <td className="py-2 font-medium text-ink tabular-nums">
                  {r.symbol}
                  {r.market && (
                    <span className="ml-2 text-2xs text-ink-dim">
                      {r.market}
                    </span>
                  )}
                </td>
                <td className="py-2 text-sm text-ink-muted">{r.name ?? "—"}</td>
                <td className="py-2 text-right tabular-nums text-sm">
                  {fmtN(r.close)}
                </td>
                <td className={`py-2 text-right tabular-nums text-sm ${dir}`}>
                  {fmtN(r.change_pct, 2)}%
                </td>
                <td className="py-2 text-right tabular-nums text-sm font-medium text-accent">
                  {fmtN(r.rsi_14, 1)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </>
  );
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

function formatError(e: unknown): string {
  if (e instanceof ApiError) {
    const detail =
      typeof e.detail === "object" && e.detail !== null
        ? JSON.stringify(e.detail)
        : String(e.detail);
    return `${e.status} — ${detail}`;
  }
  return e instanceof Error ? e.message : String(e);
}

function fmtN(n: number | null | undefined, digits = 0): string {
  if (n === null || n === undefined) return "—";
  return n.toLocaleString("en-US", { maximumFractionDigits: digits });
}

function minutesBetween(startISO: string, endISO: string): number {
  const start = new Date(startISO).getTime();
  const end = new Date(endISO).getTime();
  return Math.round((end - start) / 60000);
}
