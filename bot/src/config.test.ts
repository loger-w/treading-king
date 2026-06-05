import { describe, it, expect } from "vitest";
import { config, requireToken } from "./config";

describe("config — 載入無副作用(review #4)", () => {
  it("token 不放進 config 物件(改 lazy,避免 import 時 required→process.exit)", () => {
    expect(config).not.toHaveProperty("token");
    expect(config.backendBaseUrl).toBeTruthy();
  });

  it("requireToken 缺 token 時 throw(startup 才驗、不靜默退出)", () => {
    const saved = process.env.DISCORD_BOT_TOKEN;
    delete process.env.DISCORD_BOT_TOKEN;
    try {
      expect(() => requireToken()).toThrow(/DISCORD_BOT_TOKEN/);
    } finally {
      if (saved !== undefined) process.env.DISCORD_BOT_TOKEN = saved;
    }
  });

  it("requireToken 有 token 時回傳該值", () => {
    const saved = process.env.DISCORD_BOT_TOKEN;
    process.env.DISCORD_BOT_TOKEN = "test-token-123";
    try {
      expect(requireToken()).toBe("test-token-123");
    } finally {
      if (saved !== undefined) process.env.DISCORD_BOT_TOKEN = saved;
      else delete process.env.DISCORD_BOT_TOKEN;
    }
  });
});
