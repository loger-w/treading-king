import { useEffect, useState } from "react";
import { api } from "../lib/api";

export interface SnapshotEntry {
  prevClose: number | null;
  lastPrice: number | null;
}

// Module-level cache — 跨 hook instance 共用（watchlist + trigger 同 symbol 不重抓）
const cache = new Map<string, SnapshotEntry>();
const inFlight = new Map<string, Promise<void>>();

// backend /api/quotes/snapshot 限制單批 50 檔(max_length=50)
const SNAPSHOT_BATCH_SIZE = 50;

/**
 * 對給定的 symbols 集合，把沒見過的湊批打 /api/quotes/snapshot 並寫入 cache。
 * 回傳當前 cache 中對應 symbols 的快照（snake_case → camelCase 已轉）。
 *
 * - cache 是 module-level：watchlist 抓過後 trigger 用同 symbol 不會重打
 * - in-flight dedup：同一個 symbol 同時被多個 hook 要時，只打一次 API
 * - 分批：missing > 50 切 chunk 平行打,避免 backend 整批 400
 * - prev_close = null 不寫 cache：富邦回失敗時下次 symbols 變動可重試,不然會永久顯示 "—"
 * - 沒做 TTL：當日盤中 prev_close 不會變,盤後新 trading day 才會差；
 *   過 24h 仍命中舊 cache → reload 頁面解
 */
export function useSnapshotCache(symbols: string[]): Record<string, SnapshotEntry> {
  const [version, setVersion] = useState(0);

  useEffect(() => {
    const missing = symbols.filter((s) => !cache.has(s) && !inFlight.has(s));
    if (missing.length === 0) return;

    const chunks: string[][] = [];
    for (let i = 0; i < missing.length; i += SNAPSHOT_BATCH_SIZE) {
      chunks.push(missing.slice(i, i + SNAPSHOT_BATCH_SIZE));
    }

    const promise = (async () => {
      try {
        await Promise.all(
          chunks.map(async (chunk) => {
            try {
              const r = await api.quotesSnapshot(chunk);
              for (const row of r.quotes) {
                if (row.prev_close === null) continue;
                cache.set(row.symbol, {
                  prevClose: row.prev_close,
                  lastPrice: row.last_price,
                });
              }
            } catch (e) {
              console.warn("useSnapshotCache chunk fetch failed:", e);
            }
          }),
        );
      } finally {
        for (const s of missing) inFlight.delete(s);
        setVersion((v) => v + 1);
      }
    })();

    for (const s of missing) inFlight.set(s, promise);
  }, [symbols.join(",")]);  // deps 用 stringified 避免每 render 變陣列實例就 re-fire

  // version 變數讓 React 認為 deps 變了 — 即使 out 物件 ref 同
  void version;

  const out: Record<string, SnapshotEntry> = {};
  for (const s of symbols) {
    out[s] = cache.get(s) ?? { prevClose: null, lastPrice: null };
  }
  return out;
}
