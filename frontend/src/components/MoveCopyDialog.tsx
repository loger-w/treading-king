import { useState } from "react";
import { api, type BookmarkGroup } from "../lib/api";
import { ModalShell } from "./ModalShell";

/**
 * 「移動 / 複製」modal — 編輯模式 toolbar 內呼叫。
 *
 * 顯示 user 書籤(checkbox、不含 from group)讓 user 選目的書籤。
 * 系統書籤不能作為目的地、所以不顯示。
 */
interface Props {
  op: "move" | "copy";
  symbols: string[];
  fromGroupId: string;
  groups: BookmarkGroup[];
  onClose: () => void;
  onConfirm: () => Promise<void>;
}

export function MoveCopyDialog({ op, symbols, fromGroupId, groups, onClose, onConfirm }: Props) {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [submitting, setSubmitting] = useState(false);

  function toggle(gid: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(gid)) next.delete(gid); else next.add(gid);
      return next;
    });
  }

  async function submit() {
    if (selected.size === 0 || submitting) return;
    setSubmitting(true);
    try {
      await api.bookmarks.move({
        symbols, from_group_id: fromGroupId, to_group_ids: [...selected], op,
      });
      await onConfirm();
      onClose();
    } catch (e) {
      alert(`${op === "move" ? "移動" : "複製"}失敗:${(e as Error).message}`);
    } finally {
      setSubmitting(false);
    }
  }

  // 排除系統書籤跟 from group
  const candidates = groups.filter((g) => !g.is_system && g.id !== fromGroupId);
  const title = op === "move" ? "移動到..." : "複製到...";

  return (
    <ModalShell onClose={onClose} width="380px">
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-serif font-bold text-xl tracking-[-0.4px]">{title}</h3>
          <button onClick={onClose} className="text-ink-dim hover:text-accent text-lg" aria-label="關閉">×</button>
        </div>

        <div className="h-px bg-line mb-4" />

        <p className="text-xs text-ink-dim font-serif italic mb-3">
          已選 {symbols.length} 檔股票
        </p>

        {candidates.length === 0 ? (
          <div className="text-sm text-ink-dim font-serif italic py-4 text-center">
            沒有其他書籤可以{op === "move" ? "移動" : "複製"} — 先新增一個
          </div>
        ) : (
          <ul>
            {candidates.map((g) => (
              <li key={g.id} className="flex items-center gap-3 py-2.5 border-b border-line cursor-pointer hover:bg-bg-deep/40"
                onClick={() => toggle(g.id)}>
                <span className={[
                  "w-4 h-4 border flex items-center justify-center text-bg text-xs font-bold",
                  selected.has(g.id) ? "bg-accent border-accent" : "border-line-strong",
                ].join(" ")}>
                  {selected.has(g.id) && "✓"}
                </span>
                <span className="flex-1 text-sm">{g.name}</span>
                <span className="text-xs text-ink-dim tabular-nums">{g.count}</span>
              </li>
            ))}
          </ul>
        )}

        <div className="flex justify-end gap-2 mt-5">
          <button onClick={onClose}
            className="px-4 py-2 text-sm border border-line-strong text-ink-muted hover:border-ink-muted hover:text-ink">
            取消
          </button>
          <button onClick={submit} disabled={selected.size === 0 || submitting}
            className="px-4 py-2 text-sm bg-accent text-bg font-medium disabled:opacity-40">
            {submitting ? "處理中…" : (op === "move" ? "移動" : "複製")}
          </button>
        </div>
    </ModalShell>
  );
}
