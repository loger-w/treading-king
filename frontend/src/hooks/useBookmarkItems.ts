import { useCallback, useEffect, useState } from "react";
import { api, type BookmarkGroup, type BookmarkItem } from "../lib/api";

/**
 * 單一書籤的 items。傳 groupId=null 不撈、items=[].
 *
 * 「全部」聚合 view 由呼叫者另外處理(用 `useAllBookmarkItems` 或在 Panel 內合併)。
 */
export function useBookmarkItems(groupId: string | null) {
  const [items, setItems] = useState<BookmarkItem[]>([]);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    if (!groupId) { setItems([]); return; }
    setLoading(true);
    try {
      const r = await api.bookmarks.items(groupId);
      setItems(r.items);
    } catch (e) {
      console.warn("useBookmarkItems refresh failed:", e);
    } finally {
      setLoading(false);
    }
  }, [groupId]);

  useEffect(() => { refresh(); }, [refresh]);

  const addItems = useCallback(async (symbols: string[]) => {
    if (!groupId) return;
    await api.bookmarks.addItems(groupId, symbols);
    await refresh();
  }, [groupId, refresh]);

  const removeItem = useCallback(async (symbol: string) => {
    if (!groupId) return;
    await api.bookmarks.removeItem(groupId, symbol);
    await refresh();
  }, [groupId, refresh]);

  return { items, loading, refresh, addItems, removeItem };
}

/**
 * 「全部」聚合 view — 對所有 user 書籤 + 系統書籤打 N 次 GET items,
 * 合併去重後傳出。同檔多書籤時、歸入第一個出現的書籤(依 groups 順序)。
 *
 * 回傳 `bySymbol`(去重)跟 `byGroup`(每書籤的原始 items、給 「全部」 view section 分組用)。
 */
export function useAllBookmarkItems(groups: BookmarkGroup[]) {
  const [byGroup, setByGroup] = useState<Map<string, BookmarkItem[]>>(new Map());
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    if (groups.length === 0) { setByGroup(new Map()); return; }
    setLoading(true);
    try {
      const results = await Promise.all(
        groups.map((g) =>
          api.bookmarks.items(g.id)
            .then((r): [string, BookmarkItem[]] => [g.id, r.items])
            .catch((): [string, BookmarkItem[]] => [g.id, []]),
        ),
      );
      const m = new Map<string, BookmarkItem[]>();
      for (const [gid, items] of results) m.set(gid, items);
      setByGroup(m);
    } finally {
      setLoading(false);
    }
  }, [groups]);

  useEffect(() => { refresh(); }, [refresh]);

  // 去重後的 flat list — section 分組仍用 byGroup
  const bySymbolFirst = new Map<string, { item: BookmarkItem; groupId: string }>();
  for (const g of groups) {
    const items = byGroup.get(g.id) || [];
    for (const it of items) {
      if (!bySymbolFirst.has(it.symbol)) {
        bySymbolFirst.set(it.symbol, { item: it, groupId: g.id });
      }
    }
  }

  return { byGroup, bySymbolFirst, loading, refresh };
}
