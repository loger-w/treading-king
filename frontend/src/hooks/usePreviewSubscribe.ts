import { useEffect, useRef } from "react";
import { api } from "../lib/api";

/**
 * 當 selected 是「不在 watchlist 內」的股票時，呼叫 backend POST /api/preview
 * 設定該 symbol 為 preview owner（後端真打富邦訂閱）。
 *
 * 若 selected 在 watchlist 內或為 null → 通知後端清空 preview（owner='preview'
 * 不再訂閱任何 symbol；watchlist owner 仍持有原訂閱）。
 *
 * 用 ref 記住最後一次告訴後端的值，避免相同值重複 POST。
 * Unmount 時清空 preview。
 */
export function usePreviewSubscribe(
  selected: string | null,
  watchlistSymbols: string[],
): void {
  const lastSentRef = useRef<string | null | undefined>(undefined);

  useEffect(() => {
    const inWatchlist = selected !== null && watchlistSymbols.includes(selected);
    const desired: string | null = selected && !inWatchlist ? selected : null;

    if (lastSentRef.current === desired) return;
    lastSentRef.current = desired;

    api.preview(desired).catch((e) => {
      console.warn("preview subscribe failed:", e);
      // 失敗就讓 lastSentRef 重置，下次 effect 會 retry
      lastSentRef.current = undefined;
    });
  }, [selected, watchlistSymbols]);

  useEffect(() => {
    // Unmount 時清空 — 避免後端殘留訂閱
    return () => {
      if (lastSentRef.current !== null) {
        api.preview(null).catch(() => { /* 忽略；後端 process 結束時自然清掉 */ });
      }
    };
  }, []);
}
