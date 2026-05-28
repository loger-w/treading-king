import { useEffect, useState } from "react";
import { api } from "../lib/api";

// Module-level cache — 跨 hook instance 共用,name 不變,命中後永不重打
const cache = new Map<string, string | null>();
const inFlight = new Map<string, Promise<void>>();

// 同時最多打幾條 /api/symbols — 避免 trigger symbol 一多就 N 條並發塞爆後端 thread pool
const MAX_CONCURRENCY = 5;

async function lookupOne(sym: string): Promise<void> {
  try {
    const r = await api.symbols(sym, 5);
    const hit = r.results.find((row) => row.symbol === sym);
    cache.set(sym, hit?.name ?? null);
  } catch {
    // 失敗不寫 cache → symbols 變動時可重試
  }
}

/**
 * 對給定 symbols 解析「代號 → 名稱」。
 *
 * 用途:訊號可對非自選股觸發,那些 symbol 不在 BookmarksPanel 回報的 name map,
 * TriggerList 只剩裸代號。這裡用既有 /api/symbols(前綴查 + 取精確 match)補名稱。
 *
 * - module-level cache + in-flight dedup(同 symbol 多 hook 只打一次)
 * - 限並發 5,避免 trigger symbol 多時壓垮後端
 */
export function useSymbolNames(symbols: string[]): Record<string, string | null> {
  const [version, setVersion] = useState(0);
  const symbolsKey = symbols.join(",");

  useEffect(() => {
    const missing = symbols.filter((s) => !cache.has(s) && !inFlight.has(s));
    if (missing.length === 0) return;

    const promise = (async () => {
      try {
        for (let i = 0; i < missing.length; i += MAX_CONCURRENCY) {
          const batch = missing.slice(i, i + MAX_CONCURRENCY);
          await Promise.all(batch.map(lookupOne));
        }
      } finally {
        for (const s of missing) inFlight.delete(s);
        setVersion((v) => v + 1);
      }
    })();

    for (const s of missing) inFlight.set(s, promise);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbolsKey]);

  void version;

  const out: Record<string, string | null> = {};
  for (const s of symbols) out[s] = cache.get(s) ?? null;
  return out;
}
