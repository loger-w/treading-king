// 只在「整則訊息 = p + 合法台股代號」時觸發,避免讀到雜訊洗頻。
const RE = /^[pP]([0-9]{4,6}[A-Z]{0,2})$/;
export function parseSymbolCommand(content: string): string | null {
  const m = content.trim().match(RE);
  return m ? m[1].toUpperCase() : null;
}
