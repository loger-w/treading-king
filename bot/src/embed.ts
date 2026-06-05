import { EmbedBuilder, AttachmentBuilder } from "discord.js";
import type { QuoteResp } from "./data";
import type { CdpLevels, MaLevels } from "../../frontend/src/lib/api";
import { formatTickPrice, roundToNearestTick } from "../../frontend/src/lib/tick";

type Lvl = { price: number; size: number };

// 右對齊輔助:price=0 代表鎖漲跌停的市價單
// 市價 兩個全形字顯示寬=4;padStart(5) 才對齊到數字價欄的顯示寬 7
const cell = (p: number) => (p === 0 ? "市價".padStart(5) : p.toFixed(2).padStart(7));
const qty = (s: number) => (s > 0 ? String(s).padStart(6) : "—".padStart(6));

// 單側一格:價 + 量,等寬右對齊;缺檔補 "—"
const side = (lv: Lvl | undefined) =>
  lv ? `${cell(lv.price)} ${qty(lv.size)}` : `${"—".padStart(7)} ${"—".padStart(6)}`;

// 五檔左右掛單:買盤在左、賣盤在右,最佳價(買1/賣1)在最上、往下到第 5 檔。
// 兩側數字欄等寬右對齊,在 code block 裡上下對齊;不足 5 檔補 "—"。
export function formatLadder(bids: Lvl[], asks: Lvl[]): string {
  const lines: string[] = ["    買盤         賣盤"];
  for (let i = 0; i < 5; i++) lines.push(`${side(bids[i])} │ ${side(asks[i])}`);
  return lines.join("\n");
}

export const sumSize = (a: Lvl[]) => a.reduce((n, x) => n + x.size, 0);

export function buildReply(args: {
  symbol: string; name: string | null; lastClose: number; change: number; changePct: number;
  open: number; high: number; low: number; vwap: number; volume: number;
  cdp: CdpLevels | null; ma: MaLevels | null; quote: QuoteResp | null; png: Buffer | null; asOf: string;
}) {
  const up = args.change > 0;
  // embed 顏色與圖表 theme 一致:漲紅 0xe85a4f / 跌綠 0x7fc99a / 平盤灰 0x8a8273
  const color = up ? 0xe85a4f : args.change < 0 ? 0x7fc99a : 0x8a8273;
  const file = args.png ? new AttachmentBuilder(args.png, { name: "chart.png" }) : null;
  // 五檔失敗 quote=null:五檔區降級,不拖垮已備好的圖/現價/CDP/MA
  const ladder = args.quote ? formatLadder(args.quote.bids, args.quote.asks) : "　五檔暫無資料";
  const limit = args.quote && (args.quote.is_limit_up_bid || args.quote.is_limit_up_ask) ? "　🔺鎖漲停"
    : args.quote && (args.quote.is_limit_down_bid || args.quote.is_limit_down_ask) ? "　🔻鎖跌停" : "";
  const arrow = up ? "▲" : args.change < 0 ? "▾" : "—";
  const cdp = args.cdp
    ? `AH ${args.cdp.ah} ／ NH ${args.cdp.nh} ／ CDP* ${args.cdp.cdp} ／ NL ${args.cdp.nl} ／ AL ${args.cdp.al}`
    : "—";
  // MA 對齊台股 tick,跟圖上 MA 標籤一致(後端回的是原始 SMA,非 tick 值)
  const fmtMa = (v: number | null) => (v == null ? "—" : formatTickPrice(roundToNearestTick(v)));
  const ma = args.ma ? `MA5 ${fmtMa(args.ma.sma_5)} ／ MA20 ${fmtMa(args.ma.sma_20)}` : "—";

  const embed = new EmbedBuilder()
    .setColor(color)
    .setTitle(`${args.name ?? ""} ${args.symbol}`.trim())
    .setDescription(
      `**${args.lastClose.toFixed(2)}**　${arrow}${Math.abs(args.change).toFixed(2)} (${args.changePct >= 0 ? "+" : ""}${args.changePct.toFixed(2)}%)${limit}\n` +
      "```\n" + ladder + "\n```",
    )
    .addFields(
      { name: "開 / 高 / 低", value: `${args.open} / ${args.high} / ${args.low}`, inline: true },
      { name: "均價 / 量", value: `${args.vwap.toFixed(2)} / ${args.volume}`, inline: true },
      { name: "委買 / 委賣(張)", value: args.quote ? `${sumSize(args.quote.bids)} / ${sumSize(args.quote.asks)}` : "—", inline: true },
      { name: "CDP", value: cdp, inline: false },
      { name: "均線", value: ma, inline: false },
    )
    .setFooter({ text: `資料 ${args.asOf}` });
  // 產圖失敗時 png=null:省略附圖,只回文字 embed(現價/五檔/CDP/MA 已備齊)
  if (file) embed.setImage("attachment://chart.png");

  return { embeds: [embed], files: file ? [file] : [] };
}
