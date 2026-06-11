import { useCallback, useEffect, useRef, useState } from "react";
import { type SignalEvent, type MXFCandle } from "../lib/api";

const RECONNECT_DELAYS_MS = [1000, 2000, 4000, 8000, 16000, 30000];

export type WSStatus = "connecting" | "open" | "closed";

export interface TickEvent {
  symbol: string;
  price: number;
  size: number;
  bid?: number;
  ask?: number;
}

// Module-level EventTarget — 跨 hook instance 共用同一個 WS tick stream
const tickBus = new EventTarget();

/**
 * 任意元件可 import 此 helper 訂閱 tick。
 * 回傳 unsubscribe function（呼叫即 detach）。
 */
export function subscribeTicks(handler: (t: TickEvent) => void): () => void {
  const fn = (ev: Event) => handler((ev as CustomEvent<TickEvent>).detail);
  tickBus.addEventListener("tick", fn);
  return () => tickBus.removeEventListener("tick", fn);
}

export interface MXFCandleEvent {
  symbol: string;
  candle: MXFCandle;
}

// Module-level EventTarget — 跨 hook instance 共用同一個 WS mxf_candle stream
const mxfCandleBus = new EventTarget();

/**
 * 任意元件可 import 此 helper 訂閱 MXF K 棒推送。
 * 回傳 unsubscribe function（呼叫即 detach）。
 */
export function subscribeMxfCandles(handler: (e: MXFCandleEvent) => void): () => void {
  const fn = (ev: Event) => handler((ev as CustomEvent<MXFCandleEvent>).detail);
  mxfCandleBus.addEventListener("mxf_candle", fn);
  return () => mxfCandleBus.removeEventListener("mxf_candle", fn);
}

// 群益委託回報:WS 一推就讓委託/部位 hook 重抓
const capitalOrderBus = new EventTarget();
export function subscribeCapitalOrders(handler: () => void): () => void {
  const fn = () => handler();
  capitalOrderBus.addEventListener("capital_order", fn);
  return () => capitalOrderBus.removeEventListener("capital_order", fn);
}

// 五檔點價 → 下單匣帶價
export interface OrderTicketHint { symbol: string | null; price: number; }
const orderTicketBus = new EventTarget();
export function emitOrderTicket(hint: OrderTicketHint): void {
  orderTicketBus.dispatchEvent(new CustomEvent<OrderTicketHint>("ticket", { detail: hint }));
}
export function subscribeOrderTicket(handler: (h: OrderTicketHint) => void): () => void {
  const fn = (ev: Event) => handler((ev as CustomEvent<OrderTicketHint>).detail);
  orderTicketBus.addEventListener("ticket", fn);
  return () => orderTicketBus.removeEventListener("ticket", fn);
}

interface ManagedWS {
  ws: WebSocket;
  reconnect: boolean;
  reconnectTimer: ReturnType<typeof setTimeout> | null;
}

export function useSignalsStream(opts?: {
  onSignal?: (s: SignalEvent["data"]) => void;
  onTick?: (symbol: string, price: number) => void;
}) {
  const [status, setStatus] = useState<WSStatus>("connecting");
  const [recent, setRecent] = useState<SignalEvent["data"][]>([]);
  const currentRef = useRef<ManagedWS | null>(null);
  const attemptRef = useRef(0);
  const onSignalRef = useRef(opts?.onSignal);
  const onTickRef = useRef(opts?.onTick);

  useEffect(() => { onSignalRef.current = opts?.onSignal; }, [opts?.onSignal]);
  useEffect(() => { onTickRef.current = opts?.onTick; }, [opts?.onTick]);

  const connect = useCallback(() => {
    const apiKey = (import.meta.env.VITE_BFF_API_KEY ?? "") as string;
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${location.host}/ws/realtime?api_key=${encodeURIComponent(apiKey)}`;
    setStatus("connecting");

    const ws = new WebSocket(url);
    // 這個 ws 自己的 state — onclose 從 closure 抓 managed，cleanup 在外面把它的
    // reconnect 設 false 就能擋掉自身的 reconnect，不會被後一個 mount 的 ws 影響。
    const managed: ManagedWS = { ws, reconnect: true, reconnectTimer: null };
    currentRef.current = managed;

    ws.onopen = () => {
      setStatus("open");
      attemptRef.current = 0;
    };

    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.event === "signal") {
          const data = msg.data as SignalEvent["data"];
          setRecent((prev) => [data, ...prev].slice(0, 50));
          onSignalRef.current?.(data);
        } else if (msg.event === "tick") {
          const tick: TickEvent = {
            symbol: msg.data.symbol,
            price: msg.data.price,
            size: msg.data.size ?? 0,
            bid: typeof msg.data.bid === "number" ? msg.data.bid : undefined,
            ask: typeof msg.data.ask === "number" ? msg.data.ask : undefined,
          };
          onTickRef.current?.(tick.symbol, tick.price);
          tickBus.dispatchEvent(new CustomEvent<TickEvent>("tick", { detail: tick }));
        } else if (msg.event === "mxf_candle") {
          const evt: MXFCandleEvent = { symbol: msg.data.symbol, candle: msg.data.candle };
          mxfCandleBus.dispatchEvent(new CustomEvent<MXFCandleEvent>("mxf_candle", { detail: evt }));
        } else if (msg.event === "capital_order") {
          capitalOrderBus.dispatchEvent(new Event("capital_order"));
        } else if (msg.event === "capital_position") {
          // 庫存查詢完成 → 同一個 bus 讓委託/部位 hook 重抓(語意=群益狀態變了)
          capitalOrderBus.dispatchEvent(new Event("capital_order"));
        }
      } catch { /* ignore */ }
    };

    ws.onclose = () => {
      setStatus("closed");
      if (!managed.reconnect) return;
      const delay = RECONNECT_DELAYS_MS[Math.min(attemptRef.current, RECONNECT_DELAYS_MS.length - 1)];
      attemptRef.current += 1;
      managed.reconnectTimer = setTimeout(() => {
        managed.reconnectTimer = null;
        connect();
      }, delay);
    };

    ws.onerror = () => { /* close 會跟著觸發 */ };
  }, []);

  useEffect(() => {
    connect();
    return () => {
      const managed = currentRef.current;
      if (managed) {
        managed.reconnect = false;
        if (managed.reconnectTimer) {
          clearTimeout(managed.reconnectTimer);
          managed.reconnectTimer = null;
        }
        try { managed.ws.close(); } catch { /* ignore */ }
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { status, recent };
}
