import { getCandles, getCdp, getMa, getName } from "./data";
import type { QuoteResp } from "./data";
import { renderChartPng, renderIndexChartPng, safeRender } from "./render";
import { buildReply, buildIndexReply, imageMessage } from "./embed";
import { MARKET_OPEN_MIN, MARKET_CLOSE_MIN, minuteOfDay } from "../../frontend/src/lib/intraday-time";
import { isIndexCode } from "../../frontend/src/lib/index-symbols";
import type { BaseMessageOptions } from "discord.js";

// orchestration 的外部相依抽成介面,測試可注入假資料／強迫產圖失敗(review #5)
export interface ReplyDeps {
  getCandles: typeof getCandles;
  getCdp: typeof getCdp;
  getMa: typeof getMa;
  getName: typeof getName;
  render: typeof renderChartPng;
  renderIndex: typeof renderIndexChartPng;
}

const realDeps: ReplyDeps = { getCandles, getCdp, getMa, getName, render: renderChartPng, renderIndex: renderIndexChartPng };

// 慢資料:分時 K / CDP / MA / 已 render 的 PNG。產圖失敗只讓 png=null,不炸整則(spec §8)。
export async function loadSlow(symbol: string, deps: ReplyDeps = realDeps) {
  if (isIndexCode(symbol)) return loadSlowIndex(symbol, deps);
  const [candlesR, cdp, ma, name] = await Promise.all([
    deps.getCandles(symbol),
    deps.getCdp(symbol).catch(() => null),
    deps.getMa(symbol).catch(() => null),
    deps.getName(symbol),
  ]);
  // bot 推播的分時圖不畫 MA5/MA20 兩條水平線;embed 的「均線」欄位仍保留(走 s.ma,不吃 flags)
  const flags = { vwap: true, cdp: true, camarilla: false, volume: true, ma: false };
  const intraday = candlesR.data.filter((c) => {
    const m = minuteOfDay(c.date);
    return m >= MARKET_OPEN_MIN && m <= MARKET_CLOSE_MIN;
  });

  if (intraday.length === 0) {
    return { empty: true as const, isIndex: false as const, cdp, ma, name, prevClose: candlesR.prev_close };
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
    empty: false as const, isIndex: false as const, name, cdp, ma, png,
    lastClose: last.close, change, changePct,
    open: intraday[0].open, high, low,
    vwap: last.average,
    volume: intraday.reduce((n, c) => n + c.volume, 0),
    asOf: last.date.slice(11, 16),
  };
}

// 指數精簡路徑:不抓 CDP/MA/quote,flags 全關;圖走 renderIndex。
async function loadSlowIndex(symbol: string, deps: ReplyDeps) {
  const [candlesR, name] = await Promise.all([deps.getCandles(symbol), deps.getName(symbol)]);
  const intraday = candlesR.data.filter((c) => {
    const m = minuteOfDay(c.date);
    return m >= MARKET_OPEN_MIN && m <= MARKET_CLOSE_MIN;
  });
  if (intraday.length === 0) {
    return { empty: true as const, isIndex: true as const, name, prevClose: candlesR.prev_close };
  }
  const last = intraday[intraday.length - 1];
  const baseline = candlesR.prev_close ?? intraday[0].open;
  const change = last.close - baseline;
  const changePct = baseline ? (change / baseline) * 100 : 0;
  const high = Math.max(...intraday.map((c) => c.high));
  const low = Math.min(...intraday.map((c) => c.low));
  const png = safeRender(() => deps.renderIndex({
    candles: candlesR.data, prevClose: candlesR.prev_close,
    symbol, name, lastClose: last.close, change, changePct,
  }));
  return {
    empty: false as const, isIndex: true as const, name, png,
    lastClose: last.close, change, changePct,
    open: intraday[0].open, high, low, asOf: last.date.slice(11, 16),
  };
}

export type SlowResult = Awaited<ReturnType<typeof loadSlow>>;

// 組「三則」discord 訊息:① 文字(現價/CDP/均線)② 分時圖 ③ 五檔圖。
// 圖各自獨立一則 → Discord 頂層附件放大顯示,比內嵌 embed 明顯;空盤前只回一則純文字。
// 降級:某張圖 null 就跳過該則(不炸整體,spec §8)。
export function composeReply(
  symbol: string, s: SlowResult, quote: QuoteResp | null, quotePng: Buffer | null,
): BaseMessageOptions[] {
  // 指數:精簡(現價文字 + 走勢圖),不發五檔
  if (s.isIndex) {
    if (s.empty) {
      return [{ content: `\`${symbol}\` 目前無分時資料(盤前/非交易日)。` }];
    }
    const idxMsgs: BaseMessageOptions[] = [buildIndexReply({
      symbol, name: s.name, lastClose: s.lastClose, change: s.change, changePct: s.changePct,
      open: s.open, high: s.high, low: s.low, asOf: s.asOf,
    })];
    if (s.png) idxMsgs.push(imageMessage(s.png, "chart.png"));
    return idxMsgs;
  }
  if (s.empty) {
    return [{
      content:
        `\`${symbol}\` 目前無分時資料(盤前/非交易日)。` +
        `CDP:${s.cdp ? `${s.cdp.cdp}` : "—"} MA5:${s.ma?.sma_5 ?? "—"}`,
    }];
  }
  const messages: BaseMessageOptions[] = [
    buildReply({
      symbol, name: s.name,
      lastClose: s.lastClose, change: s.change, changePct: s.changePct,
      open: s.open, high: s.high, low: s.low,
      vwap: s.vwap, volume: s.volume,
      cdp: s.cdp, ma: s.ma, quote, asOf: s.asOf,
    }),
  ];
  if (s.png) messages.push(imageMessage(s.png, "chart.png"));
  if (quotePng) messages.push(imageMessage(quotePng, "quote.png"));
  return messages;
}
