import { describe, it, expect } from "vitest";
import {
  parseSignalPayload, formatBanner, withBanner, handleSignalPush,
  type SignalPayload, type PushDeps,
} from "./signal";
import type { BaseMessageOptions } from "discord.js";

const base: SignalPayload = {
  symbol: "2330", rule_name: "漲停打開碰CDP", price: 600.5, volume: 1234,
  triggered_at: "2026-06-08T05:30:00+00:00",
  cdp_touch: { level: "AH", role: "support", touch_index: 2 },
  ma_touch: null,
};

describe("parseSignalPayload", () => {
  it("完整 payload → 物件", () => {
    const p = parseSignalPayload({ ...base });
    expect(p).not.toBeNull();
    expect(p!.symbol).toBe("2330");
    expect(p!.cdp_touch?.level).toBe("AH");
  });
  it("缺必填(無 symbol)→ null", () => {
    expect(parseSignalPayload({ ...base, symbol: undefined })).toBeNull();
  });
  it("price 非數字 → null", () => {
    expect(parseSignalPayload({ ...base, price: "600" })).toBeNull();
  });
  it("triggered_at 非法日期字串 → null", () => {
    expect(parseSignalPayload({ ...base, triggered_at: "not-a-date" })).toBeNull();
  });
  it("非物件 → null", () => {
    expect(parseSignalPayload("nope")).toBeNull();
    expect(parseSignalPayload(null)).toBeNull();
  });
  it("touch 殘缺(無 level)→ 該 touch 視為 null", () => {
    const p = parseSignalPayload({ ...base, cdp_touch: { role: "support" } });
    expect(p!.cdp_touch).toBeNull();
  });
  it("帶 name → 解析出;不帶 name 仍 valid(name undefined)", () => {
    expect(parseSignalPayload({ ...base, name: "台積電" })!.name).toBe("台積電");
    expect(parseSignalPayload({ ...base })!.name).toBeUndefined();
  });
});

describe("formatBanner", () => {
  it("含 cdp_touch:規則名/觸發價/台北時間/碰CDP行", () => {
    const b = formatBanner(base);
    expect(b).toContain("漲停打開碰CDP");
    expect(b).toContain("600.5");
    expect(b).toMatch(/13:30:00/);            // 05:30 UTC → 13:30 台北(+08:00)
    expect(b).toContain("碰 CDP AH");
    expect(b).toContain("支撐");
    expect(b).toContain("第2次");
  });
  it("ma_touch 的 sma_5 → 顯示 MA5", () => {
    const b = formatBanner({ ...base, cdp_touch: null, ma_touch: { level: "sma_5", role: "resistance" } });
    expect(b).toContain("碰 MA MA5");
    expect(b).toContain("壓力");
  });
  it("touch 只有 touch_index、無 role → 不出現孤兒分隔符", () => {
    const b = formatBanner({ ...base, cdp_touch: { level: "AH", touch_index: 1 }, ma_touch: null });
    expect(b).toContain("碰 CDP AH（第1次）");
    expect(b).not.toContain("（·");
  });
  it("無 cdp/ma → 只有標題一行", () => {
    const b = formatBanner({ ...base, cdp_touch: null, ma_touch: null });
    expect(b.split("\n")).toHaveLength(1);
  });
  it("有 name → 第一行含「名稱 代號」", () => {
    const b = formatBanner({ ...base, name: "台積電" });
    expect(b).toContain("台積電 2330");
  });
  it("無 name(期貨/查不到)→ 第一行只含代號,不出現 undefined / 多餘前綴空格", () => {
    const b = formatBanner({ ...base, name: null });
    expect(b).toContain("｜ 2330 ｜");
    expect(b).not.toContain("undefined");
  });
});

describe("withBanner", () => {
  it("第一則只有 embeds → 加 content(橫幅)", () => {
    const m: BaseMessageOptions = { embeds: [] };
    const out = withBanner(m, "BANNER");
    expect(out.content).toBe("BANNER");
    expect(out.embeds).toBe(m.embeds);
  });
  it("第一則本身有 content → 橫幅疊上方、換行接原文", () => {
    const out = withBanner({ content: "原文" }, "BANNER");
    expect(out.content).toBe("BANNER\n原文");
  });
});

describe("handleSignalPush", () => {
  const msgs: BaseMessageOptions[] = [{ embeds: [] }, { files: [] }];
  function makeDeps(over: Partial<PushDeps> = {}) {
    const sent: BaseMessageOptions[] = [];
    const deps: PushDeps = {
      channelConfigured: true,
      buildSymbolMessages: async () => msgs.map((m) => ({ ...m })),
      sendToChannel: async (m) => { sent.push(m); },
      ...over,
    };
    return { deps, sent };
  }
  it("頻道未設 → 不送", async () => {
    const { deps, sent } = makeDeps({ channelConfigured: false });
    await handleSignalPush(base, deps);
    expect(sent).toHaveLength(0);
  });
  it("頻道有設 → 送全部,第一則帶橫幅", async () => {
    const { deps, sent } = makeDeps();
    await handleSignalPush(base, deps);
    expect(sent).toHaveLength(2);
    expect(sent[0].content).toContain("漲停打開碰CDP");
  });
  it("buildSymbolMessages 回空 → 不送", async () => {
    const { deps, sent } = makeDeps({ buildSymbolMessages: async () => [] });
    await handleSignalPush(base, deps);
    expect(sent).toHaveLength(0);
  });
  it("buildSymbolMessages 拋錯(後端不在/重啟)→ 仍送一則含橫幅的純文字,不漏訊號", async () => {
    const { deps, sent } = makeDeps({ buildSymbolMessages: async () => { throw new Error("fetch failed"); } });
    await handleSignalPush(base, deps);
    expect(sent).toHaveLength(1);
    expect(sent[0].content).toContain("漲停打開碰CDP");        // 橫幅(規則名)仍送出
    expect(sent[0].content).toContain("圖卡資料暫時無法取得");  // 標明降級原因
  });
});
