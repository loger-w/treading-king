import { useEffect, useState } from "react";
import { api, type CapitalOrder, type CapitalPosition } from "../lib/api";
import { subscribeCapitalOrders } from "./useSignalsStream";

export function useCapitalStatus(pollMs = 10000) {
  const [status, setStatus] = useState<string>("disabled");
  const [lastError, setLastError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const r = await api.capitalStatus();
        if (!alive) return;
        setStatus(r.status);
        setLastError(r.last_error ?? null);
      } catch { /* keep */ }
    };
    tick();
    const id = setInterval(tick, pollMs);
    return () => { alive = false; clearInterval(id); };
  }, [pollMs]);
  return { status, lastError };
}

export function useCapitalOrders() {
  const [orders, setOrders] = useState<CapitalOrder[]>([]);
  useEffect(() => {
    let alive = true;
    const load = async () => {
      try { const r = await api.capitalOrders(); if (alive) setOrders(r.orders); }
      catch { /* keep */ }
    };
    load();
    const unsub = subscribeCapitalOrders(load);   // 回報一來就刷新
    return () => { alive = false; unsub(); };
  }, []);
  return orders;
}

export function useCapitalPositions(pollMs = 15000) {
  const [positions, setPositions] = useState<CapitalPosition[]>([]);
  useEffect(() => {
    let alive = true;
    const load = async () => {
      try { const r = await api.capitalPositions(); if (alive) setPositions(r.positions); }
      catch { /* keep */ }
    };
    load();
    const id = setInterval(load, pollMs);
    const unsub = subscribeCapitalOrders(load);
    return () => { alive = false; clearInterval(id); unsub(); };
  }, [pollMs]);
  return positions;
}
