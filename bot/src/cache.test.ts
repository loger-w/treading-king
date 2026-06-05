import { describe, it, expect, vi } from "vitest";
import { TtlCache } from "./cache";

describe("TtlCache", () => {
  it("TTL 內回快取、過期後重抓", async () => {
    let t = 1000;
    const cache = new TtlCache<number>(30_000, () => t);
    const loader = vi.fn(async () => t);
    expect(await cache.get("2330", loader)).toBe(1000);
    t = 21_000; // +20s,仍在 30s TTL 內
    expect(await cache.get("2330", loader)).toBe(1000); // 命中,不重抓
    expect(loader).toHaveBeenCalledTimes(1);
    t = 32_000; // +31s 過期
    expect(await cache.get("2330", loader)).toBe(32_000);
    expect(loader).toHaveBeenCalledTimes(2);
  });
});
