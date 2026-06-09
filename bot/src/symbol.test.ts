import { describe, it, expect } from "vitest";
import { parseSymbolCommand } from "./symbol";

describe("parseSymbolCommand", () => {
  it.each(["p2330", "P2330", "p0050", "p00878", "p2330B"])("命中 %s", (msg) => {
    expect(parseSymbolCommand(msg)).toBe(msg.slice(1).toUpperCase());
  });
  it.each(["people", "2330", "p12", "p2330 走勢", "hello p2330", ""])("不命中 %s", (msg) => {
    expect(parseSymbolCommand(msg)).toBeNull();
  });
});

describe("parseSymbolCommand — 指數中文別名", () => {
  it("加權/大盤 → IX0001、櫃買/上櫃 → IX0043", () => {
    expect(parseSymbolCommand("p加權")).toBe("IX0001");
    expect(parseSymbolCommand("p大盤")).toBe("IX0001");
    expect(parseSymbolCommand("p櫃買")).toBe("IX0043");
    expect(parseSymbolCommand("p上櫃")).toBe("IX0043");
  });
  it("p + 未知中文 → null、無 p 前綴 → null", () => {
    expect(parseSymbolCommand("p亂碼")).toBeNull();
    expect(parseSymbolCommand("加權")).toBeNull();
  });
});
