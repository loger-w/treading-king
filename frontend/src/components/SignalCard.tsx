import type { SignalEvent } from "../lib/api";

export function SignalCard({ s }: { s: SignalEvent["data"] }) {
  const t = new Date(s.triggered_at);
  const tt = `${String(t.getHours()).padStart(2,"0")}:${String(t.getMinutes()).padStart(2,"0")}:${String(t.getSeconds()).padStart(2,"0")}`;
  return (
    <div className="border-b border-line/50 py-3 px-3 hover:bg-bg-card/40">
      <div className="flex items-baseline justify-between">
        <div className="flex items-baseline gap-3">
          <span className="text-2xs text-ink-dim font-mono tabular-nums">{tt}</span>
          <span className="font-medium text-ink">{s.symbol}</span>
        </div>
        <span className="font-serif italic text-sm text-accent">{s.active_signal_name}</span>
      </div>
      <div className="mt-1 text-sm text-ink-muted tabular-nums">
        {s.trigger_price.toFixed(2)} · vol {s.trigger_volume}
      </div>
    </div>
  );
}
