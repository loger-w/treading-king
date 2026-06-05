import { EmbedBuilder, AttachmentBuilder } from "discord.js";
import type { QuoteResp } from "./data";
import type { CdpLevels, MaLevels } from "../../frontend/src/lib/api";

type Lvl = { price: number; size: number };

// 右對齊輔助:price=0 代表鎖漲跌停的市價單
// 市價 兩個全形字顯示寬=4;padStart(5) 才對齊到數字價欄的顯示寬 7
const cell = (p: number) => (p === 0 ? "市價".padStart(5) : p.toFixed(2).padStart(7));
const qty = (s: number) => (s > 0 ? String(s).padStart(6) : "—".padStart(6));

// 建構五檔階梯文字(賣5→賣1 / 分隔線 / 買1→買5)
// 不足 5 檔時補 "—";price=0(鎖漲跌停)顯示 "市價"
export function formatLadder(bids: Lvl[], asks: Lvl[]): string {
  const row = (label: string, lv: Lvl | undefined) =>
    lv ? `${label} ${cell(lv.price)} ${qty(lv.size)}` : `${label} ${"—".padStart(7)} ${"—".padStart(6)}`;
  const lines: string[] = [];
  for (let i = 4; i >= 0; i--) lines.push(row(`賣${i + 1}`, asks[i]));
  lines.push("───────────────");
  for (let i = 0; i < 5; i++) lines.push(row(`買${i + 1}`, bids[i]));
  return lines.join("\n");
}

export const sumSize = (a: Lvl[]) => a.reduce((n, x) => n + x.size, 0);

export function buildReply(args: {
  symbol: string; name: string | null; lastClose: number; change: number; changePct: number;
  open: number; high: number; low: number; vwap: number; volume: number;
  cdp: CdpLevels | null; ma: MaLevels | null; quote: QuoteResp; png: Buffer; asOf: string;
}) {
  const up = args.change > 0;
  // embed 顏色與圖表 theme 一致:漲紅 0xe85a4f / 跌綠 0x7fc99a / 平盤灰 0x8a8273
  const color = up ? 0xe85a4f : args.change < 0 ? 0x7fc99a : 0x8a8273;
  const file = new AttachmentBuilder(args.png, { name: "chart.png" });
  const ladder = formatLadder(args.quote.bids, args.quote.asks);
  const limit = args.quote.is_limit_up_bid || args.quote.is_limit_up_ask ? "　🔺鎖漲停"
    : args.quote.is_limit_down_bid || args.quote.is_limit_down_ask ? "　🔻鎖跌停" : "";
  const arrow = up ? "▲" : args.change < 0 ? "▾" : "—";
  const cdp = args.cdp
    ? `AH ${args.cdp.ah} ／ NH ${args.cdp.nh} ／ CDP ${args.cdp.cdp} ／ NL ${args.cdp.nl} ／ AL ${args.cdp.al}`
    : "—";
  const ma = args.ma ? `MA5 ${args.ma.sma_5 ?? "—"} ／ MA20 ${args.ma.sma_20 ?? "—"}` : "—";

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
      { name: "委買 / 委賣(張)", value: `${sumSize(args.quote.bids)} / ${sumSize(args.quote.asks)}`, inline: true },
      { name: "CDP", value: cdp, inline: false },
      { name: "均線", value: ma, inline: false },
    )
    .setImage("attachment://chart.png")
    .setFooter({ text: `資料 ${args.asOf}` });

  return { embeds: [embed], files: [file] };
}
