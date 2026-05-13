import { type ActiveSignal, type SignalLogRow, type SignalEvent } from "../lib/api";

/**
 * 全寬 4-col 觸發歷史：時間 / 股票 / 規則 / 觸發資訊。
 *
 * 資料來源：
 *   - `historical`：mount 時 GET /api/signals/history 拿到的歷史 (today)
 *   - `recent`：useSignalsStream 收到的即時 events
 * 兩者合併、按 triggered_at desc，最新的標 fresh。
 *
 * 點 row → onSelect(symbol)，連動 chart + watchlist。
 *
 * 對應 spec §7.4 / v11 mockup history table。
 */
interface Props {
  historical: SignalLogRow[];       // from /api/signals/history
  recent: SignalEvent["data"][];    // from useSignalsStream
  rules: ActiveSignal[];            // 用來 lookup rule name (history.active_signal_id → name)
  symbolNames: Record<string, string | null>;  // symbol → name (from watchlist)
  onSelect: (symbol: string) => void;
}

interface UnifiedRow {
  key: string;
  time: string;          // HH:MM:SS
  date: string;          // YYYY/M/D
  symbol: string;
  name: string | null;
  ruleName: string;
  price: number;
  vol: number;
  isoTime: string;       // for sort
  isFresh: boolean;      // 來自 recent
}

function formatTime(iso: string): { time: string; date: string } {
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return {
    time: `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`,
    date: `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}`,
  };
}

export function TriggerHistoryTable({
  historical, recent, rules, symbolNames, onSelect,
}: Props) {
  const ruleNameById = Object.fromEntries(rules.map((r) => [r.id, r.name]));

  // 合併：recent 在前（已最新→舊），historical 在後（已 desc by triggered_at）
  // recent.active_signal_id 來自 WS event；historical.active_signal_id 來自 DB
  const recentRows: UnifiedRow[] = recent.map((e) => {
    const { time, date } = formatTime(e.triggered_at);
    return {
      key: `recent-${e.active_signal_id}-${e.triggered_at}-${e.symbol}`,
      time, date,
      symbol: e.symbol,
      name: symbolNames[e.symbol] ?? null,
      ruleName: e.active_signal_name ?? ruleNameById[e.active_signal_id] ?? "(unknown)",
      price: e.trigger_price,
      vol: e.trigger_volume,
      isoTime: e.triggered_at,
      isFresh: true,
    };
  });

  const historicalRows: UnifiedRow[] = historical.map((h) => {
    const { time, date } = formatTime(h.triggered_at);
    return {
      key: `hist-${h.id}`,
      time, date,
      symbol: h.symbol,
      name: symbolNames[h.symbol] ?? null,
      ruleName: ruleNameById[h.active_signal_id ?? ""] ?? "(unknown)",
      price: h.trigger_price ?? 0,
      vol: h.trigger_volume ?? 0,
      isoTime: h.triggered_at,
      isFresh: false,
    };
  });

  // Dedup: recent events 可能 server 已寫進 signals_log 然後 /history 又 fetch 到 → 用 (sym, time, rule) dedup
  const seen = new Set<string>();
  const combined: UnifiedRow[] = [];
  for (const r of [...recentRows, ...historicalRows]) {
    const k = `${r.symbol}|${r.ruleName}|${r.isoTime}`;
    if (seen.has(k)) continue;
    seen.add(k);
    combined.push(r);
  }
  combined.sort((a, b) => b.isoTime.localeCompare(a.isoTime));

  return (
    <div className="border-t border-line max-h-[480px] overflow-y-auto">
      {/* sticky header */}
      <div className="sticky top-0 z-[1] grid grid-cols-[120px_200px_1fr_280px] gap-8 px-4 py-2.5 border-b border-line bg-bg text-[10px] uppercase tracking-[2px] text-ink-dim">
        <div>時間</div>
        <div>股票</div>
        <div>規則</div>
        <div className="text-right">觸發資訊</div>
      </div>

      {combined.length === 0 ? (
        <div className="border-b border-line px-4 py-10 text-center text-ink-dim font-serif italic text-[15px]">
          等待第一筆訊號…
        </div>
      ) : (
        combined.map((r) => (
          <div
            key={r.key}
            onClick={() => onSelect(r.symbol)}
            className={[
              "grid grid-cols-[120px_200px_1fr_280px] gap-8 px-4 py-4 border-b border-line cursor-pointer transition-colors duration-200 items-baseline",
              r.isFresh
                ? "bg-accent/[0.04] border-l-2 border-l-accent pl-3.5"
                : "hover:bg-bg-card/40",
            ].join(" ")}
          >
            <div className="text-[14px] text-ink-muted tabular-nums tracking-[0.5px]">
              {r.time}
              <span className="block text-[11px] text-ink-dim tracking-[1px] mt-0.5">{r.date}</span>
            </div>
            <div className="flex flex-col">
              <span className="text-[18px] font-medium text-ink">{r.symbol}</span>
              {r.name && (
                <span className="text-[13px] text-ink-muted mt-0.5">{r.name}</span>
              )}
            </div>
            <div className="font-serif italic font-bold text-[18px] text-accent tracking-[-0.3px]">
              {r.ruleName}
            </div>
            <div className="text-right tabular-nums">
              <span className="block text-[18px] text-ink font-medium tracking-[-0.3px]">
                {r.price.toFixed(2)}
              </span>
              <span className="block text-[12px] text-ink-dim mt-0.5 tracking-[0.5px]">
                vol {r.vol}
              </span>
            </div>
          </div>
        ))
      )}
    </div>
  );
}
