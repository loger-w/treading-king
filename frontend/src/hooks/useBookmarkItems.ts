import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, type BookmarkGroup, type BookmarkItem } from "../lib/api";

/**
 * 單一書籤的 items。傳 groupId=null 不撈、items=[].
 *
 * 「全部」聚合 view 由呼叫者另外處理(用 `useAllBookmarkItems` 或在 Panel 內合併)。
 */
export function useBookmarkItems(groupId: string | null) {
  const [items, setItems] = useState<BookmarkItem[]>([]);
  const [loading, setLoading] = useState(false);
  // 只採最新一次請求:快速切換 group 時,慢的舊回應不可把 A 的 items 掛在 B 名下
  const seqRef = useRef(0);

  const refresh = useCallback(async () => {
    const mySeq = ++seqRef.current;
    if (!groupId) { setItems([]); return; }
    setLoading(true);
    try {
      const r = await api.bookmarks.items(groupId);
      if (mySeq === seqRef.current) setItems(r.items);
    } catch (e) {
      console.warn("useBookmarkItems refresh failed:", e);
      // 失敗不殘留前一個 group 的列表
      if (mySeq === seqRef.current) setItems([]);
    } finally {
      if (mySeq === seqRef.current) setLoading(false);
    }
  }, [groupId]);

  // 切換 group 先清空,舊 group 的列表不掛在新 group 標題下
  useEffect(() => { setItems([]); refresh(); }, [refresh]);

  const removeItem = useCallback(async (symbol: string) => {
    if (!groupId) return;
    await api.bookmarks.removeItem(groupId, symbol);
    await refresh();
  }, [groupId, refresh]);

  // 樂觀重排:先照新順序重組本地 items、再打 API;失敗回滾(列表彈回即為提示)。
  const reorder = useCallback(async (symbols: string[]) => {
    if (!groupId) return;
    seqRef.current++;  // 作廢在途的 refresh 回應 — 不讓拖前舊順序蓋掉樂觀結果
    const prev = items;
    const bySym = new Map(items.map((it) => [it.symbol, it]));
    setItems(symbols.flatMap((s) => bySym.get(s) ?? []));
    try {
      await api.bookmarks.reorderItems(groupId, symbols);
    } catch (e) {
      console.warn("reorder items failed:", e);
      setItems(prev);
      // 只有伺服器有回應(如 400 集合過期)才重抓現況;網路斷線時 refresh
      // 也會失敗、其 catch 會把列表清成 [] — 反而毀掉上面的回滾
      if (e instanceof ApiError) await refresh();
    }
  }, [groupId, items, refresh]);

  return { items, loading, refresh, removeItem, reorder };
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
  const seqRef = useRef(0);   // 同 useBookmarkItems:只採最新一次請求
  const groupsRef = useRef(groups);
  groupsRef.current = groups;

  // refresh 依 group id 集合而非 groups 陣列 identity:useBookmarks.refresh
  // 每次都 setGroups 新陣列,直接依賴會讓每次 mutation 全量重抓兩次
  // (顯式 refreshAll 一次 + identity 變化觸發 effect 又一次);
  // items 變動的同步點是呼叫端的顯式 refreshAll
  // 排序後 join = 集合 identity:群組「重排」只變順序不變內容,不該觸發全量重抓
  const groupIdsKey = groups.map((g) => g.id).sort().join(",");
  const refresh = useCallback(async () => {
    const gs = groupsRef.current;
    const mySeq = ++seqRef.current;
    if (gs.length === 0) { setByGroup(new Map()); return; }
    setLoading(true);
    try {
      const results = await Promise.all(
        gs.map((g) =>
          api.bookmarks.items(g.id)
            .then((r): [string, BookmarkItem[]] => [g.id, r.items])
            .catch((): [string, BookmarkItem[]] => [g.id, []]),
        ),
      );
      if (mySeq !== seqRef.current) return;
      const m = new Map<string, BookmarkItem[]>();
      for (const [gid, items] of results) m.set(gid, items);
      setByGroup(m);
    } finally {
      if (mySeq === seqRef.current) setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [groupIdsKey]);

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
