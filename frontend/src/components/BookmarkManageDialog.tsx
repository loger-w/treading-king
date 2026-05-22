import { useEffect, useState } from "react";
import { type BookmarkGroup } from "../lib/api";

/**
 * 「管理書籤」modal — 改名 / 刪 / 新增。排序留待後續(暫不實作拖拉)。
 */
interface Props {
  groups: BookmarkGroup[];
  onClose: () => void;
  onRename: (id: string, name: string) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
  onCreate: (name: string) => Promise<void>;
}

export function BookmarkManageDialog({ groups, onClose, onRename, onDelete, onCreate }: Props) {
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  function startRename(g: BookmarkGroup) {
    setRenamingId(g.id);
    setRenameValue(g.name);
  }

  async function submitRename() {
    if (!renamingId) return;
    const v = renameValue.trim();
    if (!v) { setRenamingId(null); return; }
    try { await onRename(renamingId, v); } catch (e) { alert(`改名失敗:${(e as Error).message}`); }
    setRenamingId(null);
  }

  async function handleDelete(g: BookmarkGroup) {
    if (!confirm(`刪除「${g.name}」?書籤內 ${g.count} 檔股票會一起消失。`)) return;
    try { await onDelete(g.id); } catch (e) { alert(`刪除失敗:${(e as Error).message}`); }
  }

  async function handleCreate() {
    const v = newName.trim();
    if (!v || creating) return;
    setCreating(true);
    try {
      await onCreate(v);
      setNewName("");
    } catch (e) {
      alert(`新增失敗:${(e as Error).message}`);
    } finally {
      setCreating(false);
    }
  }

  // 排序: 系統書籤先,user 書籤照 sort_order
  const sortedGroups = [...groups].sort((a, b) => {
    if (a.is_system !== b.is_system) return a.is_system ? -1 : 1;
    return a.sort_order - b.sort_order;
  });

  return (
    <>
      <div onClick={onClose}
        className="fixed inset-0 z-20 bg-bg-deep/85"
        style={{ backdropFilter: "blur(2px)" }} />

      <div role="dialog" aria-modal="true"
        className="fixed top-1/2 left-1/2 z-[21] bg-bg-card border border-line-strong p-6
                   w-[min(460px,90vw)] max-h-[82vh] overflow-y-auto scroll-editorial"
        style={{ transform: "translate(-50%, -50%)" }}>
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-serif font-bold text-xl tracking-[-0.4px]">管理書籤</h3>
          <button onClick={onClose} className="text-ink-dim hover:text-accent text-lg" aria-label="關閉">×</button>
        </div>

        <div className="h-px bg-line mb-4" />

        <ul>
          {sortedGroups.map((g) => (
            <li key={g.id} className="flex items-center gap-3 py-3 border-b border-line">
              <span className="flex-1 text-sm">
                {g.is_system && <span className="text-accent">☆ </span>}
                {renamingId === g.id ? (
                  <input
                    type="text"
                    value={renameValue}
                    onChange={(e) => setRenameValue(e.target.value)}
                    onBlur={submitRename}
                    onKeyDown={(e) => { if (e.key === "Enter") submitRename(); }}
                    autoFocus
                    className="bg-bg-deep border border-line text-ink px-2 py-1 text-sm w-32"
                  />
                ) : g.name}
                {g.is_system && (
                  <span className="ml-2 text-2xs uppercase tracking-[1px] text-ink-dim border border-line-strong px-1.5 py-0.5">
                    系統
                  </span>
                )}
              </span>
              <span className="text-xs text-ink-dim tabular-nums">{g.count}</span>
              {!g.is_system && (
                <div className="flex gap-2">
                  <button onClick={() => startRename(g)}
                    className="text-ink-dim hover:text-accent px-1 text-sm" aria-label="改名">
                    ✎
                  </button>
                  <button onClick={() => handleDelete(g)}
                    className="text-ink-dim hover:text-bear px-1 text-sm" aria-label="刪除">
                    🗑
                  </button>
                </div>
              )}
            </li>
          ))}
        </ul>

        <div className="flex gap-2 mt-4">
          <input
            type="text"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") handleCreate(); }}
            placeholder="新增書籤名稱"
            className="flex-1 bg-bg-deep border border-line text-ink px-3 py-2 text-sm outline-none focus:border-accent"
          />
          <button onClick={handleCreate} disabled={!newName.trim() || creating}
            className="px-3 py-2 text-sm bg-accent text-bg font-medium disabled:opacity-40">
            + 新增
          </button>
        </div>

        <p className="text-xs text-ink-dim mt-4 font-serif italic">
          系統書籤無法刪除或改名。
        </p>

        <div className="flex justify-end mt-5">
          <button onClick={onClose}
            className="px-4 py-2 text-sm bg-accent text-bg font-medium">
            完成
          </button>
        </div>
      </div>
    </>
  );
}
