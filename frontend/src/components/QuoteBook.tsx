import { useQuoteBook } from "../hooks/useQuoteBook";

interface Props {
  symbol: string | null;
}

/**
 * 委買賣五檔 — 走 REST poll (useQuoteBook)，1 秒更新一次。
 *
 * Header：內外盤累積量 + 紅綠比例條(內=綠賣壓、外=紅買盤)。
 * Totals row：委買總量(紅大字、左)/ 委賣總量(綠大字、右)— 五檔加總。
 * Body：左 5 檔買（量→價,鏡像 layout 讓價格集中中央）、右 5 檔賣（價→量）。
 * 量條 width 用該邊最大量做 normalize（買賣共用 maxQty,直接看大小對比）。
 *
 * 切 symbol / fetch 失敗時：見 useQuoteBook 行為說明。
 */
export function QuoteBook({ symbol }: Props) {
  const { bids, asks, innerVolume, outerVolume, error } = useQuoteBook(symbol);

  if (!symbol) {
    return (
      <div className="border border-line p-6 text-center text-ink-dim font-serif italic">
        ← 從自選 / 搜尋挑一檔看五檔
      </div>
    );
  }

  // 全局 max（買賣盤共用）— 視覺立即顯示哪邊掛單壓倒性
  const maxQty = Math.max(1, ...bids.map((b) => b.size), ...asks.map((a) => a.size));

  // 內外盤比例 — 排除中間價成交(那部分既非內也非外)
  const sumIO = innerVolume + outerVolume;
  const innerPct = sumIO > 0 ? (innerVolume / sumIO) * 100 : 0;
  const outerPct = sumIO > 0 ? (outerVolume / sumIO) * 100 : 0;

  // 當下委買 / 委賣 五檔加總(瞬時掛單,跟內外盤的累積不同)
  const bidTotal = bids.reduce((sum, b) => sum + b.size, 0);
  const askTotal = asks.reduce((sum, a) => sum + a.size, 0);

  return (
    <div className="border border-line bg-bg-card/50 p-[22px]">
      <h3 className="font-serif font-bold text-lg tracking-[-0.3px] pb-2.5 mb-3 border-b border-line">
        委買賣 五檔
        {error && <span className="ml-3 text-2xs uppercase tracking-[1px] text-bear">· 更新失敗</span>}
      </h3>

      <div className="mb-4">
        <div className="flex items-baseline justify-between text-2xs uppercase tracking-[1px] mb-1">
          <span className="text-bear font-medium">內 {innerPct.toFixed(0)}%</span>
          <span className="text-bull font-medium">外 {outerPct.toFixed(0)}%</span>
        </div>
        <div className="relative h-1.5 bg-line/30 flex overflow-hidden">
          <div className="bg-bear/70 transition-[width] duration-300" style={{ width: `${innerPct}%` }} />
          <div className="bg-bull/70 transition-[width] duration-300" style={{ width: `${outerPct}%` }} />
        </div>
        <div className="flex items-baseline justify-between text-xs text-ink-muted tabular-nums mt-1">
          <span>{innerVolume.toLocaleString()} 張</span>
          <span>{outerVolume.toLocaleString()} 張</span>
        </div>
      </div>

      <div className="flex items-baseline justify-between mb-4">
        <span className="text-2xl font-bold text-bull tabular-nums tracking-tight">
          {bidTotal.toLocaleString()}
          <span className="ml-1.5 text-sm font-normal text-bull/70">張</span>
        </span>
        <span className="text-2xl font-bold text-bear tabular-nums tracking-tight">
          {askTotal.toLocaleString()}
          <span className="ml-1.5 text-sm font-normal text-bear/70">張</span>
        </span>
      </div>

      <div className="grid grid-cols-2 gap-8">
        <div>
          {bids.length === 0 ? (
            <div className="text-xs text-ink-dim italic py-2">—</div>
          ) : (
            bids.map((b, i) => (
              <div key={i} className="relative grid grid-cols-2 gap-2.5 px-2 py-1.5 border-b border-line text-sm tabular-nums">
                <span
                  className="absolute top-0 bottom-0 right-0 bg-bull/10 pointer-events-none"
                  style={{ width: `${(b.size / maxQty) * 100}%` }}
                />
                <span className="relative z-[1] text-ink-muted">{b.size} 張</span>
                <span className="relative z-[1] text-right text-bull font-medium">{b.price.toFixed(2)}</span>
              </div>
            ))
          )}
        </div>
        <div>
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
