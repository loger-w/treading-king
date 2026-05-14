import { useCallback, useEffect, useState } from "react";
import { api, type ActiveSignal } from "../lib/api";

export function useActiveSignals() {
  const [items, setItems] = useState<ActiveSignal[]>([]);

  const refresh = useCallback(async () => {
    try {
      const r = await api.activeSignals.list();
      setItems(r.active_signals);
    } catch (e) {
      console.warn("useActiveSignals refresh failed:", e);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  return { items, refresh };
}
