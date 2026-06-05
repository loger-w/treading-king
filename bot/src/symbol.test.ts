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
