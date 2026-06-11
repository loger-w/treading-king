import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, type MonitorListItem } from "../lib/api";

interface MonitorListContextValue {
  items: MonitorListItem[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  add: (symbol: string) => Promise<void>;
  remove: (symbol: string) => Promise<void>;
}

const MonitorListContext = createContext<MonitorListContextValue | null>(null);

export function MonitorListProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<MonitorListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setError(null);
      const r = await api.monitorList.list();
      setItems(r.items);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
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

  useEffect(() => { refresh(); }, [refresh]);

  // Memo:每 render 新 object 會讓所有 consumer 重 render,在有 setState 寫回的 child
  // useEffect 路徑下會引發無窮 loop(實測 BookmarksPanel.onItemsChanged → setBookmarkSymbolNames
  // spread → Provider re-render → 新 value → BookmarksPanel re-render → ...)。
  const value = useMemo(
    () => ({ items, loading, error, refresh, add, remove }),
    [items, loading, error, refresh, add, remove],
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
