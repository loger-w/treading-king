import { useEffect, useMemo, useRef, useState } from "react";
import { IntradayChart } from "../components/IntradayChart";
import { SignalRulesDialog } from "../components/SignalRulesDialog";
import { SymbolSearch } from "../components/SymbolSearch";
import { TopToolbar } from "../components/TopToolbar";
import { TriggerHistoryTable } from "../components/TriggerHistoryTable";
import { WatchlistWithChips } from "../components/WatchlistWithChips";
import { useActiveSignals } from "../hooks/useActiveSignals";
import { useIntradayCandles } from "../hooks/useIntradayCandles";
import { useSignalsStream } from "../hooks/useSignalsStream";
import { useTodayHits } from "../hooks/useTodayHits";
import { useWatchlist } from "../hooks/useWatchlist";
import { api, type SignalLogRow } from "../lib/api";

/**
 * 即時監控頁 — 整合 watchlist + chart + history + rules dialog。
 *
 * Layout (spec §3 / v11 mockup):
 *   上半 grid-2: 自選 480 + 分時 1fr
 *   下半全寬：觸發歷史 4-col grid
 *
 * 共用 selectedSymbol state — 點自選 row / history row 都驅動 chart 切換。
 */
export function Monitor() {
  const { items: watchlistItems, add, remove } = useWatchlist();
  const { items: rules, refresh: refreshRules } = useActiveSignals();
  const { counts, bump } = useTodayHits();

  const [selected, setSelected] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [historicalToday, setHistoricalToday] = useState<SignalLogRow[]>([]);

  const chartRef = useRef<HTMLDivElement | null>(null);

  // Selected default：第一檔自選股
  useEffect(() => {
    if (!selected && watchlistItems.length > 0) {
      setSelected(watchlistItems[0].symbol);
    }
  }, [watchlistItems, selected]);

  // 拉 today 的 signals_log (給 history table 顯示)
  useEffect(() => {
    const todayStart = new Date();
    todayStart.setHours(0, 0, 0, 0);
    (async () => {
      try {
        const r = await api.signalsHistory({
          since: todayStart.toISOString(),
          limit: 500,
        });
        setHistoricalToday(r.signals);
      } catch (e) {
        console.warn("load history failed:", e);
      }
    })();
  }, []);

  // Intraday chart (selected symbol) — onTick callback 在下面 useSignalsStream 內共用
  const { candles, loading: candlesLoading, onTick } = useIntradayCandles(selected);

  // 單一 WS 連線：onSignal 累加命中、onTick 給 chart
  const { status: wsStatus, recent } = useSignalsStream({
    onSignal: (s) => bump(s.symbol, s.active_signal_id),
    onTick,
  });

  const symbolNames = useMemo(() => {
    const m: Record<string, string | null> = {};
    for (const it of watchlistItems) m[it.symbol] = it.name;
    return m;
  }, [watchlistItems]);

  function handleSelect(sym: string) {
    setSelected(sym);
    // 從 history 點時 scroll 回 chart
    if (chartRef.current) {
      chartRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  async function handleAdd(symbol: string) {
    try { await add(symbol); } catch (e) { console.warn("add failed:", e); }
  }

  return (
    <>
      <TopToolbar
        wsStatus={wsStatus}
        rulesCount={rules.length}
        dialogOpen={dialogOpen}
        onOpenRules={() => setDialogOpen((v) => !v)}
      />

      <main>
        <div className="mx-auto max-w-[1600px] px-[60px] pt-7 pb-12 max-md:px-6">

          {/* 上半 grid: 自選 + 分時 */}
          <div className="grid grid-cols-[480px_1fr] gap-14 max-md:grid-cols-1">
            <section>
              <div className="mb-5">
                <h2 className="font-serif font-bold text-[30px] tracking-[-0.5px] leading-[1.05]">
                  自選清單
                  <span className="font-sans font-normal text-[15px] text-ink-dim ml-2">
                    ({watchlistItems.length})
                  </span>
                </h2>
              </div>
              <div className="mb-5">
                <SymbolSearch onPick={handleAdd} />
              </div>
              <WatchlistWithChips
                items={watchlistItems}
                rules={rules}
                hitCounts={counts}
                selectedSymbol={selected}
                onSelect={setSelected}
                onRemove={remove}
              />
            </section>

            <section ref={chartRef}>
              <div className="mb-5">
                <h2 className="font-serif font-bold text-[30px] tracking-[-0.5px] leading-[1.05]">
                  分時走勢
                </h2>
              </div>
              {!selected ? (
                <div className="h-[460px] flex items-center justify-center border border-line text-ink-dim font-serif italic">
                  ← 點選左邊任一檔股票看分時走勢
                </div>
              ) : (
                <div className="border border-line p-7">
                  <div className="text-xs text-ink-dim mb-2">{selected}</div>
                  <IntradayChart symbol={selected} candles={candles} loading={candlesLoading} />
                </div>
              )}
            </section>
          </div>

          {/* 下半全寬: 觸發歷史 */}
          <section className="mt-14">
            <div className="mb-5">
              <h2 className="font-serif font-bold text-[30px] tracking-[-0.5px] leading-[1.05]">
                觸發歷史
                <span className="font-sans font-normal text-[15px] text-ink-dim ml-2">
                  ({historicalToday.length + recent.length})
                </span>
              </h2>
            </div>
            <TriggerHistoryTable
              historical={historicalToday}
              recent={recent}
              rules={rules}
              symbolNames={symbolNames}
              onSelect={handleSelect}
            />
          </section>

        </div>
      </main>

      <SignalRulesDialog
        open={dialogOpen}
        rules={rules}
        onClose={() => setDialogOpen(false)}
        onChanged={refreshRules}
      />
    </>
  );
}
