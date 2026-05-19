import { useQuoteBook } from "../hooks/useQuoteBook";

interface Props {
  symbol: string | null;
}

/**
 * 委買賣五檔 — 走 REST poll (useQuoteBook)，1 秒更新一次。
 *
 * Totals row：委買總量(紅大字、左)/ 委賣總量(綠大字、右)— 五檔加總。
 * Body：左 5 檔買、右 5 檔賣，量條 width 用兩邊共用 maxQty 做 normalize。
 * 鎖漲跌停時 price=0 的對手檔顯示「市價」，header 出現對應 badge。
 */
export function QuoteBook({ symbol }: Props) {
  const { bids, asks, isLimitUp, isLimitDown, error } = useQuoteBook(symbol);

  if (!symbol) {
    return (
      <div className="border border-line p-6 text-center text-ink-dim font-serif italic">
        ← 從自選 / 搜尋挑一檔看五檔
      </div>
    );
  }

  const maxQty = Math.max(1, ...bids.map((b) => b.size), ...asks.map((a) => a.size));
  const bidTotal = bids.reduce((sum, b) => sum + b.size, 0);
  const askTotal = asks.reduce((sum, a) => sum + a.size, 0);

  return (
    <div className="border border-line bg-bg-card/50 p-[22px]">
      <h3 className="font-serif font-bold text-lg tracking-[-0.3px] pb-2.5 mb-3 border-b border-line flex items-center gap-3">
        <span>委買賣 五檔</span>
        {isLimitUp && (
          <span className="px-2 py-0.5 text-2xs uppercase tracking-[1.5px] text-bull border border-bull/40">
            鎖漲停
          </span>
        )}
        {isLimitDown && (
          <span className="px-2 py-0.5 text-2xs uppercase tracking-[1.5px] text-bear border border-bear/40">
            鎖跌停
          </span>
        )}
        {error && <span className="ml-auto text-2xs uppercase tracking-[1px] text-bear">· 更新失敗</span>}
      </h3>

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
                <span className="relative z-[1] text-ink-muted">{b.size > 0 ? `${b.size} 張` : "—"}</span>
                <span className="relative z-[1] text-right text-bull font-medium">
                  {b.price === 0 ? "市價" : b.price.toFixed(2)}
                </span>
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
                <span className="relative z-[1] text-bear font-medium">
                  {a.price === 0 ? "市價" : a.price.toFixed(2)}
                </span>
                <span className="relative z-[1] text-right text-ink-muted">{a.size > 0 ? `${a.size} 張` : "—"}</span>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
