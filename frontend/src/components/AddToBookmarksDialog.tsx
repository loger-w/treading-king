import { useEffect, useState } from "react";
import { api, type BookmarkGroup } from "../lib/api";
import { useMonitorList } from "../hooks/useMonitorList";
import { ModalShell } from "./ModalShell";

/**
 * 「加股票到書籤」modal — 從 IntradayChart header「+ 加入書籤」開。
 *
 * 顯示 user 書籤(checkbox),預設勾起該股票已在的書籤;系統書籤 disabled。
 * 儲存:對「新勾的」call addItems、對「取消勾的」call removeItem。
 */
interface Props {
  symbol: string;
  onClose: () => void;
  onChanged: () => Promise<void> | void;
}

export function AddToBookmarksDialog({ symbol, onClose, onChanged }: Props) {
  const { add: addToMonitor } = useMonitorList();
  const [alsoMonitor, setAlsoMonitor] = useState(false);
  const [groups, setGroups] = useState<BookmarkGroup[]>([]);
  const [initial, setInitial] = useState<Set<string>>(new Set());  // 進入時已勾
  const [selected, setSelected] = useState<Set<string>>(new Set());  // 目前勾選
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [newName, setNewName] = useState("");

  // Load groups + 每個 group 看 symbol 是否在內。
  // list() 失敗必須有 catch:否則是 unhandled rejection + 安靜顯示「空書籤」,
  // 使用者會誤以為自己沒有任何書籤;items() 失敗會讓已收藏顯示未勾,擋儲存
  useEffect(() => {
    (async () => {
      try {
        const r = await api.bookmarks.list();
        const userGroups = r.groups.filter((g) => !g.is_system);
        setGroups(r.groups);   // render 自行分組,這裡不用重排

        const containing = new Set<string>();
        let itemsFailed = false;
        await Promise.all(userGroups.map(async (g) => {
          try {
            const items = await api.bookmarks.items(g.id);
            if (items.items.some((it) => it.symbol === symbol)) {
              containing.add(g.id);
            }
          } catch (e) {
            itemsFailed = true;
            console.warn("AddToBookmarksDialog items load failed:", g.id, e);
          }
        }));
        if (itemsFailed) { setLoadError(true); return; }
        setInitial(containing);
        setSelected(new Set(containing));
      } catch (e) {
        console.warn("AddToBookmarksDialog load failed:", e);
        setLoadError(true);
      } finally {
        setLoading(false);
      }
    })();
  }, [symbol]);

  function toggle(gid: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(gid)) next.delete(gid); else next.add(gid);
      return next;
    });
  }

  async function submit() {
    setSubmitting(true);
    try {
      const toAdd = [...selected].filter((g) => !initial.has(g));
      const toRemove = [...initial].filter((g) => !selected.has(g));
      await Promise.all([
        ...toAdd.map((gid) => api.bookmarks.addItems(gid, [symbol])),
        ...toRemove.map((gid) => api.bookmarks.removeItem(gid, symbol)),
      ]);
      if (alsoMonitor) {
        try {
          await addToMonitor(symbol);
        } catch (e) {
          console.warn("add to monitor failed:", e);
        }
      }
      await onChanged();
      onClose();
    } catch (e) {
      alert(`儲存失敗:${(e as Error).message}`);
    } finally {
      setSubmitting(false);
    }
  }

  async function handleCreate() {
    const v = newName.trim();
    if (!v) return;
    try {
      const g = await api.bookmarks.create(v);
      setGroups((prev) => [...prev, g]);
      setSelected((prev) => new Set(prev).add(g.id));
      setNewName("");
      // server 上群組已存在,立刻通知 parent 刷新——否則按「取消」關閉後
      // sidebar 看不到新書籤(再建同名還會撞 409),像「新增失蹤」
      await onChanged();
    } catch (e) {
      alert(`新增失敗:${(e as Error).message}`);
    }
  }

  const userGroups = groups.filter((g) => !g.is_system);
  const systemGroups = groups.filter((g) => g.is_system);

  return (
    <ModalShell onClose={onClose} width="420px" scroll>
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-serif font-bold text-xl tracking-[-0.4px]">
            加入 <span className="text-accent">{symbol}</span> 到書籤
          </h3>
          <button onClick={onClose} className="text-ink-dim hover:text-accent text-lg" aria-label="關閉">×</button>
        </div>

        <div className="h-px bg-line mb-4" />

        <label className="label-tiny mb-3 block">選擇書籤(可複選)</label>

        {loading ? (
          <div className="text-sm text-ink-dim font-serif italic py-4 text-center">載入中…</div>
        ) : loadError ? (
          <div className="text-sm text-bear py-4 text-center">書籤載入失敗 — 勾選狀態不可靠,請關閉重試。</div>
        ) : (
          <>
            <ul>
              {userGroups.map((g) => (
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

            {systemGroups.length > 0 && (
              <>
                <div className="mt-4 mb-2 text-xs text-ink-dim flex items-center gap-2">
                  <span>系統書籤</span>
                  <span className="flex-1 h-px bg-line" />
                </div>
                <ul>
                  {systemGroups.map((g) => (
                    <li key={g.id} className="flex items-center gap-3 py-2.5 opacity-40">
                      <span className="w-4 h-4 border border-line-strong" />
                      <span className="flex-1 text-sm">
                        <span className="text-accent">☆ </span>{g.name}
                      </span>
                      <span className="text-xs text-ink-dim">{g.count} · 不可手動加</span>
                    </li>
                  ))}
                </ul>
              </>
            )}

            <div className="flex gap-2 mt-4">
              <input
                type="text"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") handleCreate(); }}
                placeholder="新增書籤..."
                className="flex-1 bg-bg-deep border border-line text-ink px-3 py-2 text-sm outline-none focus:border-accent"
              />
              <button onClick={handleCreate} disabled={!newName.trim()}
                className="px-3 py-2 text-xs border border-line-strong text-ink-muted hover:border-accent hover:text-accent disabled:opacity-40">
                + 新增
              </button>
            </div>
          </>
        )}

        <label className="text-sm flex items-center gap-2 cursor-pointer text-ink-muted py-3 border-t border-line">
          <input
            type="checkbox"
            checked={alsoMonitor}
            onChange={(e) => setAlsoMonitor(e.target.checked)}
            className="accent-accent"
          />
          同時加入監聽清單(訊號評估)
        </label>

        <div className="flex justify-end gap-2 mt-5">
          <button onClick={onClose}
            className="px-4 py-2 text-sm border border-line-strong text-ink-muted hover:border-ink-muted hover:text-ink">
            取消
          </button>
          <button onClick={submit} disabled={loading || submitting || loadError}
            className="px-4 py-2 text-sm bg-accent text-bg font-medium disabled:opacity-40">
            {submitting ? "儲存中…" : "儲存"}
          </button>
        </div>
    </ModalShell>
  );
}
