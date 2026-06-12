import { arrayMove } from "@dnd-kit/sortable";

/**
 * 書籤列表排序純函式 — 抽出來測(專案無 hook 測試環境)。
 *
 * 概念:後端 position 給出「完整順序」;顯示時訊號命中的置頂(不可拖拉),
 * 其餘照完整順序排。拖拉只發生在非置頂區,但結果要套回完整順序送後端。
 */

/** 訊號命中的置頂(命中數降冪、同數維持原順序),其餘照原順序。 */
export function partitionByHits<T extends { symbol: string }>(
  items: T[],
  totalHits: (symbol: string) => number,
): { pinned: T[]; rest: T[] } {
  const pinned = items
    .filter((it) => totalHits(it.symbol) > 0)
    .sort((a, b) => totalHits(b.symbol) - totalHits(a.symbol));
  const rest = items.filter((it) => totalHits(it.symbol) === 0);
  return { pinned, rest };
}

/**
 * 把「拖 active 放到 over 的位置」套到完整順序。搬移本體用 dnd-kit 的
 * arrayMove — 與 SortableContext 拖拉預覽的位移計算同一份語意,不自製。
 * 置頂項目不會是 over(不在拖拉區),它們的 slot 自然保留。
 */
export function applyDragToOrder(order: string[], active: string, over: string): string[] {
  const from = order.indexOf(active);
  const to = order.indexOf(over);
  if (from < 0 || to < 0 || from === to) return order;
  return arrayMove(order, from, to);
}
