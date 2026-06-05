import { Client, GatewayIntentBits, Events, type Message } from "discord.js";
import { config, requireToken } from "./config";
import { parseSymbolCommand } from "./symbol";
import { getQuote } from "./data";
import { renderQuotePng, safeRender } from "./render";
import { TtlCache } from "./cache";
import { loadSlow, composeReply, type SlowResult } from "./reply";

// 慢資料(分時 K / CDP / MA / 已 render PNG)30s 快取;五檔每次即時抓(見 handle)。
const slow = new TtlCache<SlowResult>(30_000);

async function handle(msg: Message, symbol: string) {
  try {
    const [s, quote] = await Promise.all([
      slow.get(symbol, () => loadSlow(symbol)),
      getQuote(symbol).catch(() => null),  // 五檔失敗 → null,不拖垮已備好的圖/CDP/MA(spec §8)
    ]);
    // 五檔圖必須跟即時 quote 同產(quote 不走快取);產圖失敗 → null,只少一張圖
    const quotePng = quote ? safeRender(() => renderQuotePng(quote)) : null;
    await msg.reply(composeReply(symbol, s, quote, quotePng));
  } catch (e) {
    // handle 以 void 呼叫;reply 本身也可能丟出(如缺少頻道權限),改為 fire-and-log 避免 unhandled rejection
    msg.reply(`\`${symbol}\` 查詢失敗(行情暫時不可用)。`).catch(console.error);
    console.warn(`[bot] ${symbol} 失敗:`, e);
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
