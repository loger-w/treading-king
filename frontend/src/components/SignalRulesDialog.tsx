import { useEffect, useState } from "react";
import { ActiveSignalEditor } from "./ActiveSignalEditor";
import { api, type ActiveSignal } from "../lib/api";

/**
 * 訊號規則 Dialog — 列表 + 新增/編輯 + toggle 啟用 + 刪除。
 *
 * 內嵌 ActiveSignalEditor 做新增/編輯（nested modal — z-index 由內部 fixed inset-0 處理）。
 * 對應 spec §7.5 / v11 mockup dialog 樣式。
 *
 * 關閉：點 × / 點 backdrop / 按 Esc。
 */
interface Props {
  open: boolean;
  rules: ActiveSignal[];
  onClose: () => void;
  onChanged: () => void;  // 任何 CRUD 操作後通知 parent refresh (useActiveSignals.refresh)
}

function pillsForRule(r: ActiveSignal): string[] {
  const scope = r.scope.type === "watchlist"
    ? "自選全部"
    : `指定 ${r.scope.symbols.length} 檔`;
  const cd = `cd ${r.cooldown_seconds}s`;
  const conditions = (r.filter_json.conditions?.length ?? 0)
    + (r.filter_json.window_conditions?.length ?? 0);
  const logic = `${r.filter_json.logic} · ${conditions} 條件`;
  return [scope, cd, logic];
}

export function SignalRulesDialog({ open, rules, onClose, onChanged }: Props) {
  const [editing, setEditing] = useState<ActiveSignal | null>(null);
  const [creating, setCreating] = useState(false);

  // Esc 關閉
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  async function toggleEnabled(r: ActiveSignal) {
    await api.activeSignals.update(r.id, {
      name: r.name,
      filter_json: r.filter_json,
      scope: r.scope,
      cooldown_seconds: r.cooldown_seconds,
      ignore_auctions: r.ignore_auctions,
      enabled: !r.enabled,
    });
    onChanged();
  }

  async function removeRule(r: ActiveSignal) {
    if (!confirm(`刪除「${r.name}」？`)) return;
    await api.activeSignals.delete(r.id);
    onChanged();
  }

  return (
    <>
      {/* Backdrop */}
      <div
        onClick={onClose}
        className={[
          "fixed inset-0 z-20 bg-bg-deep/85 transition-opacity duration-200",
          open ? "opacity-100 pointer-events-auto" : "opacity-0 pointer-events-none",
        ].join(" ")}
        style={{ backdropFilter: "blur(2px)" }}
      />

      {/* Dialog */}
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="rules-dialog-title"
        className={[
          "fixed top-1/2 left-1/2 z-[21] bg-bg-card border border-line-strong flex flex-col",
          "w-[min(740px,90vw)] max-h-[82vh]",
          "transition-[opacity,transform] duration-200",
          open
            ? "opacity-100 pointer-events-auto -translate-x-1/2 -translate-y-1/2"
            : "opacity-0 pointer-events-none -translate-x-1/2 -translate-y-[calc(50%-20px)]",
        ].join(" ")}
      >
        <div className="flex items-baseline justify-between px-8 pt-7 pb-4 border-b border-line">
          <div>
            <div className="text-[12px] uppercase tracking-[1.8px] text-ink-dim font-medium">設定</div>
            <h3 id="rules-dialog-title" className="font-serif font-bold text-[28px] tracking-[-0.5px] leading-[1.05] mt-1">訊號規則</h3>
            <div className="text-[12px] uppercase tracking-[1.5px] text-ink-dim mt-1">
              {rules.length} 條規則 · {rules.filter(r => r.enabled).length} 啟用中
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="關閉"
            className="text-2xl text-ink-dim hover:text-ink px-2 leading-none cursor-pointer"
          >
            ×
          </button>
        </div>

        <div className="px-8 pt-4 pb-7 overflow-y-auto">
          {rules.length === 0 ? (
            <div className="border border-line p-6 text-center text-ink-dim font-serif italic">
              還沒有訊號規則 — 點下方「+ 新增」設第一條
            </div>
          ) : (
            <div className="border-t border-line">
              {rules.map((r) => (
                <div
                  key={r.id}
                  className="flex items-center justify-between px-2.5 py-4 border-b border-line hover:bg-bg-deep transition-colors duration-200"
                >
                  <div className="flex-1 min-w-0">
                    <div className="text-[18px] font-medium text-ink mb-1">{r.name}</div>
                    <div className="text-[13px] text-ink-dim leading-relaxed">
                      {pillsForRule(r).map((p, i) => (
                        <span
                          key={i}
                          className="inline-block px-2.5 py-px mr-1.5 border border-line-strong text-[11px] text-ink-muted tracking-[0.3px]"
                          style={{ verticalAlign: "1px" }}
                        >
                          {p}
                        </span>
                      ))}
                    </div>
                  </div>
                  <div className="flex items-center gap-4 shrink-0">
                    <button
                      type="button"
                      onClick={() => toggleEnabled(r)}
                      aria-label={r.enabled ? "停用" : "啟用"}
                      className={[
                        "relative w-9 h-5 rounded-full transition-colors duration-200 cursor-pointer border-0 p-0",
                        r.enabled ? "bg-accent/40" : "bg-line-strong",
                      ].join(" ")}
                    >
                      <span
                        className={[
                          "absolute top-0.5 w-4 h-4 rounded-full transition-all duration-200",
                          r.enabled ? "left-[18px] bg-accent" : "left-0.5 bg-ink-muted",
                        ].join(" ")}
                      />
                    </button>
                    <button
                      type="button"
                      onClick={() => setEditing(r)}
                      className="text-[11px] uppercase tracking-[1.2px] text-ink-dim hover:text-ink px-1"
                    >
                      編輯
                    </button>
                    <button
                      type="button"
                      onClick={() => removeRule(r)}
                      className="text-[11px] uppercase tracking-[1.2px] text-ink-dim hover:text-accent px-1"
                    >
                      刪除
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}

          <button
            type="button"
            onClick={() => setCreating(true)}
            className="block w-full text-center mt-4 px-4 py-4 text-[12px] uppercase tracking-[1.5px] text-ink-dim border border-dashed border-line-strong hover:text-accent hover:border-accent transition-colors cursor-pointer bg-transparent"
          >
            + 新增規則
          </button>
        </div>
      </div>

      {/* 內嵌 ActiveSignalEditor (新增/編輯) */}
      {(creating || editing) && (
        <ActiveSignalEditor
          initial={editing ?? undefined}
          onClose={() => { setCreating(false); setEditing(null); }}
          onSaved={() => { onChanged(); setCreating(false); setEditing(null); }}
        />
      )}
    </>
  );
}
