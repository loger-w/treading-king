import { useEffect, useMemo, useRef, useState } from "react";
import { IntradayChart } from "../components/IntradayChart";
import { QuoteBook } from "../components/QuoteBook";
import { SignalRulesDialog } from "../components/SignalRulesDialog";
import { TopToolbar } from "../components/TopToolbar";
import { TradeTape } from "../components/TradeTape";
import { TriggerList } from "../components/TriggerList";
import { WatchlistWithChips } from "../components/WatchlistWithChips";
import { useActiveSignals } from "../hooks/useActiveSignals";
import { useIntradayCandles } from "../hooks/useIntradayCandles";
import { usePreviewSubscribe } from "../hooks/usePreviewSubscribe";
import { useSignalsStream } from "../hooks/useSignalsStream";
import { useTodayHits } from "../hooks/useTodayHits";
import { useWatchlist } from "../hooks/useWatchlist";
import { useWatchlistQuotes } from "../hooks/useWatchlistQuotes";
import { api, type SignalLogRow } from "../lib/api";

/**
 * 即時監控頁 — grid-4 等高 layout。
 *
 * Layout:
 *   觸發歷史 300 | 自選 340 | 分時走勢+五檔 1fr | 明細 300
 *   max-w 1960、等高 (中央列驅動，其他 3 欄 stretch + scroll-panel flex:1)
 *
 * 搜尋流程：toolbar 搜尋 → setSelected 預覽（不再直接 add 自選）；
 *           IntradayChart header 提供「+ 加入自選 / 已在自選 ✓」按鈕。
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

  // 拉 today 的 signals_log (給 history list 顯示)
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
  const { candles, prevClose, onTick } = useIntradayCandles(selected);

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

  const inWatchlist = useMemo(
    () => selected !== null && watchlistItems.some((w) => w.symbol === selected),
    [watchlistItems, selected]
  );

  const watchlistSymbols = useMemo(
    () => watchlistItems.map((w) => w.symbol),
    [watchlistItems]
  );

  const watchlistQuotes = useWatchlistQuotes(watchlistSymbols);

  // 預覽訂閱：selected 不在 watchlist 時通知 backend 用 owner='preview' 訂閱該 symbol
  usePreviewSubscribe(selected, watchlistSymbols);

  function handleSelect(sym: string) {
    setSelected(sym);
    // 從 history 點時 scroll 回 chart
    if (chartRef.current) {
      chartRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  function handleSearchPick(sym: string) {
    // 搜尋現在「先預覽」：只 setSelected，不 add。
    setSelected(sym);
    if (chartRef.current) {
      chartRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  async function handleAddCurrent() {
    if (!selected) return;
    try { await add(selected); } catch (e) { console.warn("add failed:", e); }
  }

  return (
    <>
      <TopToolbar
        wsStatus={wsStatus}
        rulesCount={rules.length}
        dialogOpen={dialogOpen}
        onOpenRules={() => setDialogOpen((v) => !v)}
        onPickSymbol={handleSearchPick}
      />

      <main className="h-screen flex flex-col overflow-hidden">
        <div className="mx-auto w-full max-w-[1960px] px-9 pt-3 pb-12 max-md:px-6 flex-1 min-h-0">
          <div
            className="grid items-stretch gap-6 max-[1200px]:grid-cols-1 h-full"
            style={{ gridTemplateColumns: "300px 340px 1fr 300px" }}
          >

            {/* COL 1: 觸發歷史 */}
            <section className="flex flex-col min-w-0 min-h-0">
              <div className="flex items-baseline gap-2.5 mb-4 flex-shrink-0">
                <h2 className="font-serif font-bold text-2xl tracking-[-0.5px] leading-[1.05]">
                  觸發歷史
                </h2>
                <span className="font-sans font-normal text-sm text-ink-dim">
                  ({historicalToday.length + recent.length})
                </span>
              </div>
              <div className="flex-1 min-h-0 overflow-y-auto pr-1.5">
                <TriggerList
                  historical={historicalToday}
                  recent={recent}
                  rules={rules}
                  symbolNames={symbolNames}
                  selectedSymbol={selected}
                  onSelect={handleSelect}
                />
              </div>
            </section>

            {/* COL 2: 自選清單 */}
            <section className="flex flex-col min-w-0 min-h-0">
              <div className="flex items-baseline gap-2.5 mb-4 flex-shrink-0">
                <h2 className="font-serif font-bold text-2xl tracking-[-0.5px] leading-[1.05]">
                  自選清單
                </h2>
                <span className="font-sans font-normal text-sm text-ink-dim">
                  ({watchlistItems.length})
                </span>
              </div>
              <div className="flex-1 min-h-0 overflow-y-auto pr-1.5">
                <WatchlistWithChips
                  items={watchlistItems}
                  rules={rules}
                  hitCounts={counts}
                  quotes={watchlistQuotes}
                  selectedSymbol={selected}
                  onSelect={setSelected}
                  onRemove={remove}
                />
              </div>
            </section>

            {/* COL 3: 分時走勢 + 五檔 */}
            <section ref={chartRef} className="flex flex-col min-w-0 gap-6">
              <div className="flex-shrink-0">
                <div className="flex items-baseline gap-2.5 mb-4">
                  <h2 className="font-serif font-bold text-2xl tracking-[-0.5px] leading-[1.05]">
                    分時走勢
                  </h2>
                </div>
                {!selected ? (
                  <div className="h-[460px] flex items-center justify-center border border-line text-ink-dim font-serif italic">
                    ← 從觸發歷史 / 自選 / 上方搜尋挑一檔
                  </div>
                ) : (
                  <div className="border border-line p-6">
                    <IntradayChart
                      symbol={selected}
                      name={symbolNames[selected] ?? null}
                      candles={candles}
                      prevClose={prevClose}
                      inWatchlist={inWatchlist}
                      onAddToWatchlist={handleAddCurrent}
                    />
                  </div>
                )}
              </div>
              <QuoteBook symbol={selected} />
            </section>

            {/* COL 4: 明細 */}
            <section className="flex flex-col min-w-0 min-h-0">
              <div className="flex items-baseline gap-2.5 mb-4 flex-shrink-0">
                <h2 className="font-serif font-bold text-2xl tracking-[-0.5px] leading-[1.05]">
                  明細
                </h2>
              </div>
              <div className="flex-1 min-h-0 overflow-y-auto pr-1.5">
                <TradeTape symbol={selected} />
              </div>
            </section>

          </div>
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
