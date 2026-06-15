import { arrayMove } from "@dnd-kit/sortable";

/**
 * 書籤/監聽列表排序純函式 — 抽出來測(專案無 hook 測試環境)。
 */

/**
 * 把「拖 active 放到 over 的位置」套到完整順序。搬移本體用 dnd-kit 的
 * arrayMove — 與 SortableContext 拖拉預覽的位移計算同一份語意,不自製。
 */
export function applyDragToOrder(order: string[], active: string, over: string): string[] {
  const from = order.indexOf(active);
  const to = order.indexOf(over);
  if (from < 0 || to < 0 || from === to) return order;
  return arrayMove(order, from, to);
}
