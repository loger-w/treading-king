import { useState } from "react";
import { ActiveSignalEditor } from "../components/ActiveSignalEditor";
import { SignalCard } from "../components/SignalCard";
import { type ActiveSignal } from "../lib/api";
import { useActiveSignals } from "../hooks/useActiveSignals";
import { useSignalsStream } from "../hooks/useSignalsStream";

export function Signals() {
  const { items: actives, refresh, remove } = useActiveSignals();
  const { status, recent } = useSignalsStream({
    onSignal: () => {
      // 未來：if user toggle 開聲音 → new Audio('/notify.mp3').play()
    },
  });
  const [editing, setEditing] = useState<ActiveSignal | null>(null);
  const [creating, setCreating] = useState(false);

  return (
    <div className="mx-auto max-w-[1200px] px-12 py-12 max-md:px-6 max-md:py-6 space-y-10">
      {/* 已啟用規則 */}
      <section>
        <div className="label-small text-accent mb-2.5">壹</div>
        <div className="flex items-baseline justify-between mb-4">
          <h2 className="h-display text-[28px]">即時訊號規則 ({actives.length})</h2>
          <div className="flex gap-2">
            <button type="button"
              onClick={() => setCreating(true)}
              className="border-2 border-accent text-accent px-4 py-1.5 text-xs uppercase tracking-[2px] hover:bg-accent/10">
              + 新增
            </button>
          </div>
        </div>

        {actives.length === 0 ? (
          <div className="border border-line p-6 text-center text-ink-dim font-serif italic">
            還沒有訊號規則 — 點上方「+ 新增」設第一條
          </div>
        ) : (
          <ul className="border-t border-line">
            {actives.map((a) => (
              <li key={a.id} className="border-b border-line py-3 px-3 flex items-baseline justify-between hover:bg-bg-card/40">
                <div>
                  <div className="text-base font-medium text-ink">{a.name}</div>
                  <div className="mt-0.5 text-xs text-ink-dim">
                    {a.scope.type === "watchlist" ? "自選清單全部" : `指定 ${a.scope.symbols.length} 檔`}
                    · cooldown {a.cooldown_seconds}s
                    · {a.enabled ? <span className="text-bull">啟用中</span> : <span className="text-ink-dim">停用</span>}
                  </div>
                </div>
                <div className="flex gap-2">
                  <button type="button" onClick={() => setEditing(a)}
                    className="text-xs text-ink-dim hover:text-ink">編輯</button>
                  <button type="button" onClick={() => { if (confirm(`刪除「${a.name}」？`)) remove(a.id); }}
                    className="text-xs text-ink-dim hover:text-bear">刪除</button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* 即時訊號流 */}
      <section>
        <div className="label-small text-accent mb-2.5">貳</div>
        <div className="flex items-baseline justify-between mb-4">
          <h2 className="h-display text-[28px]">即時訊號流</h2>
          <div className="text-xs text-ink-dim">
            {status === "open" ? <span className="text-bull">● 連線中</span>
              : status === "connecting" ? <span className="text-accent">● 連線中…</span>
              : <span className="text-bear">● 已斷線</span>}
            <span className="ml-3">最近 {recent.length} 筆</span>
          </div>
        </div>

        {recent.length === 0 ? (
          <div className="border border-line p-6 text-center text-ink-dim font-serif italic">
            等待第一筆訊號…
          </div>
        ) : (
          <div className="border-t border-line">
            {recent.map((s, i) => <SignalCard key={`${s.active_signal_id}-${s.triggered_at}-${i}`} s={s} />)}
          </div>
        )}
      </section>

      {(creating || editing) && (
        <ActiveSignalEditor
          initial={editing ?? undefined}
          onClose={() => { setCreating(false); setEditing(null); }}
          onSaved={() => refresh()}
        />
      )}
    </div>
  );
}
