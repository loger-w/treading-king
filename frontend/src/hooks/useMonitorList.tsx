import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
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

  const add = useCallback(async (symbol: string) => {
    await api.monitorList.add(symbol);
    await refresh();
  }, [refresh]);

  const remove = useCallback(async (symbol: string) => {
    await api.monitorList.remove(symbol);
    await refresh();
  }, [refresh]);

  useEffect(() => { refresh(); }, [refresh]);

  return (
    <MonitorListContext.Provider value={{ items, loading, error, refresh, add, remove }}>
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
