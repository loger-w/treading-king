import { memo, useMemo } from "react";
import { type ActiveSignal, type SignalLogRow, type SignalEvent, type TouchMeta } from "../lib/api";
import { formatTouch, extractTouch } from "../lib/signal-format";

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
  prevCloseMap: Record<string, number | null>;
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
  cdpTouch?: TouchMeta;
  maTouch?: TouchMeta;
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

// memo + useMemo:選中股每筆 tick 都會 re-render 父層,期間本元件 props 參考
// 全部不變——最多 550 列的重算(每列 new Date)與 reconcile 不該每 tick 跑一次
export const TriggerList = memo(function TriggerList({
  historical, recent, rules, symbolNames, prevCloseMap, selectedSymbol, onSelect,
}: Props) {
  const combined = useMemo(() => {
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
      cdpTouch: e.cdp_touch,
      maTouch: e.ma_touch,
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
      cdpTouch: extractTouch(h.context_json, "cdp_touch"),
      maTouch: extractTouch(h.context_json, "ma_touch"),
    }));

    const seen = new Set<string>();
    const out: UnifiedRow[] = [];
    for (const r of [...recentRows, ...historicalRows]) {
      const k = `${r.symbol}|${r.ruleName}|${r.isoTime}`;
      if (seen.has(k)) continue;
      seen.add(k);
      out.push(r);
    }
    out.sort((a, b) => b.isoTime.localeCompare(a.isoTime));
    return out;
  }, [historical, recent, rules, symbolNames]);

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
              <span className="font-serif font-bold text-lg tracking-[-0.2px]">
                {r.symbol}
                {r.name && (
                  <span className="ml-1.5 font-serif font-normal text-sm text-ink-muted">
                    {r.name}
                  </span>
                )}
              </span>
              <span className="text-sm text-ink-dim tabular-nums">{r.time}</span>
            </div>
            <div className="flex items-baseline justify-between gap-2 mt-1">
              <span className="text-sm text-ink-dim uppercase tracking-[0.5px]">
                {r.ruleName}
              </span>
              {(() => {
                const prev = prevCloseMap[r.symbol];
                const pct = (prev != null && prev !== 0)
                  ? ((r.price - prev) / prev) * 100
                  : null;
                const cls = pct == null
                  ? "text-ink-muted"
                  : pct > 0 ? "text-bull"
                  : pct < 0 ? "text-bear"
                  : "text-ink-muted";
                return (
                  <span className="flex items-baseline gap-2">
                    {pct != null && (
                      <span className={`text-xs tabular-nums ${cls}`}>
                        {pct > 0 ? "+" : ""}{pct.toFixed(2)}%
                      </span>
                    )}
                    <span className={`text-base tabular-nums font-medium ${cls}`}>
                      {r.price.toFixed(2)}
                    </span>
                  </span>
                );
              })()}
            </div>
            {(r.cdpTouch || r.maTouch) && (
              <div className="text-2xs text-ink-dim mt-1.5 tabular-nums">
                {r.cdpTouch && (
                  <span className="mr-3">{formatTouch(r.cdpTouch)}</span>
                )}
                {r.maTouch && (
                  <span>{formatTouch(r.maTouch)}</span>
                )}
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
});
