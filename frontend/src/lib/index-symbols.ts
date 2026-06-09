// 台股大盤指數常數 — 前端頁面與 Discord bot 共用。
// 用價格指數(IX),非報酬指數(IR)。代碼經 backend /api/candles 實測。
export interface IndexSymbol {
  code: string; // 富邦行情代碼
  name: string; // 顯示全名
  short: string; // 短名(圖例)
  color: string; // 重疊圖識別色 hex
  aliases: string[]; // bot p 指令中文別名
}

export const INDEX_SYMBOLS: IndexSymbol[] = [
  { code: "IX0001", name: "加權指數", short: "加權", color: "#f0b429", aliases: ["加權", "大盤"] },
  { code: "IX0043", name: "櫃買指數", short: "櫃買", color: "#3b82f6", aliases: ["櫃買", "上櫃"] },
];

const BY_CODE = new Map(INDEX_SYMBOLS.map((s) => [s.code, s]));
const BY_ALIAS = new Map(
  INDEX_SYMBOLS.flatMap((s) => s.aliases.map((a) => [a, s.code] as const)),
);

export function isIndexCode(code: string): boolean {
  return BY_CODE.has(code);
}

export function indexName(code: string): string | null {
  return BY_CODE.get(code)?.name ?? null;
}

export function indexMeta(code: string): IndexSymbol | null {
  return BY_CODE.get(code) ?? null;
}

/** bot p 指令:中文別名 → 代碼。未知回 null。 */
export function resolveIndexAlias(input: string): string | null {
  return BY_ALIAS.get(input.trim()) ?? null;
}
