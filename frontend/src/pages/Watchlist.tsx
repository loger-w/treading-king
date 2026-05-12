import { useState } from "react";
import { IntradayChart } from "../components/IntradayChart";
import { SymbolSearch } from "../components/SymbolSearch";
import { useIntradayCandles } from "../hooks/useIntradayCandles";
import { useSignalsStream } from "../hooks/useSignalsStream";
import { useWatchlist } from "../hooks/useWatchlist";

export function Watchlist() {
  const { items, loading, error, add, remove } = useWatchlist();
  const [selected, setSelected] = useState<string | null>(null);
  const { candles, loading: candlesLoading, onTick } = useIntradayCandles(selected);
  useSignalsStream({ onTick });

  return (
    <div className="mx-auto max-w-[1200px] px-12 py-12 max-md:px-6 max-md:py-6 grid grid-cols-[360px_1fr] gap-8 max-md:grid-cols-1">
      <section>
        <div className="label-small text-accent mb-2.5">壹</div>
        <h2 className="h-display text-[28px] mb-4">自選清單</h2>

        <div className="mb-4">
          <SymbolSearch onPick={(s) => add(s).catch(() => {})} />
        </div>

        {error && (
          <div className="border border-accent/40 bg-accent/10 px-3 py-2 mb-3 text-xs text-bear">
            {error}
          </div>
        )}

        {loading && items.length === 0 ? (
          <div className="text-ink-dim text-sm">載入中…</div>
        ) : items.length === 0 ? (
          <div className="border border-line p-6 text-center text-ink-dim font-serif italic">
            自選清單還是空的 — 上面搜尋加入第一檔股票
          </div>
        ) : (
          <ul className="border-t border-line">
            {items.map((it) => {
              const isSel = it.symbol === selected;
              return (
                <li key={it.symbol}
                  className={`relative border-b border-line ${isSel ? "bg-bg-card" : "hover:bg-bg-card/40"}`}>
                  <button type="button"
                    onClick={() => setSelected(it.symbol)}
                    className="w-full text-left px-3 py-2.5 pr-8">
                    <div className="flex items-baseline justify-between">
                      <span className="font-medium text-ink">{it.symbol}</span>
                      <span className="text-2xs text-ink-dim">{it.market}</span>
                    </div>
                    <div className="mt-0.5 text-sm text-ink-muted">{it.name ?? "—"}</div>
                  </button>
                  <button type="button"
                    onClick={() => remove(it.symbol)}
                    className="absolute right-2 top-2 text-ink-dim hover:text-bear text-xs">
                    ×
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <section>
        <div className="label-small text-accent mb-2.5">貳</div>
        <h2 className="h-display text-[28px] mb-4">分時走勢</h2>

        {!selected ? (
          <div className="h-[400px] flex items-center justify-center border border-line text-ink-dim font-serif italic">
            ← 點選左邊任一檔股票看分時走勢
          </div>
        ) : (
          <div className="border border-line p-4">
            <div className="text-xs text-ink-dim mb-2">{selected}</div>
            <IntradayChart symbol={selected} candles={candles} loading={candlesLoading} />
          </div>
        )}
      </section>
    </div>
  );
}
