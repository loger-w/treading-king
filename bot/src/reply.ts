import { getCandles, getCdp, getMa, getName } from "./data";
import type { QuoteResp } from "./data";
import { renderChartPng, safeRender } from "./render";
import { buildReply } from "./embed";
import { MARKET_OPEN_MIN, MARKET_CLOSE_MIN, minuteOfDay } from "../../frontend/src/lib/intraday-time";
import type { MessageReplyOptions } from "discord.js";

// orchestration 的外部相依抽成介面,測試可注入假資料／強迫產圖失敗(review #5)
export interface ReplyDeps {
  getCandles: typeof getCandles;
  getCdp: typeof getCdp;
  getMa: typeof getMa;
  getName: typeof getName;
  render: typeof renderChartPng;
}

const realDeps: ReplyDeps = { getCandles, getCdp, getMa, getName, render: renderChartPng };

// 慢資料:分時 K / CDP / MA / 已 render 的 PNG。產圖失敗只讓 png=null,不炸整則(spec §8)。
export async function loadSlow(symbol: string, deps: ReplyDeps = realDeps) {
  const [candlesR, cdp, ma, name] = await Promise.all([
    deps.getCandles(symbol),
    deps.getCdp(symbol).catch(() => null),
    deps.getMa(symbol).catch(() => null),
    deps.getName(symbol),
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

  const png = safeRender(() => deps.render({
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

export type SlowResult = Awaited<ReturnType<typeof loadSlow>>;

// 把 slow 結果 + 即時五檔組成 discord 回覆:空盤前 → 純文字(仍給 CDP/MA5);否則 → buildReply。
// 純函式,好測;降級判斷(png=null / quote=null)交給 buildReply。
export function composeReply(symbol: string, s: SlowResult, quote: QuoteResp | null): MessageReplyOptions {
  if (s.empty) {
    return {
      content:
        `\`${symbol}\` 目前無分時資料(盤前/非交易日)。` +
        `CDP:${s.cdp ? `${s.cdp.cdp}` : "—"} MA5:${s.ma?.sma_5 ?? "—"}`,
    };
  }
  return buildReply({
    symbol, name: s.name,
    lastClose: s.lastClose, change: s.change, changePct: s.changePct,
    open: s.open, high: s.high, low: s.low,
    vwap: s.vwap, volume: s.volume,
    cdp: s.cdp, ma: s.ma, quote, png: s.png, asOf: s.asOf,
  });
}
