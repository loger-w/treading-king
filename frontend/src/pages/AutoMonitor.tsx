import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type AutoMonitorItem } from "../lib/api";
import { useWatchlistQuotes } from "../hooks/useWatchlistQuotes";

const POLL_INTERVAL = 30_000;

export function AutoMonitor({ active = true }: { active?: boolean }) {
  const [items, setItems] = useState<AutoMonitorItem[]>([]);
  const [loading, setLoading] = useState(true);

  const symbols = useMemo(() => items.map((i) => i.symbol), [items]);
  const quotes = useWatchlistQuotes(symbols);

  const refresh = useCallback(async () => {
    try {
      const r = await api.autoMonitor.list();
      setItems(r.items);
    } catch (e) {
      console.warn("auto_monitor refresh failed:", e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    if (!active) return;
    const id = setInterval(refresh, POLL_INTERVAL);
    return () => clearInterval(id);
  }, [active, refresh]);

  if (loading) {
    return (
      <div className="h-full flex items-center justify-center text-ink-muted text-sm">
        載入中…
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div className="h-full flex items-center justify-center text-ink-muted text-sm">
        尚無符合條件的股票（盤中自動篩選）
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col overflow-hidden">
      <header className="px-4 py-3 border-b border-line flex items-center justify-between shrink-0">
        <h2 className="text-sm font-medium text-ink">
          自動監聽
          <span className="ml-2 text-ink-muted font-normal">{items.length} 檔</span>
        </h2>
      </header>

      <div className="overflow-y-auto scroll-editorial flex-1">
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-bg-deep z-10">
            <tr className="text-ink-muted text-xs border-b border-line">
              <th className="text-left px-4 py-2 font-normal w-16">#</th>
              <th className="text-left px-2 py-2 font-normal">代號</th>
              <th className="text-left px-2 py-2 font-normal">名稱</th>
              <th className="text-right px-2 py-2 font-normal">現價</th>
              <th className="text-right px-2 py-2 font-normal">漲跌%</th>
              <th className="text-right px-2 py-2 font-normal">振幅%</th>
              <th className="text-right px-4 py-2 font-normal">成交量(張)</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => {
              const q = quotes[item.symbol];
              const price = q?.price ?? null;
              const pct = q?.changePct ?? item.change_pct;
              const color =
                pct != null && pct > 0
                  ? "text-bull"
                  : pct != null && pct < 0
                    ? "text-bear"
                    : "text-ink-dim";
              return (
                <tr
                  key={item.symbol}
                  className="border-b border-line/50 hover:bg-bg-surface transition-colors"
                >
                  <td className="px-4 py-2 text-ink-muted">{item.rank}</td>
                  <td className="px-2 py-2 font-mono">{item.symbol}</td>
                  <td className="px-2 py-2 text-ink-muted">{item.name ?? "—"}</td>
                  <td className={`px-2 py-2 text-right font-mono ${color}`}>
                    {price != null ? price.toFixed(2) : "—"}
                  </td>
                  <td className={`px-2 py-2 text-right font-mono ${color}`}>
                    {pct != null ? `${pct > 0 ? "+" : ""}${pct.toFixed(2)}%` : "—"}
                  </td>
                  <td className="px-2 py-2 text-right font-mono text-ink-muted">
                    {item.amplitude_pct != null ? `${item.amplitude_pct.toFixed(1)}%` : "—"}
                  </td>
                  <td className="px-2 py-2 text-right font-mono text-ink-muted pr-4">
                    {item.volume_lots != null ? item.volume_lots.toLocaleString() : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
