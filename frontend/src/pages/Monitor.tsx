import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AddToBookmarksDialog } from "../components/AddToBookmarksDialog";
import { BookmarksPanel } from "../components/BookmarksPanel";
import { IntradayChart } from "../components/IntradayChart";
import { QuoteBook } from "../components/QuoteBook";
import { SignalRulesDialog } from "../components/SignalRulesDialog";
import { TopToolbar } from "../components/TopToolbar";
import { TradingPanel } from "../components/TradingPanel";
import { TriggerList } from "../components/TriggerList";
import { useActiveSignals } from "../hooks/useActiveSignals";
import { MonitorListProvider, useMonitorList } from "../hooks/useMonitorList";
import { usePreviewSubscribe } from "../hooks/usePreviewSubscribe";
import { useSignalsStream } from "../hooks/useSignalsStream";
import { useTodayHits } from "../hooks/useTodayHits";
import { useSnapshotCache } from "../hooks/useSnapshotCache";
import { useSymbolNames } from "../hooks/useSymbolNames";
import { api, type SignalLogRow } from "../lib/api";
import { buildSymbolNames } from "../lib/symbol-name";

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
export function Monitor({ active = true }: { active?: boolean }) {
  return (
    <MonitorListProvider>
      <MonitorInner active={active} />
    </MonitorListProvider>
  );
}

function MonitorInner({ active }: { active: boolean }) {
  const { items: rules, refresh: refreshRules } = useActiveSignals();
  const { counts, bump } = useTodayHits();

  const [selected, setSelected] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [historicalToday, setHistoricalToday] = useState<SignalLogRow[]>([]);
  const [bookmarkSymbols, setBookmarkSymbols] = useState<string[]>([]);  // 所有書籤的 union(去重)
  const [bookmarkSymbolNames, setBookmarkSymbolNames] = useState<Record<string, string | null>>({});
  const [addToBookmarksOpen, setAddToBookmarksOpen] = useState(false);
  const [bookmarksRefreshKey, setBookmarksRefreshKey] = useState(0);

  const chartRef = useRef<HTMLDivElement | null>(null);

  // Selected default:第一檔書籤股
  useEffect(() => {
    if (!selected && bookmarkSymbols.length > 0) {
      setSelected(bookmarkSymbols[0]);
    }
  }, [bookmarkSymbols, selected]);

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

  // 單一 WS 連線：onSignal 累加命中。tick 走 module bus(subscribeTicks),
  // 分時圖的 candles state 養在 IntradayChart 內,不再經頁根穿針引線
  const { status: wsStatus, recent } = useSignalsStream({
    onSignal: (s) => bump(s.symbol, s.active_signal_id),
  });

  const inAnyBookmark = useMemo(
    () => selected !== null && bookmarkSymbols.includes(selected),
    [bookmarkSymbols, selected]
  );

  const { items: monitorItems, add: addToMonitor, remove: removeFromMonitor } = useMonitorList();

  const inMonitor = useMemo(
    () => selected !== null && monitorItems.some((m) => m.symbol === selected),
    [monitorItems, selected]
  );
  const triggerSymbols = useMemo(() => {
    const set = new Set<string>();
    for (const h of historicalToday) set.add(h.symbol);
    for (const r of recent) set.add(r.symbol);
    return Array.from(set);
  }, [historicalToday, recent]);
  const triggerSnapshot = useSnapshotCache(triggerSymbols);
  const prevCloseMap = useMemo(() => {
    const m: Record<string, number | null> = {};
    for (const s of triggerSymbols) m[s] = triggerSnapshot[s]?.prevClose ?? null;
    return m;
  }, [triggerSymbols, triggerSnapshot]);

  // 預覽訂閱:selected 不在任何書籤時通知 backend 用 owner='preview' 訂閱該 symbol
  usePreviewSubscribe(selected, bookmarkSymbols);

  // 訊號可對非自選股觸發 — 這些 symbol 不在 bookmarkSymbolNames,TriggerList 只剩裸代號。
  // 補查這些未知 symbol 的名稱(已知的不重打)。
  const unknownTriggerSymbols = useMemo(
    () => triggerSymbols.filter((s) => !(s in bookmarkSymbolNames)),
    [triggerSymbols, bookmarkSymbolNames]
  );
  const resolvedNames = useSymbolNames(unknownTriggerSymbols);

  // chart header / 觸發列表的 symbol → name:書籤/搜尋 + 監聽 + 觸發解析三來源合併。
  // 監聽來源不可漏 — 純監聽股不在書籤、當日未觸發時名稱會變「—」。
  const symbolNames = useMemo(
    () => buildSymbolNames(resolvedNames, monitorItems, bookmarkSymbolNames),
    [resolvedNames, monitorItems, bookmarkSymbolNames]
  );

  // useCallback:TriggerList 有 memo,不穩定的 onSelect 會打穿它
  const handleSelect = useCallback((sym: string) => {
    setSelected(sym);
    // 從 history 點時 scroll 回 chart
    if (chartRef.current) {
      chartRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, []);

  function handleSearchPick(sym: string, name: string | null) {
    // 搜尋現在「先預覽」:只 setSelected,不 add。
    setSelected(sym);
    // 把搜尋帶來的 name 寫進 cache,讓 chart header 即使該股不在書籤也有名稱
    setBookmarkSymbolNames((prev) => ({ ...prev, [sym]: name }));
    if (chartRef.current) {
      chartRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  function handleOpenBookmarkDialog() {
    if (!selected) return;
    setAddToBookmarksOpen(true);
  }

  const handleBookmarkItemsChanged = useCallback((symbols: string[], names: Record<string, string | null>) => {
    setBookmarkSymbols(symbols);
    // Merge:書籤的 names 蓋上,但保留之前 search preview 寫進的 entries
    setBookmarkSymbolNames((prev) => ({ ...prev, ...names }));
  }, []);

  return (
    <div className="h-screen flex flex-col overflow-hidden">
      <div className="shrink-0">
        <TopToolbar
          wsStatus={wsStatus}
          rulesCount={rules.length}
          dialogOpen={dialogOpen}
          onOpenRules={() => setDialogOpen((v) => !v)}
          onPickSymbol={handleSearchPick}
        />
      </div>

      <main className="flex-1 min-h-0 flex flex-col overflow-hidden">
        <div className="mx-auto w-full max-w-[1960px] px-9 pt-3 pb-6 max-md:px-6 flex-1 min-h-0">
          <div
            className="grid items-stretch gap-6 max-[1200px]:grid-cols-1 h-full"
            style={{ gridTemplateColumns: "300px 460px 1fr 380px" }}
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
              <div className="flex-1 min-h-0 overflow-y-auto pr-1.5 scroll-editorial">
                <TriggerList
                  historical={historicalToday}
                  recent={recent}
                  rules={rules}
                  symbolNames={symbolNames}
                  prevCloseMap={prevCloseMap}
                  selectedSymbol={selected}
                  onSelect={handleSelect}
                />
              </div>
            </section>

            {/* COL 2: 書籤 */}
            <section className="flex flex-col min-w-0 min-h-0">
              <BookmarksPanel
                key={bookmarksRefreshKey}
                rules={rules}
                hitCounts={counts}
                selectedSymbol={selected}
                onSelectSymbol={setSelected}
                onItemsChanged={handleBookmarkItemsChanged}
              />
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
                      active={active}
                      inAnyBookmark={inAnyBookmark}
                      onOpenBookmarkDialog={handleOpenBookmarkDialog}
                      inMonitor={inMonitor}
                      onAddToMonitor={() => selected && addToMonitor(selected)}
                      onRemoveFromMonitor={() => selected && removeFromMonitor(selected)}
                    />
                  </div>
                )}
              </div>
              {/* 頁面隱藏時暫停 1Hz 五檔輪詢(直打富邦);下單面板的輪詢刻意不動,
                  切頁不可重置使用者下到一半的單 */}
              <QuoteBook symbol={active ? selected : null} />
            </section>

            {/* COL 4: 下單面板(群益) */}
            <TradingPanel selected={selected} onPick={setSelected} />

          </div>
        </div>
      </main>

      <SignalRulesDialog
        open={dialogOpen}
        rules={rules}
        onClose={() => setDialogOpen(false)}
        onChanged={refreshRules}
      />

      {addToBookmarksOpen && selected && (
        <AddToBookmarksDialog
          symbol={selected}
          onClose={() => setAddToBookmarksOpen(false)}
          onChanged={async () => {
            // 重新渲染 BookmarksPanel(讓它重新拉 groups + items)
            setBookmarksRefreshKey((k) => k + 1);
            // refreshMonitor 已不需要 — MonitorListProvider 共用同一份 state,
            // add/remove 操作完成後所有消費者(Monitor、BookmarksPanel、AddToBookmarksDialog)都會看到最新資料
          }}
        />
      )}
    </div>
  );
}
