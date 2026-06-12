import { useCallback, useEffect, useState } from "react";
import { api, type BookmarkGroup } from "../lib/api";

/**
 * Bookmark groups (含系統書籤) 列表 + CRUD。
 *
 * 不負責拿單一書籤的股票 — 那是 `useBookmarkItems`。
 */
export function useBookmarks() {
  const [groups, setGroups] = useState<BookmarkGroup[]>([]);
  const [loading, setLoading] = useState(true);
  // 失敗要可見:只 console.warn 的話,後端沒起來時面板顯示「都還是空的」,
  // 使用者會以為資料遺失而不是連線失敗
  const [error, setError] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const r = await api.bookmarks.list();
      setGroups(r.groups);
      setError(false);
    } catch (e) {
      console.warn("useBookmarks refresh failed:", e);
      setError(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const create = useCallback(async (name: string) => {
    await api.bookmarks.create(name);
    await refresh();
  }, [refresh]);

  const rename = useCallback(async (id: string, name: string) => {
    await api.bookmarks.patch(id, { name });
    await refresh();
  }, [refresh]);

  const remove = useCallback(async (id: string) => {
    await api.bookmarks.delete(id);
    await refresh();
  }, [refresh]);

  // 樂觀重排 user 群組(系統書籤固定殿後,不在 ids 內);失敗回滾。
  // sort_order 同步改寫成 0..n-1(對齊後端寫入值)— ManageDialog 依它排序,
  // 只重排陣列會讓 dialog 顯示拖前舊順序
  const reorderGroups = useCallback(async (ids: string[]) => {
    const prev = groups;
    const byId = new Map(groups.map((g) => [g.id, g]));
    const system = groups.filter((g) => g.is_system);
    setGroups([
      ...ids.flatMap((i, idx) => {
        const g = byId.get(i);
        return g ? [{ ...g, sort_order: idx }] : [];
      }),
      ...system,
    ]);
    try {
      await api.bookmarks.reorderGroups(ids);
    } catch (e) {
      console.warn("reorder groups failed:", e);
      setGroups(prev);
      await refresh();
    }
  }, [groups, refresh]);

  return { groups, loading, error, refresh, create, rename, remove, reorderGroups };
}
