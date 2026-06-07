import type { BaseMessageOptions } from "discord.js";
import { getQuote } from "./data";
import { renderQuotePng, safeRender } from "./render";
import { TtlCache } from "./cache";
import { loadSlow, composeReply, type SlowResult } from "./reply";

// 慢資料(分時 K / CDP / MA / 分時圖 PNG)30s 快取;五檔 quote + quotePng 每次即時抓。
// 訊息處理器(p代號)與訊號 push 共用同一份快取,連續查同檔可重用圖。
const slow = new TtlCache<SlowResult>(30_000);

export async function buildSymbolMessages(symbol: string): Promise<BaseMessageOptions[]> {
  const [s, quote] = await Promise.all([
    slow.get(symbol, () => loadSlow(symbol)),
    getQuote(symbol).catch(() => null),  // 五檔失敗 → null,不拖垮已備好的圖/CDP/MA
  ]);
  // 五檔圖必須跟即時 quote 同產(quote 不走快取);產圖失敗 → null,只少一則
  const quotePng = quote ? safeRender(() => renderQuotePng(quote)) : null;
  return composeReply(symbol, s, quote, quotePng);
}
