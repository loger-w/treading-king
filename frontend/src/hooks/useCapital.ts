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

// 成交突發/啟動 backlog 重播時,每筆回報各推一次 capital_order:
// trailing debounce 把連發收斂成尾端一次 refetch,也消除並發 GET 舊回應蓋掉新快照的窗口
function debounced(fn: () => void, ms = 200) {
  let t: ReturnType<typeof setTimeout> | undefined;
  const run = () => { clearTimeout(t); t = setTimeout(fn, ms); };
  return Object.assign(run, { cancel: () => clearTimeout(t) });
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
    const onReply = debounced(load);
    const unsub = subscribeCapitalOrders(onReply);   // 回報一來就刷新(去抖)
    return () => { alive = false; onReply.cancel(); unsub(); };
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
    const onReply = debounced(load);
    const unsub = subscribeCapitalOrders(onReply);
    return () => { alive = false; clearInterval(id); onReply.cancel(); unsub(); };
  }, [pollMs]);
  return positions;
}
