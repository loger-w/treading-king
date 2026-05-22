import { useEffect, useState } from "react";
import { api, type SymbolSearchRow } from "../lib/api";

interface Props {
  onPick: (symbol: string, name: string | null) => void;
  placeholder?: string;
}

export function SymbolSearch({ onPick, placeholder = "搜尋代號或名稱..." }: Props) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<SymbolSearchRow[]>([]);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!q.trim()) { setResults([]); return; }
    const t = setTimeout(async () => {
      try {
        const r = await api.symbols(q.trim(), 10);
        setResults(r.results);
      } catch { /* ignore */ }
    }, 200);
    return () => clearTimeout(t);
  }, [q]);

  return (
    <div className="relative">
      <input
        type="text"
        value={q}
        onChange={(e) => { setQ(e.target.value); setOpen(true); }}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        onFocus={() => setOpen(true)}
        placeholder={placeholder}
        className="w-full bg-bg-deep border border-line text-ink px-3 py-2 outline-none focus:border-accent text-sm"
      />
      {open && results.length > 0 && (
        <div className="absolute z-10 left-0 right-0 mt-1 max-h-64 overflow-y-auto bg-bg-card border border-line">
          {results.map((r) => (
            <button
              key={r.symbol}
              type="button"
              onClick={() => { onPick(r.symbol, r.name || null); setQ(""); setResults([]); setOpen(false); }}
              className="w-full text-left px-3 py-2 hover:bg-bg-deep flex items-baseline justify-between"
            >
              <span className="text-sm">
                <span className="font-medium text-ink">{r.symbol}</span>
                <span className="ml-2 text-ink-muted">{r.name || "(無名稱)"}</span>
              </span>
              <span className="text-2xs text-ink-dim">{r.market}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
