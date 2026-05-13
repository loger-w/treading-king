import { useQuoteBook } from "../hooks/useQuoteBook";

interface Props {
  symbol: string | null;
}

/**
 * 委買賣五檔 — 走 REST poll (useQuoteBook)，2 秒更新一次。
 *
 * Layout：左 5 檔買（紅）、右 5 檔賣（綠），每 row 顯示價+量+量條。
 * 量條 width 用該邊最大量做 normalize（買賣分開算）。
 *
 * 切 symbol / fetch 失敗時：見 useQuoteBook 行為說明。
 */
export function QuoteBook({ symbol }: Props) {
  const { bids, asks, lastSuccessAt, error } = useQuoteBook(symbol);

  if (!symbol) {
    return (
      <div className="border border-line p-6 text-center text-ink-dim font-serif italic">
        ← 從自選 / 搜尋挑一檔看五檔
      </div>
    );
  }

  // 全局 max（買賣盤共用）— 視覺立即顯示哪邊掛單壓倒性
  const maxQty = Math.max(1, ...bids.map((b) => b.size), ...asks.map((a) => a.size));

  const timeStr = lastSuccessAt
    ? lastSuccessAt.toLocaleTimeString("zh-TW", { hour12: false })
    : "—";

  return (
    <div className="border border-line bg-bg-card/50 p-[22px]">
      <div className="flex items-baseline justify-between pb-2.5 mb-3.5 border-b border-line">
        <h3 className="font-serif font-bold text-lg tracking-[-0.3px]">委買賣 五檔</h3>
        <span className="text-2xs uppercase tracking-[1px] text-ink-dim">
          每 2 秒 refresh · {timeStr}
          {error && <span className="ml-2 text-bear">· 更新失敗</span>}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-8">
        <div>
          <h4 className="text-2xs uppercase tracking-[1.5px] text-ink-dim mb-2">委買 BID</h4>
          {bids.length === 0 ? (
            <div className="text-xs text-ink-dim italic py-2">—</div>
          ) : (
            bids.map((b, i) => (
              <div key={i} className="relative grid grid-cols-2 gap-2.5 px-2 py-1.5 border-b border-line text-sm tabular-nums">
                <span
                  className="absolute top-0 bottom-0 right-0 bg-bull/10 pointer-events-none"
                  style={{ width: `${(b.size / maxQty) * 100}%` }}
                />
                <span className="relative z-[1] text-bull font-medium">{b.price.toFixed(2)}</span>
                <span className="relative z-[1] text-right text-ink-muted">{b.size} 張</span>
              </div>
            ))
          )}
        </div>
        <div>
          <h4 className="text-2xs uppercase tracking-[1.5px] text-ink-dim mb-2">委賣 ASK</h4>
          {asks.length === 0 ? (
            <div className="text-xs text-ink-dim italic py-2">—</div>
          ) : (
            asks.map((a, i) => (
              <div key={i} className="relative grid grid-cols-2 gap-2.5 px-2 py-1.5 border-b border-line text-sm tabular-nums">
                <span
                  className="absolute top-0 bottom-0 left-0 bg-bear/10 pointer-events-none"
                  style={{ width: `${(a.size / maxQty) * 100}%` }}
                />
                <span className="relative z-[1] text-bear font-medium">{a.price.toFixed(2)}</span>
                <span className="relative z-[1] text-right text-ink-muted">{a.size} 張</span>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
