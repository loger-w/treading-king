import { describe, it, expect } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { QuoteBookSvg } from "./quote-book-svg";
import { formatTickPrice } from "./tick";

const render = (input: Parameters<typeof QuoteBookSvg>[0]) =>
  renderToStaticMarkup(createElement(QuoteBookSvg, input));

const base = { isLimitUp: false, isLimitDown: false };

describe("QuoteBookSvg", () => {
  it("含委買/委賣總量(加總)與五檔價量", () => {
    const svg = render({
      ...base,
      bids: [{ price: 384, size: 5 }, { price: 383.5, size: 1 }],
      asks: [{ price: 385, size: 13 }, { price: 386, size: 1 }],
    });
    expect(svg).toContain("6 張");                 // 委買總量 5+1
    expect(svg).toContain("14 張");                // 委賣總量 13+1
    expect(svg).toContain(formatTickPrice(384));   // 買1 價(="384.0")
    expect(svg).toContain(formatTickPrice(385));   // 賣1 價(="385.0")
  });

  it("price=0 顯示「市價」、缺檔補「—」", () => {
    const svg = render({
      ...base,
      bids: [{ price: 0, size: 0 }],               // 鎖停的市價單
      asks: [{ price: 385, size: 13 }],
    });
    expect(svg).toContain("市價");                  // price=0
    expect(svg).toContain("—");                     // 買2..買5、賣2..賣5 缺檔
  });

  it("鎖漲停 → 顯示 badge", () => {
    const svg = render({
      ...base, isLimitUp: true,
      bids: [{ price: 384, size: 5 }], asks: [{ price: 385, size: 13 }],
    });
    expect(svg).toContain("鎖漲停");
  });
});
