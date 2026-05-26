import { useCallback, useEffect, useState } from "react";
import { api, type MonitorListItem } from "../lib/api";

export function useMonitorList() {
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

  return { items, loading, error, refresh, add, remove };
}
