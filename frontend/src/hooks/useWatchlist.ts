import { useCallback, useEffect, useState } from "react";
import { api, type WatchlistRow } from "../lib/api";

export function useWatchlist() {
  const [items, setItems] = useState<WatchlistRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await api.watchlist.list();
      setItems(r.watchlist);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const add = useCallback(async (symbol: string) => {
    await api.watchlist.add(symbol);
    await refresh();
  }, [refresh]);

  const remove = useCallback(async (symbol: string) => {
    await api.watchlist.remove(symbol);
    await refresh();
  }, [refresh]);

  return { items, loading, error, refresh, add, remove };
}
