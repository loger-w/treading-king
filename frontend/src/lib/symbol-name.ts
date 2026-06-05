/**
 * 從 /api/symbols 前綴查結果解析某 symbol 的名稱 cache 值。
 *
 * - 回 `undefined`:查無精確 match(symbols 快取可能還沒載入)→ 不該寫入 cache,
 *   之後重打就會補上。
 * - 回 `string | null`:確定值(查到名稱、或確定此 symbol 無名)→ 可快取、不需重試。
 *
 * 關鍵:查無時回 `undefined` 而非 `null`,呼叫端才不會把它當「已知無名」永久不
 * 重試(這正是分時走勢名稱有時永久「—」的成因)。
 */
export function resolveNameFromResults(
  sym: string,
  results: Array<{ symbol: string; name?: string | null }>,
): string | null | undefined {
  const hit = results.find((row) => row.symbol === sym);
  if (!hit) return undefined;
  return hit.name ?? null;
}
