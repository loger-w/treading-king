import { useEffect, useMemo, useState } from "react";
import { api, type BookmarkGroup, type BookmarkItem, type SymbolSearchRow } from "../lib/api";
import { type WatchlistQuote } from "../hooks/useWatchlistQuotes";
import { MoveCopyDialog } from "./MoveCopyDialog";

/**
 * 編輯模式 — 對單一 user 書籤的批次操作。
 *
 * Header:「+ 加入股票」 toggle inline 搜尋區、「✕ 結束編輯」結束
 * 列表:每 row 加 checkbox
 * Toolbar:已選 N 檔 + 移動 / 複製 / 移除
 *
 * 加入後新 row 套用 1.4s fade-out highlight 動畫。
 */
interface Props {
  group: BookmarkGroup;
  items: BookmarkItem[];
  groups: BookmarkGroup[];
  quotes: Record<string, WatchlistQuote>;
  onExit: () => void;
  onChanged: () => Promise<void>;
}

export function BookmarkEditMode({ group, items, groups, quotes, onExit, onChanged }: Props) {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [addOpen, setAddOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SymbolSearchRow[]>([]);
  const [recentlyAdded, setRecentlyAdded] = useState<Set<string>>(new Set());
  const [moveOp, setMoveOp] = useState<"move" | "copy" | null>(null);

  // 搜尋(debounce 200ms)。cancelled 擋已發出的舊請求:
  // 晚到的舊回應不可蓋掉新結果、也不可在清空輸入後把結果回填
  useEffect(() => {
    if (!addOpen || !query.trim()) { setResults([]); return; }
    let cancelled = false;
    const t = setTimeout(async () => {
      try {
        const r = await api.symbols(query.trim(), 10);
        if (!cancelled) setResults(r.results);
      } catch { /* ignore */ }
    }, 200);
    return () => { cancelled = true; clearTimeout(t); };
  }, [query, addOpen]);

  const existingSymbols = useMemo(() => new Set(items.map((it) => it.symbol)), [items]);

  function toggle(symbol: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(symbol)) next.delete(symbol); else next.add(symbol);
      return next;
    });
  }

  async function addStock(symbol: string) {
    if (existingSymbols.has(symbol)) return;
    try {
      await api.bookmarks.addItems(group.id, [symbol]);
      setQuery("");
      setResults([]);
      setRecentlyAdded((p) => new Set(p).add(symbol));
      // 1.4s 後從 recent 移除
      setTimeout(() => {
        setRecentlyAdded((p) => {
          const n = new Set(p); n.delete(symbol); return n;
        });
      }, 1400);
      await onChanged();
    } catch (e) {
      alert(`加入失敗:${(e as Error).message}`);
    }
  }

  async function removeSelected() {
    if (selected.size === 0) return;
    if (!confirm(`從「${group.name}」移除 ${selected.size} 檔?`)) return;
    // 用 move 端點實作「批次移除」會 ws unsubscribe 對的 owner — 但這裡是純刪。
    // allSettled + 必 refresh:部分失敗時已刪成功的也要從畫面消失,
    // 失敗的留著勾選讓使用者重試
    const targets = [...selected];
    const results = await Promise.allSettled(targets.map((s) => api.bookmarks.removeItem(group.id, s)));
    const failed = targets.filter((_, i) => results[i].status === "rejected");
    setSelected(new Set(failed));
    await onChanged();
    if (failed.length > 0) alert(`移除失敗 ${failed.length} 檔:${failed.join(", ")}`);
  }

  function openMove(op: "move" | "copy") {
    if (selected.size === 0) return;
    setMoveOp(op);
  }

  return (
    <>
      {/* Header */}
      <div className="px-3.5 py-3 border-b border-line flex items-baseline justify-between">
        <div className="flex items-baseline gap-2">
          <span className="font-serif font-bold text-lg tracking-[-0.3px]">{group.name}</span>
          <span className="text-xs text-ink-dim">({items.length})</span>
        </div>
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => setAddOpen((v) => !v)}
            className={[
              "text-xs text-accent border-b transition-colors",
              addOpen ? "border-accent" : "border-transparent hover:border-accent",
            ].join(" ")}
          >
            + 加入股票
          </button>
          <button
            type="button"
            onClick={onExit}
            className="text-xs text-ink-dim hover:text-accent"
          >
            ✕ 結束編輯
          </button>
        </div>
      </div>

      {/* Inline 搜尋區 */}
      {addOpen && (
        <div className="bg-bg-card px-3.5 py-3 border-b border-line">
          <div className="flex justify-between mb-1.5 text-2xs uppercase tracking-[1.5px] text-ink-dim">
            <span>搜尋並加入</span>
            <span className="font-serif italic text-xs lowercase tracking-normal">可連續加多支</span>
          </div>
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="代號或名稱..."
            autoFocus
            className="w-full bg-bg-deep border border-line text-ink px-3 py-2 text-sm outline-none focus:border-accent"
          />
          {results.length > 0 && (
            <div className="mt-2 max-h-48 overflow-y-auto scroll-editorial">
              {results.map((r) => {
                const isIn = existingSymbols.has(r.symbol);
                return (
                  <button
                    key={r.symbol}
                    type="button"
                    disabled={isIn}
                    onClick={() => addStock(r.symbol)}
                    className={[
                      "w-full text-left px-2 py-2 flex justify-between items-center gap-2 border-l-2 transition-colors",
                      isIn
                        ? "opacity-50 cursor-not-allowed border-transparent"
                        : "hover:bg-bg-deep border-transparent hover:border-accent",
                    ].join(" ")}
                  >
                    <span className="flex gap-2 items-baseline min-w-0">
                      <span className="text-sm font-medium text-ink shrink-0">{r.symbol}</span>
                      <span className="text-xs text-ink-muted truncate">{r.name || "(無名稱)"}</span>
                    </span>
                    <span className="flex gap-1.5 items-center shrink-0">
                      <span className="text-2xs text-ink-dim border border-line-strong px-1">{r.market}</span>
                      <span className={isIn ? "text-bear text-sm" : "text-accent text-sm font-bold"}>
                        {isIn ? "✓" : "+"}
                      </span>
                    </span>
                  </button>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* 列表 (每 row 有 checkbox) */}
      <ul>
        {items.map((it) => {
          const isChecked = selected.has(it.symbol);
          const isJustAdded = recentlyAdded.has(it.symbol);
          const q = quotes[it.symbol];
          const pct = q?.changePct ?? null;
          const priceCls = pct == null ? "text-ink-dim"
            : pct > 0 ? "text-bull"
            : pct < 0 ? "text-bear" : "text-ink-dim";

          return (
            <li
              key={it.symbol}
              onClick={() => toggle(it.symbol)}
              className={[
                "relative pl-10 pr-3.5 py-3 border-b border-line cursor-pointer transition-colors",
                isChecked ? "bg-bg-card" : "hover:bg-bg-card/40",
                isJustAdded ? "animate-bm-highlight" : "",
              ].join(" ")}
            >
              <span className={[
                "absolute left-3.5 top-4 w-3.5 h-3.5 border flex items-center justify-center text-bg text-2xs font-bold",
                isChecked ? "bg-accent border-accent" : "border-line-strong",
              ].join(" ")}>
                {isChecked && "✓"}
              </span>
              <div className="flex items-baseline gap-2">
                <span className="text-[17px] font-medium text-ink shrink-0">{it.symbol}</span>
                <span className="text-sm text-ink-muted truncate flex-1">{it.name ?? "(無名稱)"}</span>
                <span className={`text-sm tabular-nums shrink-0 ${priceCls}`}>
                  {q?.price != null ? q.price.toFixed(2) : "—"}
                  {pct != null && (
                    <span className="ml-1 text-xs">
                      {pct > 0 ? "+" : ""}{pct.toFixed(2)}%
                    </span>
                  )}
                </span>
              </div>
            </li>
          );
        })}
      </ul>

      {/* Toolbar */}
      <div className="sticky bottom-0 bg-bg-card border-t border-line-strong px-3.5 py-2.5 flex items-center gap-2">
        <span className="text-xs text-ink-muted flex-1">已選 {selected.size} 檔</span>
        <button
          type="button"
          disabled={selected.size === 0}
          onClick={() => openMove("move")}
          className="px-3 py-1.5 text-xs border border-line-strong text-ink hover:border-accent hover:text-accent disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:border-line-strong disabled:hover:text-ink"
        >
          移動
        </button>
        <button
          type="button"
          disabled={selected.size === 0}
          onClick={() => openMove("copy")}
          className="px-3 py-1.5 text-xs border border-line-strong text-ink hover:border-accent hover:text-accent disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:border-line-strong disabled:hover:text-ink"
        >
          複製
        </button>
        <button
          type="button"
          disabled={selected.size === 0}
          onClick={removeSelected}
          className="px-3 py-1.5 text-xs border border-line-strong text-ink hover:border-bear hover:text-bear disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:border-line-strong disabled:hover:text-ink"
        >
          移除
        </button>
      </div>

      {moveOp && (
        <MoveCopyDialog
          op={moveOp}
          symbols={[...selected]}
          fromGroupId={group.id}
          groups={groups}
          onClose={() => setMoveOp(null)}
          onConfirm={async () => {
            setSelected(new Set());
            await onChanged();
          }}
        />
      )}
    </>
  );
}
