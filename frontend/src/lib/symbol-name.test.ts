import { describe, expect, test } from "vitest";
import { buildSymbolNames, resolveNameFromResults } from "./symbol-name";

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

// bug3:chart header / 觸發列表的 symbolNames 只合併「書籤回報 + 觸發解析」兩個來源,
// 漏掉監聽清單。→ 在監聽、但不在書籤、且當日未觸發的純監聽股(實測 4989 榮科),
// header 名稱永久「—」。修法:把 monitorItems 的 name 也併入。
describe("buildSymbolNames", () => {
  test("純監聽股(不在書籤、未觸發)的 name 要被涵蓋", () => {
    const r = buildSymbolNames({}, [{ symbol: "4989", name: "榮科" }], {});
    expect(r["4989"]).toBe("榮科");
  });

  test("書籤/搜尋來源優先於監聽(同 symbol 名稱以書籤為準)", () => {
    const r = buildSymbolNames(
      {},
      [{ symbol: "2330", name: "台積電-監聽舊名" }],
      { "2330": "台積電" },
    );
    expect(r["2330"]).toBe("台積電");
  });

  test("觸發解析來源仍涵蓋", () => {
    const r = buildSymbolNames({ "1785": "光洋科" }, [], {});
    expect(r["1785"]).toBe("光洋科");
  });

  test("書籤 name=null 不可蓋掉低優先來源已知的真名(null 不是「明確命名」)", () => {
    // 系統書籤 capture 時可能沒帶名稱(後端 symbols 快取未載入 → name=None),
    // null 蓋掉監聽/解析的真名會讓 header 永久「—」(Monitor 用 in 檢查不會補查)
    const r = buildSymbolNames(
      { "2330": "台積電" },
      [{ symbol: "3357", name: "臺慶科" }],
      { "2330": null, "3357": null, "9999": null },
    );
    expect(r["2330"]).toBe("台積電");
    expect(r["3357"]).toBe("臺慶科");
    expect(r["9999"]).toBeNull();  // 全來源皆無名才留 null
  });
});
