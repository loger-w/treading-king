import { resolveIndexAlias } from "../../frontend/src/lib/index-symbols";

// 只在「整則訊息 = p + 合法台股代號 或 指數別名」時觸發,避免讀到雜訊洗頻。
const RE = /^[pP]([0-9]{4,6}[A-Z]{0,2})$/;
export function parseSymbolCommand(content: string): string | null {
  const t = content.trim();
  const m = t.match(RE);
  if (m) return m[1].toUpperCase();
  // p + 中文別名(加權/大盤/櫃買/上櫃);p 後需至少一字元
  if (/^[pP]./.test(t)) {
    return resolveIndexAlias(t.slice(1));
  }
  return null;
}
