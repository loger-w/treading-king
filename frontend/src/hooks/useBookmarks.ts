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

  const refresh = useCallback(async () => {
    try {
      const r = await api.bookmarks.list();
      setGroups(r.groups);
    } catch (e) {
      console.warn("useBookmarks refresh failed:", e);
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

  const reorder = useCallback(async (id: string, sort_order: number) => {
    await api.bookmarks.patch(id, { sort_order });
    await refresh();
  }, [refresh]);

  const remove = useCallback(async (id: string) => {
    await api.bookmarks.delete(id);
    await refresh();
  }, [refresh]);

  return { groups, loading, refresh, create, rename, reorder, remove };
}
