import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { Resvg } from "@resvg/resvg-js";
import { existsSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import {
  IntradayChartStatic, computeIntradayGeometry, INTRADAY_THEME,
  CHART_W, CHART_H, TOTAL_H, type IntradayChartInput,
} from "../../frontend/src/lib/intraday-chart-svg";
import {
  IndexIntradayStatic, computeIndexGeometry, fmtIndex, type IndexChartInput,
} from "../../frontend/src/lib/index-intraday-svg";
import { QuoteBookSvg } from "../../frontend/src/lib/quote-book-svg";
import type { QuoteResp } from "./data";

// Windows 本機一定有 Microsoft JhengHei(正黑體),可同時顯示中文與數字。
// 若 bot/assets/ 下有自帶字型則額外掛入並優先使用(選填,沒有也能跑)。
const ASSETS_DIR = join(dirname(fileURLToPath(import.meta.url)), "../assets");
const TITLE_H = 58;  // 標題帶 150% 字級(33px)需要更高的帶子

// 字型 family 名稱 — 對齊 loadSystemFonts + defaultFontFamily 設定
const FONT_FAMILY = "Microsoft JhengHei";

// INTRADAY_THEME 預設 fontFamily 是 "Inter Tight, system-ui, sans-serif"。
// Override 成 Windows CJK 字型,確保 resvg 能正確 render 中文與數字。
const THEME = { ...INTRADAY_THEME, fontFamily: `"${FONT_FAMILY}", sans-serif` };

// 標題帶左側「代號 + 名稱」過長會撞到右側現價(33px 字、820 寬)。
// 上界 14 字元(代號 ~5 + 空格 + 中文名),超過截斷補 …。
export function fitTitle(symbol: string, name: string | null): string {
  const full = `${symbol}${name ? " " + name : ""}`;
  const MAX = 14;
  const chars = [...full];
  return chars.length > MAX ? chars.slice(0, MAX - 1).join("") + "…" : full;
}

// 掃描 bot/assets/*.ttf 選填字型(授權 OFL 可內嵌);模組載入時執行一次
// 用 existsSync guard — 目錄不存在或無 .ttf 都安全略過
function discoverFontFiles(): string[] {
  if (!existsSync(ASSETS_DIR)) return [];
  try {
    return readdirSync(ASSETS_DIR)
      .filter((f) => f.endsWith(".ttf"))
      .map((f) => join(ASSETS_DIR, f));
  } catch {
    return [];
  }
}

const EXTRA_FONTS = discoverFontFiles();

export function buildChartSvg(args: IntradayChartInput & {
  symbol: string; name: string | null; lastClose: number; change: number; changePct: number;
}): string {
  const input: IntradayChartInput = { ...args, theme: THEME, scale: 1.6 };  // bot 圖內字 160%
  const geometry = computeIntradayGeometry(input);
  const dirColor = args.change > 0 ? THEME.bull : args.change < 0 ? THEME.bear : THEME.ink;
  const chartH = args.flags.volume ? TOTAL_H : CHART_H;
  const totalH = chartH + TITLE_H;
  const arrow = args.change > 0 ? "▲" : args.change < 0 ? "▾" : "—";

  return renderToStaticMarkup(
    createElement("svg", {
      xmlns: "http://www.w3.org/2000/svg",
      viewBox: `0 0 ${CHART_W} ${totalH}`,
      width: CHART_W,
      height: totalH,
    },
      createElement("rect", { x: 0, y: 0, width: CHART_W, height: totalH, fill: THEME.bg }),
      // 標題帶:左側代號+名稱(超長截斷),右側現價與漲跌;字級 150% = 33
      createElement("text", {
        x: 14, y: 40,
        fontSize: 33, fontFamily: FONT_FAMILY, fill: THEME.ink,
      }, fitTitle(args.symbol, args.name)),
      createElement("text", {
        x: CHART_W - 14, y: 40,
        fontSize: 33, textAnchor: "end", fontFamily: FONT_FAMILY, fill: dirColor,
      }, `${args.lastClose.toFixed(2)}  ${arrow}${Math.abs(args.change).toFixed(2)} (${args.changePct >= 0 ? "+" : ""}${args.changePct.toFixed(2)}%)`),
      // 圖表主體下移 TITLE_H,在標題帶下方
      createElement("g", { transform: `translate(0, ${TITLE_H})` },
        createElement(IntradayChartStatic, { ...input, geometry }),
      ),
    ),
  );
}

export function renderChartPng(args: IntradayChartInput & {
  symbol: string; name: string | null; lastClose: number; change: number; changePct: number;
}): Buffer {
  return svgToPng(buildChartSvg(args));
}

// 指數圖:精簡(無量子圖),沿用同一標題帶 + 160% scale + resvg 管線。
export function buildIndexChartSvg(args: IndexChartInput & {
  symbol: string; name: string | null; lastClose: number; change: number; changePct: number;
}): string {
  const input: IndexChartInput = { ...args, theme: THEME, scale: 1.6 };
  const geometry = computeIndexGeometry(input);
  const dirColor = args.change > 0 ? THEME.bull : args.change < 0 ? THEME.bear : THEME.ink;
  const totalH = CHART_H + TITLE_H; // 指數無量子圖
  const arrow = args.change > 0 ? "▲" : args.change < 0 ? "▾" : "—";

  return renderToStaticMarkup(
    createElement("svg", {
      xmlns: "http://www.w3.org/2000/svg",
      viewBox: `0 0 ${CHART_W} ${totalH}`,
      width: CHART_W,
      height: totalH,
    },
      createElement("rect", { x: 0, y: 0, width: CHART_W, height: totalH, fill: THEME.bg }),
      createElement("text", {
        x: 14, y: 40, fontSize: 33, fontFamily: FONT_FAMILY, fill: THEME.ink,
      }, fitTitle(args.symbol, args.name)),
      createElement("text", {
        x: CHART_W - 14, y: 40, fontSize: 33, textAnchor: "end", fontFamily: FONT_FAMILY, fill: dirColor,
      }, `${fmtIndex(args.lastClose)}  ${arrow}${Math.abs(args.change).toFixed(2)} (${args.changePct >= 0 ? "+" : ""}${args.changePct.toFixed(2)}%)`),
      createElement("g", { transform: `translate(0, ${TITLE_H})` },
        createElement(IndexIntradayStatic, { ...input, geometry }),
      ),
    ),
  );
}

export function renderIndexChartPng(args: IndexChartInput & {
  symbol: string; name: string | null; lastClose: number; change: number; changePct: number;
}): Buffer {
  return svgToPng(buildIndexChartSvg(args));
}

// SVG 字串 → PNG buffer 的共用 resvg 管線(分時圖 / 五檔圖共用)。
// 2x zoom = retina 解析度(點開清楚);feed 顯示尺寸由 Discord 控,與此無關。
function svgToPng(svg: string): Buffer {
  const resvg = new Resvg(svg, {
    fitTo: { mode: "zoom", value: 2 },
    font: {
      loadSystemFonts: true,
      defaultFontFamily: FONT_FAMILY,
      ...(EXTRA_FONTS.length > 0 ? { fontFiles: EXTRA_FONTS } : {}),
    },
  });
  return Buffer.from(resvg.render().asPng());
}

// 委買賣五檔獨立 PNG。跟即時 quote 同產、不走 loadSlow 30s 快取。
export function renderQuotePng(quote: QuoteResp): Buffer {
  const svg = renderToStaticMarkup(
    createElement(QuoteBookSvg, {
      bids: quote.bids,
      asks: quote.asks,
      isLimitUp: quote.is_limit_up_bid || quote.is_limit_up_ask,
      isLimitDown: quote.is_limit_down_bid || quote.is_limit_down_ask,
      theme: THEME,
    }),
  );
  return svgToPng(svg);
}

// renderChartPng 可能因 resvg／缺系統字型拋例外;包成 null 讓上層退純文字 embed(spec §8 不讓整則炸)
export function safeRender(render: () => Buffer): Buffer | null {
  try {
    return render();
  } catch (e) {
    console.warn("[bot] 產圖失敗,退純文字:", e);
    return null;
  }
}
