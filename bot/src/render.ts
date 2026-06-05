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

// Windows 本機一定有 Microsoft JhengHei(正黑體),可同時顯示中文與數字。
// 若 bot/assets/ 下有自帶字型則額外掛入並優先使用(選填,沒有也能跑)。
const ASSETS_DIR = join(dirname(fileURLToPath(import.meta.url)), "../assets");
const TITLE_H = 44;

// 字型 family 名稱 — 對齊 loadSystemFonts + defaultFontFamily 設定
const FONT_FAMILY = "Microsoft JhengHei";

// INTRADAY_THEME 預設 fontFamily 是 "Inter Tight, system-ui, sans-serif"。
// Override 成 Windows CJK 字型,確保 resvg 能正確 render 中文與數字。
const THEME = { ...INTRADAY_THEME, fontFamily: `"${FONT_FAMILY}", sans-serif` };

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

export function renderChartPng(args: IntradayChartInput & {
  symbol: string; name: string | null; lastClose: number; change: number; changePct: number;
}): Buffer {
  const input: IntradayChartInput = { ...args, theme: THEME };
  const geometry = computeIntradayGeometry(input);
  const dirColor = args.change > 0 ? THEME.bull : args.change < 0 ? THEME.bear : THEME.ink;
  const chartH = args.flags.volume ? TOTAL_H : CHART_H;
  const totalH = chartH + TITLE_H;
  const arrow = args.change > 0 ? "▲" : args.change < 0 ? "▾" : "—";

  const svg = renderToStaticMarkup(
    createElement("svg", {
      xmlns: "http://www.w3.org/2000/svg",
      viewBox: `0 0 ${CHART_W} ${totalH}`,
      width: CHART_W,
      height: totalH,
    },
      createElement("rect", { x: 0, y: 0, width: CHART_W, height: totalH, fill: THEME.bg }),
      // 標題帶:左側股票代號 + 名稱,右側現價與漲跌
      createElement("text", {
        x: 14, y: 30,
        fontSize: 22, fontFamily: FONT_FAMILY, fill: THEME.ink,
      }, `${args.symbol}${args.name ? " " + args.name : ""}`),
      createElement("text", {
        x: CHART_W - 14, y: 30,
        fontSize: 22, textAnchor: "end", fontFamily: FONT_FAMILY, fill: dirColor,
      }, `${args.lastClose.toFixed(2)}  ${arrow}${Math.abs(args.change).toFixed(2)} (${args.changePct >= 0 ? "+" : ""}${args.changePct.toFixed(2)}%)`),
      // 圖表主體下移 TITLE_H,在標題帶下方
      createElement("g", { transform: `translate(0, ${TITLE_H})` },
        createElement(IntradayChartStatic, { ...input, geometry }),
      ),
    ),
  );

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
