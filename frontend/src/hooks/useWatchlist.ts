import { useCallback, useEffect, useState } from "react";
import { api, type WatchlistRow } from "../lib/api";

export function useWatchlist() {
  const [items, setItems] = useState<WatchlistRow[]>([]);

  const refresh = useCallback(async () => {
    try {
      const r = await api.watchlist.list();
      setItems(r.watchlist);
    } catch (e) {
      console.warn("useWatchlist refresh failed:", e);
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

  return { items, add, remove };
}
