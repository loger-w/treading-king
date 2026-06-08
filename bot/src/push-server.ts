import { createServer, type IncomingMessage, type ServerResponse, type Server } from "node:http";
import type { Client, BaseMessageOptions } from "discord.js";
import { config } from "./config";
import { parseSignalPayload, handleSignalPush, type SignalPayload } from "./signal";
import { buildSymbolMessages } from "./messages";

export interface PushServerHandlers {
  onSignal: (payload: SignalPayload) => void;   // fire-and-forget,在回 202 之後
}

// HTTP 殼:純路由 + parse + 回 202 + 把 payload 丟給 onSignal。不碰 discord client → 好測。
export function createPushServer(handlers: PushServerHandlers): Server {
  return createServer((req, res) => { void route(req, res, handlers); });
}

function readJsonBody(req: IncomingMessage): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    let size = 0;
    let tooLarge = false;
    req.on("data", (c: Buffer) => {
      if (tooLarge) return;            // 超限後停止累積,讓 socket 自然 drain 到 end(避免 destroy 後寫不出乾淨的 400)
      size += c.length;
      if (size > 64 * 1024) { tooLarge = true; return; }
      chunks.push(c);
    });
    req.on("end", () => {
      if (tooLarge) { reject(new Error("body too large")); return; }
      try { resolve(JSON.parse(Buffer.concat(chunks).toString("utf8"))); }
      catch { reject(new Error("invalid json")); }
    });
    req.on("error", reject);
  });
}

async function route(req: IncomingMessage, res: ServerResponse, handlers: PushServerHandlers): Promise<void> {
  if (req.method !== "POST") { res.writeHead(405).end(); return; }
  if (req.url !== "/push-signal") { res.writeHead(404).end(); return; }
  if (!String(req.headers["content-type"] ?? "").includes("application/json")) { res.writeHead(415).end(); return; }
  let body: unknown;
  try { body = await readJsonBody(req); } catch { res.writeHead(400).end(); return; }
  const payload = parseSignalPayload(body);
  if (!payload) { res.writeHead(400).end(); return; }
  res.writeHead(202).end();          // 立刻 ACK,渲圖+送出走背景
  handlers.onSignal(payload);
}

// 真正的 dispatch:解析目標頻道 → 委派 handleSignalPush。client.login 後由 startPushServer 接上。
async function dispatch(client: Client, payload: SignalPayload): Promise<void> {
  try {
    const id = config.signalsChannelId;
    let channel: { send: (m: BaseMessageOptions) => Promise<unknown> } | null = null;
    if (id) {
      const ch = await client.channels.fetch(id).catch(() => null);
      if (ch && "send" in ch) {
        channel = ch as unknown as { send: (m: BaseMessageOptions) => Promise<unknown> };
      } else {
        console.warn(`[bot] 訊號頻道抓不到或不可發送:${id}`);
      }
    }
    await handleSignalPush(payload, {
      channelConfigured: channel !== null,
      buildSymbolMessages,
      sendToChannel: async (m) => { if (!channel) return; await channel.send(m); },  // channelConfigured 已保護,這層防 signal.ts 日後改動
    });
  } catch (e) {
    console.warn(`[bot] 訊號推播失敗:${payload.symbol} / ${payload.rule_name}`, e);
  }
}

// startup 用:綁 client + 只聽 127.0.0.1。
export function startPushServer(client: Client): void {
  const server = createPushServer({ onSignal: (p) => void dispatch(client, p) });
  server.on("error", (e) => console.error("[bot] push-server 錯誤:", e));
  server.listen(config.pushPort, "127.0.0.1", () =>
    console.log(`[bot] push-server 監聽 127.0.0.1:${config.pushPort}`));
}
