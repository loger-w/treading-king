import { type ActiveSignal, type WatchlistRow } from "../lib/api";
import { SignalChip } from "./SignalChip";
import { type HitCounts } from "../hooks/useTodayHits";

/**
 * 自選 list + Scope chip 顯示。
 *
 * 排序：has-hit 置頂（按 total hit desc），無命中按 added_at desc（原順序）。
 * 命中股票左側 3px accent marker（spec §7.3）。
 *
 * 點 row → onSelect(symbol)；點 × → onRemove(symbol)。
 */
interface Props {
  items: WatchlistRow[];
  rules: ActiveSignal[];
  hitCounts: HitCounts;
  selectedSymbol: string | null;
  onSelect: (symbol: string) => void;
  onRemove: (symbol: string) => void;
}

function rulesForSymbol(symbol: string, rules: ActiveSignal[]): ActiveSignal[] {
  return rules.filter((r) => {
    if (!r.enabled) return false;
    if (r.scope.type === "watchlist") return true;
    if (r.scope.type === "symbols") return r.scope.symbols.includes(symbol);
    return false;
  });
}

function totalHitsForSymbol(symbol: string, hitCounts: HitCounts): number {
  const m = hitCounts[symbol] ?? {};
  return Object.values(m).reduce((a, b) => a + b, 0);
}

export function WatchlistWithChips({
  items, rules, hitCounts, selectedSymbol, onSelect, onRemove,
}: Props) {
  // sort: has-hit desc, by total hits desc; rest 維持原順序
  const sorted = [...items].sort((a, b) => {
    const ha = totalHitsForSymbol(a.symbol, hitCounts);
    const hb = totalHitsForSymbol(b.symbol, hitCounts);
    if (ha !== hb) return hb - ha;
    return 0;  // stable: 原順序維持
  });

  if (items.length === 0) {
    return (
      <div className="border border-line p-6 text-center text-ink-dim font-serif italic">
        自選清單還是空的 — 上面搜尋加入第一檔股票
      </div>
    );
  }

  return (
    <ul className="border-t border-line">
      {sorted.map((it) => {
        const symRules = rulesForSymbol(it.symbol, rules);
        const isSel = it.symbol === selectedSymbol;
        const totalHits = totalHitsForSymbol(it.symbol, hitCounts);
        const hasHit = totalHits > 0;

        return (
          <li
            key={it.symbol}
            className={[
              "relative px-3.5 py-4 border-b border-line cursor-pointer transition-colors duration-200",
              isSel ? "bg-bg-card border-l-2 border-l-accent pl-3" : "hover:bg-bg-card/40",
            ].join(" ")}
            onClick={() => onSelect(it.symbol)}
          >
            {/* has-hit marker (覆蓋於 selected 時隱藏 — selected 自己有 left border) */}
            {hasHit && !isSel && (
              <span
                className="absolute left-0 top-4 w-[3px] h-[22px] bg-accent"
                aria-hidden
              />
            )}

            <span className="block text-[19px] font-medium text-ink mb-0.5">{it.symbol}</span>
            <div className="text-[15px] text-ink-muted mb-2.5">{it.name ?? "—"}</div>

            <div className="flex flex-wrap gap-1.5">
              {symRules.map((r) => (
                <SignalChip
                  key={r.id}
                  ruleName={r.name}
                  count={hitCounts[it.symbol]?.[r.id] ?? 0}
                />
              ))}
            </div>

            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); onRemove(it.symbol); }}
              className="absolute right-2.5 top-3 text-base text-ink-dim hover:text-accent px-1"
              aria-label={`移除 ${it.symbol}`}
            >
              ×
            </button>
          </li>
        );
      })}
    </ul>
  );
}
