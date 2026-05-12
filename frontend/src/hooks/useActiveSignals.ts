import { useCallback, useEffect, useState } from "react";
import { api, type ActiveSignal } from "../lib/api";

export function useActiveSignals() {
  const [items, setItems] = useState<ActiveSignal[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await api.activeSignals.list();
      setItems(r.active_signals);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const create = useCallback(async (payload: Omit<ActiveSignal, "id" | "created_at">) => {
    await api.activeSignals.create(payload);
    await refresh();
  }, [refresh]);

  const update = useCallback(async (id: string, payload: Omit<ActiveSignal, "id" | "created_at">) => {
    await api.activeSignals.update(id, payload);
    await refresh();
  }, [refresh]);

  const remove = useCallback(async (id: string) => {
    await api.activeSignals.delete(id);
    await refresh();
  }, [refresh]);

  return { items, loading, error, refresh, create, update, remove };
}
