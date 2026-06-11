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

/**
 * 合併 chart header / 觸發列表要用的 symbol→name 對照。三個來源都要涵蓋,後者覆蓋前者:
 * 觸發解析 < 監聽清單 < 書籤/搜尋(書籤/搜尋是使用者明確命名的來源,優先)。
 *
 * 監聽清單這個來源容易被漏:在監聽、但不在書籤、且當日未觸發的純監聽股,
 * 名稱會無處可取而顯示「—」。
 */
export function buildSymbolNames(
  resolved: Record<string, string | null>,
  monitorItems: Array<{ symbol: string; name?: string | null }>,
  bookmarkNames: Record<string, string | null>,
): Record<string, string | null> {
  const monitorNames: Record<string, string | null> = {};
  for (const it of monitorItems) monitorNames[it.symbol] = it.name ?? null;
  // 高優先來源只在「有名」時覆蓋:書籤 capture 時可能沒帶名稱(name=null),
  // null 不是明確命名,蓋掉低優先來源的真名會讓 header 永久「—」
  // (Monitor 用 in 檢查 key 存在即跳過補查,null 不會被補救)
  const out: Record<string, string | null> = { ...resolved };
  for (const src of [monitorNames, bookmarkNames]) {
    for (const [sym, name] of Object.entries(src)) {
      if (name !== null) out[sym] = name;
      else if (!(sym in out)) out[sym] = null;
    }
  }
  return out;
}
