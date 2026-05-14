import { type ActiveSignal, type SignalLogRow, type SignalEvent } from "../lib/api";

/**
 * 觸發歷史 — 單欄列表（適合 ≤ 300px 寬欄位）。
 *
 * 每 row 兩行：
 *   line1: 股票代號 + name (italic)        時間
 *   line2: 規則名稱                         觸發價
 */
interface Props {
  historical: SignalLogRow[];
  recent: SignalEvent["data"][];
  rules: ActiveSignal[];
  symbolNames: Record<string, string | null>;
  selectedSymbol: string | null;
  onSelect: (symbol: string) => void;
}

interface UnifiedRow {
  key: string;
  time: string;
  symbol: string;
  name: string | null;
  ruleName: string;
  price: number;
  isoTime: string;
  isFresh: boolean;
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

export function TriggerList({
  historical, recent, rules, symbolNames, selectedSymbol, onSelect,
}: Props) {
  const ruleNameById = Object.fromEntries(rules.map((r) => [r.id, r.name]));

  const recentRows: UnifiedRow[] = recent.map((e) => ({
    key: `recent-${e.active_signal_id}-${e.triggered_at}-${e.symbol}`,
    time: formatTime(e.triggered_at),
    symbol: e.symbol,
    name: symbolNames[e.symbol] ?? null,
    ruleName: e.active_signal_name ?? ruleNameById[e.active_signal_id] ?? "(unknown)",
    price: e.trigger_price,
    isoTime: e.triggered_at,
    isFresh: true,
  }));

  const historicalRows: UnifiedRow[] = historical.map((h) => ({
    key: `hist-${h.id}`,
    time: formatTime(h.triggered_at),
    symbol: h.symbol,
    name: symbolNames[h.symbol] ?? null,
    ruleName: ruleNameById[h.active_signal_id ?? ""] ?? "(unknown)",
    price: h.trigger_price ?? 0,
    isoTime: h.triggered_at,
    isFresh: false,
  }));

  const seen = new Set<string>();
  const combined: UnifiedRow[] = [];
  for (const r of [...recentRows, ...historicalRows]) {
    const k = `${r.symbol}|${r.ruleName}|${r.isoTime}`;
    if (seen.has(k)) continue;
    seen.add(k);
    combined.push(r);
  }
  combined.sort((a, b) => b.isoTime.localeCompare(a.isoTime));

  if (combined.length === 0) {
    return (
      <div className="border border-line p-6 text-center text-ink-dim font-serif italic">
        等待第一筆訊號…
      </div>
    );
  }

  return (
    <ul className="border-t border-line">
      {combined.map((r) => {
        const isSel = r.symbol === selectedSymbol;
        return (
          <li
            key={r.key}
            onClick={() => onSelect(r.symbol)}
            className={[
              "px-1 py-3 border-b border-line cursor-pointer transition-colors duration-150",
              isSel ? "bg-bg-card border-l-2 border-l-accent pl-2.5" : "hover:bg-bg-card/40",
              r.isFresh && !isSel ? "bg-accent/[0.04]" : "",
            ].join(" ")}
          >
            <div className="flex items-baseline justify-between gap-2">
              <span className="font-serif font-bold text-base tracking-[-0.2px]">
                {r.symbol}
                {r.name && (
                  <span className="ml-1.5 font-serif italic font-normal text-xs text-ink-muted">
                    {r.name}
                  </span>
                )}
              </span>
              <span className="text-xs text-ink-dim tabular-nums">{r.time}</span>
            </div>
            <div className="flex items-baseline justify-between gap-2 mt-1">
              <span className="text-xs text-ink-dim uppercase tracking-[0.5px]">
                {r.ruleName}
              </span>
              <span className="text-sm tabular-nums text-bull font-medium">
                {r.price.toFixed(2)}
              </span>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
