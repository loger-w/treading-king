import { describe, it, expect } from "vitest";
import { resolveIndexAlias, isIndexCode, indexName, indexMeta, INDEX_SYMBOLS } from "./index-symbols";

describe("index-symbols", () => {
  it("加權/大盤 → IX0001", () => {
    expect(resolveIndexAlias("加權")).toBe("IX0001");
    expect(resolveIndexAlias("大盤")).toBe("IX0001");
  });
  it("櫃買/上櫃 → IX0043", () => {
    expect(resolveIndexAlias("櫃買")).toBe("IX0043");
    expect(resolveIndexAlias("上櫃")).toBe("IX0043");
  });
  it("去前後空白", () => expect(resolveIndexAlias(" 加權 ")).toBe("IX0001"));
  it("未知 → null", () => {
    expect(resolveIndexAlias("台積電")).toBeNull();
    expect(resolveIndexAlias("2330")).toBeNull();
  });
  it("isIndexCode", () => {
    expect(isIndexCode("IX0001")).toBe(true);
    expect(isIndexCode("2330")).toBe(false);
  });
  it("indexName / indexMeta", () => {
    expect(indexName("IX0043")).toBe("櫃買指數");
    expect(indexName("2330")).toBeNull();
    expect(indexMeta("IX0001")?.color).toBe("#f0b429");
  });
  it("INDEX_SYMBOLS 含兩檔", () => expect(INDEX_SYMBOLS).toHaveLength(2));
});
