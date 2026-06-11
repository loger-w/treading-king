import { useQuoteBook } from "../hooks/useQuoteBook";
import { emitOrderTicket } from "../hooks/useSignalsStream";

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
        <BookSide side="bid" symbol={symbol} levels={bids} maxQty={maxQty} />
        <BookSide side="ask" symbol={symbol} levels={asks} maxQty={maxQty} />
      </div>
    </div>
  );
}

// 買賣兩欄共用一份:點價 guard(鎖停的市價檔 price=0 不可帶價下單)、
// 市價/缺量顯示規則只寫一次——點價直通下單匣,兩側規則不可漂移
function BookSide({ side, symbol, levels, maxQty }: {
  side: "bid" | "ask";
  symbol: string;
  levels: Array<{ price: number; size: number }>;
  maxQty: number;
}) {
  if (levels.length === 0) return <div><div className="text-xs text-ink-dim italic py-2">—</div></div>;
  const isBid = side === "bid";
  return (
    <div>
      {levels.map((l, i) => {
        const sizeText = l.size > 0 ? `${l.size} 張` : "—";
        const priceText = l.price === 0 ? "市價" : l.price.toFixed(2);
        return (
          <div
            key={i}
            onClick={() => l.price > 0 && emitOrderTicket({ symbol, price: l.price })}
            className="relative grid grid-cols-2 gap-2.5 px-2 py-1.5 border-b border-line text-sm tabular-nums cursor-pointer hover:bg-bg-card/40"
          >
            <span
              className={`absolute top-0 bottom-0 pointer-events-none ${isBid ? "right-0 bg-bull/10" : "left-0 bg-bear/10"}`}
              style={{ width: `${(l.size / maxQty) * 100}%` }}
            />
            {isBid ? (
              <>
                <span className="relative z-[1] text-ink-muted">{sizeText}</span>
                <span className="relative z-[1] text-right text-bull font-medium">{priceText}</span>
              </>
            ) : (
              <>
                <span className="relative z-[1] text-bear font-medium">{priceText}</span>
                <span className="relative z-[1] text-right text-ink-muted">{sizeText}</span>
              </>
            )}
          </div>
        );
      })}
    </div>
  );
}
