import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, ApiError, type MonitorListItem } from "../lib/api";

interface MonitorListContextValue {
  items: MonitorListItem[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  add: (symbol: string) => Promise<void>;
  remove: (symbol: string) => Promise<void>;
  reorder: (symbols: string[]) => Promise<void>;
}

const MonitorListContext = createContext<MonitorListContextValue | null>(null);

export function MonitorListProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<MonitorListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const seqRef = useRef(0);

  const refresh = useCallback(async () => {
    const mySeq = ++seqRef.current;
    try {
      setError(null);
      const r = await api.monitorList.list();
      if (mySeq === seqRef.current) setItems(r.items);
    } catch (e) {
      if (mySeq === seqRef.current) setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (mySeq === seqRef.current) setLoading(false);
    }
  }, []);

  // 失敗寫進 error state(呼叫端多半丟棄 promise):無聲失敗的話,
  // 使用者以為已加入監聽但訊號引擎不會對該股觸發
  const add = useCallback(async (symbol: string) => {
    try {
      setError(null);
      await api.monitorList.add(symbol);
      await refresh();
    } catch (e) {
      setError(`加入監聽失敗:${e instanceof Error ? e.message : String(e)}`);
    }
  }, [refresh]);

  const remove = useCallback(async (symbol: string) => {
    try {
      setError(null);
      await api.monitorList.remove(symbol);
      await refresh();
    } catch (e) {
      setError(`移除監聽失敗:${e instanceof Error ? e.message : String(e)}`);
    }
  }, [refresh]);

  const reorder = useCallback(async (symbols: string[]) => {
    seqRef.current++;
    const prev = items;
    const bySymbol = new Map(items.map((it) => [it.symbol, it]));
    setItems(symbols.flatMap((s) => bySymbol.get(s) ?? []));
    try {
      await api.monitorList.reorder(symbols);
    } catch (e) {
      console.warn("reorder monitor failed:", e);
      setItems(prev);
      if (e instanceof ApiError) await refresh();
    }
  }, [items, refresh]);

  useEffect(() => { refresh(); }, [refresh]);

  // Memo:每 render 新 object 會讓所有 consumer 重 render,在有 setState 寫回的 child
  // useEffect 路徑下會引發無窮 loop(實測 BookmarksPanel.onItemsChanged → setBookmarkSymbolNames
  // spread → Provider re-render → 新 value → BookmarksPanel re-render → ...)。
  const value = useMemo(
    () => ({ items, loading, error, refresh, add, remove, reorder }),
    [items, loading, error, refresh, add, remove, reorder],
  );

  return (
    <MonitorListContext.Provider value={value}>
      {children}
    </MonitorListContext.Provider>
  );
}

export function useMonitorList(): MonitorListContextValue {
  const ctx = useContext(MonitorListContext);
  if (!ctx) {
    throw new Error("useMonitorList must be used within <MonitorListProvider>");
  }
  return ctx;
}
