import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";

/**
 * 管 (symbol, active_signal_id) → 今日命中次數。
 *
 * Mount 時打 GET /api/signals/today_counts 拿基準（group by 兩 key）。
 * 之後靠 useSignalsStream 的 onSignal 呼叫 bump() 累加。
 *
 * Fail-soft：endpoint 失敗時 fallback 全 0 baseline，UI 不擋。
 */
export type HitCounts = Record<string, Record<string, number>>;
// {[symbol]: {[active_signal_id]: count}}

export function useTodayHits() {
  const [counts, setCounts] = useState<HitCounts>({});

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await api.signalsTodayCounts();
        if (cancelled) return;
        const grouped: HitCounts = {};
        for (const row of r.counts) {
          if (!grouped[row.symbol]) grouped[row.symbol] = {};
          grouped[row.symbol][row.active_signal_id] =
            (grouped[row.symbol][row.active_signal_id] ?? 0) + 1;
        }
        // 與 fetch 在途時 WS bump 進來的計數 merge(同 key 取大):整包取代會把
        // 開盤瞬間的命中蓋掉(後端先 broadcast 後寫 log,snapshot 必然漏掉那筆)
        setCounts((prev) => {
          const merged: HitCounts = { ...grouped };
          for (const [sym, rules] of Object.entries(prev)) {
            merged[sym] = { ...merged[sym] };
            for (const [rid, n] of Object.entries(rules)) {
              merged[sym][rid] = Math.max(merged[sym][rid] ?? 0, n);
            }
          }
          return merged;
        });
      } catch (e) {
        console.warn("useTodayHits baseline failed:", e);
        // fallback: 保持 {} (全 0)
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const bump = useCallback((symbol: string, activeSignalId: string) => {
    setCounts((prev) => {
      const cur = prev[symbol] ?? {};
      return {
        ...prev,
        [symbol]: {
          ...cur,
          [activeSignalId]: (cur[activeSignalId] ?? 0) + 1,
        },
      };
    });
  }, []);

  return { counts, bump };
}
