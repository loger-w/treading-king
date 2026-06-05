import { Client, GatewayIntentBits, Events, type Message } from "discord.js";
import { config } from "./config";
import { parseSymbolCommand } from "./symbol";
import { getQuote, getCandles, getCdp, getMa, getName } from "./data";
import { TtlCache } from "./cache";
import { renderChartPng, safeRender } from "./render";
import { buildReply } from "./embed";
import { MARKET_OPEN_MIN, MARKET_CLOSE_MIN, minuteOfDay } from "../../frontend/src/lib/intraday-time";

// 慢資料(分時 K / CDP / MA / 已 render PNG)30s 快取;五檔每次即時抓(見 handle)。
const slow = new TtlCache<Awaited<ReturnType<typeof loadSlow>>>(30_000);

async function loadSlow(symbol: string) {
  const [candlesR, cdp, ma, name] = await Promise.all([
    getCandles(symbol),
    getCdp(symbol).catch(() => null),
    getMa(symbol).catch(() => null),
    getName(symbol),
  ]);
  const flags = { vwap: true, cdp: true, camarilla: false, volume: true, ma: true };
  const intraday = candlesR.data.filter((c) => {
    const m = minuteOfDay(c.date);
    return m >= MARKET_OPEN_MIN && m <= MARKET_CLOSE_MIN;
  });

  if (intraday.length === 0) {
    return { empty: true as const, cdp, ma, name, prevClose: candlesR.prev_close };
  }

  const last = intraday[intraday.length - 1];
  const baseline = candlesR.prev_close ?? intraday[0].open;
  const change = last.close - baseline;
  const changePct = baseline ? (change / baseline) * 100 : 0;
  const high = Math.max(...intraday.map((c) => c.high));
  const low = Math.min(...intraday.map((c) => c.low));

  // 產圖失敗(resvg／缺字型)不該炸掉整則:safeRender 失敗回 null,buildReply 退純文字(spec §8)
  const png = safeRender(() => renderChartPng({
    candles: candlesR.data, prevClose: candlesR.prev_close, cdp, camarilla: null, ma, flags,
    symbol, name, lastClose: last.close, change, changePct,
  }));

  return {
    empty: false as const, name, cdp, ma, png,
    lastClose: last.close, change, changePct,
    open: intraday[0].open, high, low,
    vwap: last.average,
    volume: intraday.reduce((n, c) => n + c.volume, 0),
    asOf: last.date.slice(11, 16),
  };
}

async function handle(msg: Message, symbol: string) {
  try {
    const [s, quote] = await Promise.all([
      slow.get(symbol, () => loadSlow(symbol)),
      getQuote(symbol).catch(() => null),  // 五檔失敗 → null,不拖垮已備好的圖/CDP/MA(spec §8)
    ]);

    if (s.empty) {
      await msg.reply(
        `\`${symbol}\` 目前無分時資料(盤前/非交易日)。` +
        `CDP:${s.cdp ? `${s.cdp.cdp}` : "—"} MA5:${s.ma?.sma_5 ?? "—"}`,
      );
      return;
    }

    const reply = buildReply({
      symbol, name: s.name,
      lastClose: s.lastClose, change: s.change, changePct: s.changePct,
      open: s.open, high: s.high, low: s.low,
      vwap: s.vwap, volume: s.volume,
      cdp: s.cdp, ma: s.ma, quote, png: s.png, asOf: s.asOf,
    });
    await msg.reply(reply);
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

client.login(config.token);
