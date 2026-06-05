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

  it("恰好 TTL 邊界(嚴格 <)→ 過期 reload", async () => {
    let t = 1000;
    const cache = new TtlCache<number>(30_000, () => t);
    const loader = vi.fn(async () => t);
    await cache.get("X", loader);   // at=1000
    t = 31_000;                     // diff=30000,非 < 30000 → miss
    expect(await cache.get("X", loader)).toBe(31_000);
    expect(loader).toHaveBeenCalledTimes(2);
  });

  it("不同 key 各自獨立 TTL", async () => {
    let t = 1000;
    const cache = new TtlCache<number>(30_000, () => t);
    const la = vi.fn(async () => 100);
    const lb = vi.fn(async () => 200);
    expect(await cache.get("A", la)).toBe(100);
    t = 20_000;
    expect(await cache.get("B", lb)).toBe(200); // B 首次
    expect(await cache.get("A", la)).toBe(100); // A 仍命中
    expect(la).toHaveBeenCalledTimes(1);
    expect(lb).toHaveBeenCalledTimes(1);
  });
});
