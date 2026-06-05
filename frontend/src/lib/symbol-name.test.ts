import { describe, expect, test } from "vitest";
import { resolveNameFromResults } from "./symbol-name";

// bug1:useSymbolNames 對「/api/symbols 前綴查沒回精確 match」的 symbol 也 cache.set(null),
// null 被當「已快取」永久不重試 → 分時走勢點該股名稱永久「—」。
// 修法:查無 match 回 undefined(不快取、可重試),等 symbols 快取載入後重打就會補上。
describe("resolveNameFromResults", () => {
  test("查到精確 match → 回該 name", () => {
    expect(resolveNameFromResults("2330", [{ symbol: "2330", name: "台積電" }])).toBe("台積電");
  });

  test("前綴查只回別的同前綴股(沒回自己)→ undefined,代表不該快取、可重試", () => {
    expect(resolveNameFromResults("2330", [{ symbol: "2331", name: "精英" }])).toBeUndefined();
  });

  test("空結果(symbols 快取還沒載入)→ undefined,等載入後重試而非永久 null", () => {
    expect(resolveNameFromResults("2330", [])).toBeUndefined();
  });

  test("DB 有此 symbol 但確實無名 → null(確定值,可快取、不需重試)", () => {
    expect(resolveNameFromResults("2330", [{ symbol: "2330", name: null }])).toBeNull();
  });
});
