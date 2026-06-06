import { EmbedBuilder, AttachmentBuilder } from "discord.js";
import type { QuoteResp } from "./data";
import type { CdpLevels, MaLevels } from "../../frontend/src/lib/api";
import { formatTickPrice, roundToNearestTick } from "../../frontend/src/lib/tick";

// 文字訊息那則 — 現價/開高低/均價量/CDP/均線。分時圖、五檔圖各自獨立一則(見 reply.composeReply),
// 不放進這個 embed,避免被 Discord 內嵌縮小。
export function buildReply(args: {
  symbol: string; name: string | null; lastClose: number; change: number; changePct: number;
  open: number; high: number; low: number; vwap: number; volume: number;
  cdp: CdpLevels | null; ma: MaLevels | null; quote: QuoteResp | null; asOf: string;
}) {
  const up = args.change > 0;
  // embed 顏色與圖表 theme 一致:漲紅 0xe85a4f / 跌綠 0x7fc99a / 平盤灰 0x8a8273
  const color = up ? 0xe85a4f : args.change < 0 ? 0x7fc99a : 0x8a8273;
  // quote 僅用於偵測鎖漲/跌停標記;五檔內容走獨立的五檔圖
  const limit = args.quote && (args.quote.is_limit_up_bid || args.quote.is_limit_up_ask) ? "　🔺鎖漲停"
    : args.quote && (args.quote.is_limit_down_bid || args.quote.is_limit_down_ask) ? "　🔻鎖跌停" : "";
  const arrow = up ? "▲" : args.change < 0 ? "▾" : "—";
  const cdp = args.cdp
    ? `AH* ${args.cdp.ah} ／ NH* ${args.cdp.nh} ／ CDP* ${args.cdp.cdp} ／ NL* ${args.cdp.nl} ／ AL* ${args.cdp.al}`
    : "—";
  // MA 對齊台股 tick,跟圖上 MA 標籤一致(後端回的是原始 SMA,非 tick 值)
  const fmtMa = (v: number | null) => (v == null ? "—" : formatTickPrice(roundToNearestTick(v)));
  const ma = args.ma ? `MA5 ${fmtMa(args.ma.sma_5)} ／ MA20 ${fmtMa(args.ma.sma_20)}` : "—";

  const embed = new EmbedBuilder()
    .setColor(color)
    .setTitle(`${args.name ?? ""} ${args.symbol}`.trim())
    .setDescription(
      `**${args.lastClose.toFixed(2)}**　${arrow}${Math.abs(args.change).toFixed(2)} (${args.changePct >= 0 ? "+" : ""}${args.changePct.toFixed(2)}%)${limit}`,
    )
    .addFields(
      { name: "開 / 高 / 低", value: `${args.open} / ${args.high} / ${args.low}`, inline: true },
      { name: "均價 / 量", value: `${args.vwap.toFixed(2)} / ${args.volume}`, inline: true },
      { name: "CDP", value: cdp, inline: false },
      { name: "均線", value: ma, inline: false },
    )
    .setFooter({ text: `資料 ${args.asOf}` });
  return { embeds: [embed] };
}

// 圖片各自獨立一則:頂層附件(不被 embed 引用)→ Discord feed 放大顯示,比內嵌 embed 明顯。
export function imageMessage(buf: Buffer, name: string) {
  return { files: [new AttachmentBuilder(buf, { name })] };
}
