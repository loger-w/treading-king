import { useTradeTape } from "../hooks/useTradeTape";

interface Props {
  symbol: string | null;
}

function formatTime(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

/**
 * 明細 — 最近 50 筆成交 (selected symbol)。
 *
 * 4 欄：時間 / 價 / 向 / 張數。Header sticky，內容滾動。
 */
export function TradeTape({ symbol }: Props) {
  const rows = useTradeTape(symbol);

  if (!symbol) {
    return (
      <div className="border border-line p-6 text-center text-ink-dim font-serif italic">
        ← 從自選 / 搜尋挑一檔看明細
      </div>
    );
  }

  return (
    <div className="border-t border-line">
      <div className="grid grid-cols-[60px_1fr_28px_56px] gap-1.5 px-1 py-2 border-b border-line-strong text-2xs uppercase tracking-[1.2px] text-ink-dim">
        <div>時間</div>
        <div className="text-right">價</div>
        <div className="text-center">向</div>
        <div className="text-right">張</div>
      </div>
      {rows.length === 0 ? (
        <div className="px-4 py-10 text-center text-ink-dim font-serif italic text-sm">
          等待第一筆成交…
        </div>
      ) : (
        rows.map((r, i) => (
          <div
            key={i}
            className="grid grid-cols-[60px_1fr_28px_56px] gap-1.5 px-1 py-1.5 border-b border-line text-xs tabular-nums"
          >
            <span className="text-ink-muted">{formatTime(r.time)}</span>
            <span className={[
              "text-right font-medium",
              r.side === "buy" ? "text-bull" : r.side === "sell" ? "text-bear" : "text-ink",
            ].join(" ")}>
              {r.price.toFixed(2)}
            </span>
            <span className={[
              "text-center text-xs",
              r.side === "buy" ? "text-bull" : r.side === "sell" ? "text-bear" : "text-ink-dim",
            ].join(" ")}>
              {r.side === "buy" ? "外" : r.side === "sell" ? "內" : "—"}
            </span>
            <span className="text-right text-ink-muted">
              {r.size > 0 ? r.size.toLocaleString() : "—"}
            </span>
          </div>
        ))
      )}
    </div>
  );
}
