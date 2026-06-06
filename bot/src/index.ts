import { Client, GatewayIntentBits, Events, type Message, type BaseMessageOptions } from "discord.js";
import { config, requireToken } from "./config";
import { parseSymbolCommand } from "./symbol";
import { getQuote } from "./data";
import { renderQuotePng, safeRender } from "./render";
import { TtlCache } from "./cache";
import { loadSlow, composeReply, type SlowResult } from "./reply";

// 慢資料(分時 K / CDP / MA / 分時圖 PNG)30s 快取;五檔 quote + quotePng 每次即時抓(見 handle)。
const slow = new TtlCache<SlowResult>(30_000);

async function handle(msg: Message, symbol: string) {
  let messages: BaseMessageOptions[];
  try {
    const [s, quote] = await Promise.all([
      slow.get(symbol, () => loadSlow(symbol)),
      getQuote(symbol).catch(() => null),  // 五檔失敗 → null,不拖垮已備好的圖/CDP/MA(spec §8)
    ]);
    // 五檔圖必須跟即時 quote 同產(quote 不走快取);產圖失敗 → null,只少一則
    const quotePng = quote ? safeRender(() => renderQuotePng(quote)) : null;
    messages = composeReply(symbol, s, quote, quotePng);
  } catch (e) {
    msg.reply(`\`${symbol}\` 查詢失敗(行情暫時不可用)。`).catch(console.error);
    console.warn(`[bot] ${symbol} 失敗:`, e);
    return;
  }
  // 拆三則:第一則回覆原訊息(帶 tag),其餘同頻道送出 — 圖各自獨立一則才會放大、又不重複 tag。
  // 第一則已送出後就不再丟通用失敗訊息;後續沒送到只記 log(不蓋掉已成功的內容)。
  try {
    await msg.reply(messages[0]);
    for (let i = 1; i < messages.length; i++) {
      if ("send" in msg.channel) await msg.channel.send(messages[i]);
    }
  } catch (e) {
    console.warn(`[bot] ${symbol} 送出部分訊息失敗:`, e);
  }
}

const client = new Client({
  intents: [
    GatewayIntentBits.Guilds,
    GatewayIntentBits.GuildMessages,
    GatewayIntentBits.MessageContent,
  ],
});

client.once(Events.ClientReady, (c) => console.log(`[bot] 上線:${c.user.tag}`));

client.on(Events.MessageCreate, (msg) => {
  if (msg.author.bot) return;
  if (config.allowedChannels.length && !config.allowedChannels.includes(msg.channelId)) return;
  const symbol = parseSymbolCommand(msg.content);
  if (!symbol) return;
  void handle(msg, symbol);
});

client.login(requireToken());
