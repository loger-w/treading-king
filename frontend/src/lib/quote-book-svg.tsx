// 委買賣五檔的共用畫圖層 — bot 產圖用(網頁 QuoteBook.tsx 維持 Tailwind 版不動)。
// 顏色一律 inline hex(resvg 不解析 Tailwind / CSS var);landscape 大字,
// 讓 Discord feed 把圖縮到欄寬後仍免點開可讀。
import { createElement, Fragment } from "react";
import { formatTickPrice } from "./tick";
import { INTRADAY_THEME, type ChartTheme } from "./intraday-chart-svg";

export const QUOTE_W = 720;
export const QUOTE_H = 300;

export interface QuoteBookLevel { price: number; size: number; }
export interface QuoteBookSvgInput {
  bids: QuoteBookLevel[];
  asks: QuoteBookLevel[];
  isLimitUp: boolean;
  isLimitDown: boolean;
  theme?: ChartTheme;
}

const PAD = 24;
const MID_X = QUOTE_W / 2;          // 360 — 買賣分隔中線
const COL_GAP = 12;                 // 中線兩側留白
const LEFT_R = MID_X - COL_GAP;     // 348 左欄右界(價靠中)
const RIGHT_L = MID_X + COL_GAP;    // 372 右欄左界
const COL_W = LEFT_R - PAD;         // 324 單欄寬(量條 normalize 用)
const ROWS_TOP = 104;
const ROW_H = 36;
const BAR_H = 24;
// 容量核查:ROWS_TOP + 5*ROW_H = 284 ≤ QUOTE_H(300);改上面常數時別讓五檔區超出畫布

export function QuoteBookSvg(input: QuoteBookSvgInput) {
  const t = input.theme ?? INTRADAY_THEME;
  const bids = input.bids.slice(0, 5);
  const asks = input.asks.slice(0, 5);
  const maxQty = Math.max(1, ...bids.map((b) => b.size), ...asks.map((a) => a.size));
  const bidTotal = bids.reduce((s, b) => s + b.size, 0);
  const askTotal = asks.reduce((s, a) => s + a.size, 0);

  // price=0 是鎖漲跌停的市價單;缺檔(undefined)補 —
  const priceCell = (lv: QuoteBookLevel | undefined) =>
    lv ? (lv.price === 0 ? "市價" : formatTickPrice(lv.price)) : "—";
  const qtyCell = (lv: QuoteBookLevel | undefined) =>
    lv && lv.size > 0 ? String(lv.size) : "—";

  const num = { fontVariantNumeric: "tabular-nums" as const };

  return createElement("svg", {
      xmlns: "http://www.w3.org/2000/svg",
      viewBox: `0 0 ${QUOTE_W} ${QUOTE_H}`, width: QUOTE_W, height: QUOTE_H,
    },
    createElement("rect", { x: 0, y: 0, width: QUOTE_W, height: QUOTE_H, fill: t.bg }),

    // ── 抬頭 + 鎖漲跌停 badge ──
    createElement("text", {
      x: PAD, y: 34, fontSize: 22, fontFamily: t.fontFamily, fill: t.ink,
      style: { fontWeight: 700 },
    }, "委買賣 五檔"),
    input.isLimitUp && createElement("text", {
      x: QUOTE_W - PAD, y: 34, fontSize: 18, textAnchor: "end",
      fontFamily: t.fontFamily, fill: t.bull,
    }, "🔺 鎖漲停"),
    input.isLimitDown && createElement("text", {
      x: QUOTE_W - PAD, y: 34, fontSize: 18, textAnchor: "end",
      fontFamily: t.fontFamily, fill: t.bear,
    }, "🔻 鎖跌停"),
    createElement("line", { x1: PAD, y1: 46, x2: QUOTE_W - PAD, y2: 46, stroke: t.line, strokeWidth: 1 }),

    // ── 委買總量(左紅)/ 委賣總量(右綠)大字 ──
    createElement("text", {
      x: PAD, y: 84, fontSize: 28, fontFamily: t.fontFamily, fill: t.bull,
      style: { ...num, fontWeight: 700 },
    }, `${bidTotal} 張`),
    createElement("text", {
      x: QUOTE_W - PAD, y: 84, fontSize: 28, textAnchor: "end", fontFamily: t.fontFamily, fill: t.bear,
      style: { ...num, fontWeight: 700 },
    }, `${askTotal} 張`),

    // ── 五檔列 × 5(左買右賣,最佳價在最上,量條由中線往外) ──
    ...Array.from({ length: 5 }).map((_, i) => {
      const bid = bids[i];
      const ask = asks[i];
      const y = ROWS_TOP + i * ROW_H;
      const barY = y + (ROW_H - BAR_H) / 2;
      const textY = y + ROW_H / 2 + 6;
      const bidW = bid && bid.size > 0 ? (bid.size / maxQty) * COL_W : 0;
      const askW = ask && ask.size > 0 ? (ask.size / maxQty) * COL_W : 0;
      return createElement(Fragment, { key: i },
        // 買量條:靠中線(LEFT_R)往左長
        bidW > 0 && createElement("rect", {
          x: LEFT_R - bidW, y: barY, width: bidW, height: BAR_H,
          fill: t.bull, fillOpacity: "0.15",
        }),
        // 買量(左)/ 買價(靠中,紅)
        createElement("text", { x: PAD, y: textY, fontSize: 18, fill: t.inkMuted, fontFamily: t.fontFamily, style: num }, qtyCell(bid)),
        createElement("text", { x: LEFT_R, y: textY, fontSize: 18, textAnchor: "end", fill: t.bull, fontFamily: t.fontFamily, style: { ...num, fontWeight: 500 } }, priceCell(bid)),
        // 賣量條:靠中線(RIGHT_L)往右長
        askW > 0 && createElement("rect", {
          x: RIGHT_L, y: barY, width: askW, height: BAR_H,
          fill: t.bear, fillOpacity: "0.15",
        }),
        // 賣價(靠中,綠)/ 賣量(右)
        createElement("text", { x: RIGHT_L, y: textY, fontSize: 18, fill: t.bear, fontFamily: t.fontFamily, style: { ...num, fontWeight: 500 } }, priceCell(ask)),
        createElement("text", { x: QUOTE_W - PAD, y: textY, fontSize: 18, textAnchor: "end", fill: t.inkMuted, fontFamily: t.fontFamily, style: num }, qtyCell(ask)),
      );
    }),
  );
}
