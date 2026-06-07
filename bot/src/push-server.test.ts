import { describe, it, expect, afterEach } from "vitest";
import type { AddressInfo } from "node:net";
import type { Server } from "node:http";
import { createPushServer } from "./push-server";
import type { SignalPayload } from "./signal";

let server: Server | null = null;
afterEach(() => { server?.close(); server = null; });

async function start(onSignal: (p: SignalPayload) => void): Promise<number> {
  server = createPushServer({ onSignal });
  await new Promise<void>((r) => server!.listen(0, "127.0.0.1", () => r()));
  return (server!.address() as AddressInfo).port;
}

const VALID = { symbol: "2330", rule_name: "r", price: 1, volume: 1, triggered_at: "2026-06-08T05:30:00+00:00" };
const json = { "content-type": "application/json" };

describe("push-server HTTP 殼", () => {
  it("非 POST → 405", async () => {
    const port = await start(() => {});
    expect((await fetch(`http://127.0.0.1:${port}/push-signal`)).status).toBe(405);
  });
  it("路徑錯 → 404", async () => {
    const port = await start(() => {});
    const res = await fetch(`http://127.0.0.1:${port}/nope`, { method: "POST", headers: json, body: "{}" });
    expect(res.status).toBe(404);
  });
  it("非 JSON content-type → 415", async () => {
    const port = await start(() => {});
    const res = await fetch(`http://127.0.0.1:${port}/push-signal`, { method: "POST", headers: { "content-type": "text/plain" }, body: "{}" });
    expect(res.status).toBe(415);
  });
  it("壞 JSON → 400", async () => {
    const port = await start(() => {});
    const res = await fetch(`http://127.0.0.1:${port}/push-signal`, { method: "POST", headers: json, body: "{bad" });
    expect(res.status).toBe(400);
  });
  it("body 太大 → 400", async () => {
    const port = await start(() => {});
    const res = await fetch(`http://127.0.0.1:${port}/push-signal`, {
      method: "POST", headers: json, body: "x".repeat(65 * 1024),
    });
    expect(res.status).toBe(400);
  });
  it("payload 缺欄位 → 400", async () => {
    const port = await start(() => {});
    const res = await fetch(`http://127.0.0.1:${port}/push-signal`, { method: "POST", headers: json, body: JSON.stringify({ symbol: "2330" }) });
    expect(res.status).toBe(400);
  });
  it("合法 → 202 且 onSignal 收到 payload", async () => {
    const got: SignalPayload[] = [];
    const port = await start((p) => got.push(p));
    const res = await fetch(`http://127.0.0.1:${port}/push-signal`, { method: "POST", headers: json, body: JSON.stringify(VALID) });
    expect(res.status).toBe(202);
    expect(got).toHaveLength(1);
    expect(got[0].symbol).toBe("2330");
  });
});
