import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { resolveNameFromResults } from "../lib/symbol-name";

// Module-level cache — 跨 hook instance 共用,name 不變,命中後永不重打
const cache = new Map<string, string | null>();
const inFlight = new Map<string, Promise<void>>();

// 同時最多打幾條 /api/symbols — 避免 trigger symbol 一多就 N 條並發塞爆後端 thread pool
const MAX_CONCURRENCY = 5;

// 查無 name 的 symbol 多久重打一次。後端 symbols 市場快取是「本機自然重建」,
// 啟動初期還沒載入時查會落空 → 定期重試直到補上,而非永久顯示裸代號。
const RETRY_DELAY_MS = 20_000;

async function lookupOne(sym: string): Promise<void> {
  try {
    const r = await api.symbols(sym, 5);
    const resolved = resolveNameFromResults(sym, r.results);
    // resolved === undefined:查無精確 match(symbols 快取可能還沒載入)→ 不寫 cache,
    // 留給 retryTick 重打。寫了 null 會被當「已知無名」永久不重試。
    if (resolved !== undefined) cache.set(sym, resolved);
  } catch {
    // 失敗也不寫 cache → symbols 變動 / retryTick 時可重試
  }
}

/**
 * 對給定 symbols 解析「代號 → 名稱」。
 *
 * 用途:訊號可對非自選股觸發,那些 symbol 不在 BookmarksPanel 回報的 name map,
 * TriggerList / 分時走勢只剩裸代號。這裡用既有 /api/symbols(前綴查 + 取精確 match)補名稱。
 *
 * - module-level cache + in-flight dedup(同 symbol 多 hook 只打一次)
 * - 限並發 5,避免 trigger symbol 多時壓垮後端
 * - 查無 → 不寫 cache + retryTick 定期重打(symbols 快取載入前查會落空,不能永久放棄)
 */
export function useSymbolNames(symbols: string[]): Record<string, string | null> {
  const [version, setVersion] = useState(0);
  const [retryTick, setRetryTick] = useState(0);
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
  }, [symbolsKey, retryTick]);

  // 還有 symbol 沒查到 name(查無 → 沒進 cache)→ 定期 bump retryTick 重打,
  // 全部命中後 stillMissing=false 就停。version 進 deps 讓每次查完重新評估。
  useEffect(() => {
    const stillMissing = symbols.some((s) => !cache.has(s));
    if (!stillMissing) return;
    const t = setTimeout(() => setRetryTick((n) => n + 1), RETRY_DELAY_MS);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbolsKey, retryTick, version]);

  void version;

  const out: Record<string, string | null> = {};
  for (const s of symbols) out[s] = cache.get(s) ?? null;
  return out;
}
