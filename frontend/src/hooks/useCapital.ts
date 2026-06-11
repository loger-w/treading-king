import { useEffect, useState } from "react";
import { api, type CapitalOrder, type CapitalPosition } from "../lib/api";
import { subscribeCapitalOrders } from "./useSignalsStream";

export function useCapitalStatus(pollMs = 10000) {
  const [status, setStatus] = useState<string>("disabled");
  const [lastError, setLastError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    let failStreak = 0;
    const tick = async () => {
      try {
        const r = await api.capitalStatus();
        if (!alive) return;
        failStreak = 0;
        setStatus(r.status);
        setLastError(r.last_error ?? null);
      } catch {
        // 單次暫態失敗容忍(保留舊值);連續失敗=後端不可達,
        // 健康燈不能停在舊的「已連線」讓送單鈕維持可按
        if (!alive) return;
        failStreak += 1;
        if (failStreak >= 2) {
          setStatus("unreachable");
          setLastError("後端無回應(status 輪詢連續失敗)");
        }
      }
    };
    tick();
    const id = setInterval(tick, pollMs);
    return () => { alive = false; clearInterval(id); };
  }, [pollMs]);
  return { status, lastError };
}

// 成交突發/啟動 backlog 重播時,每筆回報各推一次 capital_order:
// trailing debounce 把連發收斂成尾端一次 refetch。並發在途的舊回應
// (mount load / interval / bus 三入口)由 load 內的遞增序號擋下
function debounced(fn: () => void, ms = 200) {
  let t: ReturnType<typeof setTimeout> | undefined;
  const run = () => { clearTimeout(t); t = setTimeout(fn, ms); };
  return Object.assign(run, { cancel: () => clearTimeout(t) });
}

export function useCapitalOrders(pollMs = 30000) {
  const [orders, setOrders] = useState<CapitalOrder[]>([]);
  useEffect(() => {
    let alive = true;
    let seq = 0;   // 只採最新一次請求:慢的舊回應不可蓋掉新快照(剛刪的單會短暫復活)
    const load = async () => {
      const mySeq = ++seq;
      try { const r = await api.capitalOrders(); if (alive && mySeq === seq) setOrders(r.orders); }
      catch { /* keep */ }
    };
    load();
    const id = setInterval(load, pollMs);   // WS 斷線(含 half-open)期間的備援刷新
    const onReply = debounced(load);
    const unsub = subscribeCapitalOrders(onReply);   // 回報一來就刷新(去抖)
    return () => { alive = false; clearInterval(id); onReply.cancel(); unsub(); };
  }, [pollMs]);
  return orders;
}

export function useCapitalPositions(pollMs = 15000) {
  const [positions, setPositions] = useState<CapitalPosition[]>([]);
  useEffect(() => {
    let alive = true;
    let seq = 0;
    const load = async () => {
      const mySeq = ++seq;
      try { const r = await api.capitalPositions(); if (alive && mySeq === seq) setPositions(r.positions); }
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
