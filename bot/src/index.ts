import { Client, GatewayIntentBits, Events, type Message, type BaseMessageOptions } from "discord.js";
import { config, requireToken } from "./config";
import { parseSymbolCommand } from "./symbol";
import { buildSymbolMessages } from "./messages";

async function handle(msg: Message, symbol: string) {
  let messages: BaseMessageOptions[];
  try {
    messages = await buildSymbolMessages(symbol);
  } catch (e) {
    msg.reply(`\`${symbol}\` 查詢失敗(行情暫時不可用)。`).catch(console.error);
    console.warn(`[bot] ${symbol} 失敗:`, e);
    return;
  }
  // 拆多則:第一則回覆原訊息(帶 tag),其餘同頻道送出 — 圖各自獨立一則才會放大、又不重複 tag。
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
