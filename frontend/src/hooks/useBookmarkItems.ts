import { useCallback, useEffect, useMemo, useState } from "react";
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

  // useMemo:不穩定的 Map ref 會讓 BookmarksPanel 的 useEffect([bySymbolFirst]) 每次 render
  // 都跑、setState 寫回去、再觸 render → 無窮 loop。Provider 重構 (useMonitorList → context)
  // 之前不明顯,改 context 後 trigger frequency 放大成 maximum update depth。
  const bySymbolFirst = useMemo(() => {
    const m = new Map<string, { item: BookmarkItem; groupId: string }>();
    for (const g of groups) {
      const items = byGroup.get(g.id) || [];
      for (const it of items) {
        if (!m.has(it.symbol)) {
          m.set(it.symbol, { item: it, groupId: g.id });
        }
      }
    }
    return m;
  }, [groups, byGroup]);

  return { byGroup, bySymbolFirst, loading, refresh };
}
