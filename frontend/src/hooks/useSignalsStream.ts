import { useCallback, useEffect, useRef, useState } from "react";
import { type SignalEvent } from "../lib/api";

const RECONNECT_DELAYS_MS = [1000, 2000, 4000, 8000, 16000, 30000];

export type WSStatus = "connecting" | "open" | "closed";

export function useSignalsStream(opts?: {
  onSignal?: (s: SignalEvent["data"]) => void;
  onTick?: (symbol: string, price: number) => void;  // 預留：未來 backend 廣播 tick 給 chart 用
}) {
  const [status, setStatus] = useState<WSStatus>("connecting");
  const [recent, setRecent] = useState<SignalEvent["data"][]>([]);
  const wsRef = useRef<WebSocket | null>(null);
  const attemptRef = useRef(0);
  const onSignalRef = useRef(opts?.onSignal);
  const onTickRef = useRef(opts?.onTick);

  useEffect(() => { onSignalRef.current = opts?.onSignal; }, [opts?.onSignal]);
  useEffect(() => { onTickRef.current = opts?.onTick; }, [opts?.onTick]);

  const connect = useCallback(() => {
    setStatus("connecting");
    const apiKey = (import.meta.env.VITE_BFF_API_KEY ?? "") as string;
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${location.host}/ws/realtime?api_key=${encodeURIComponent(apiKey)}`;
    const ws = new WebSocket(url);
    wsRef.current = ws;

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
          onTickRef.current?.(msg.data.symbol, msg.data.price);
        }
      } catch { /* ignore */ }
    };

    ws.onclose = () => {
      setStatus("closed");
      const delay = RECONNECT_DELAYS_MS[Math.min(attemptRef.current, RECONNECT_DELAYS_MS.length - 1)];
      attemptRef.current += 1;
      setTimeout(connect, delay);
    };

    ws.onerror = () => { /* close 會跟著觸發 */ };
  }, []);

  useEffect(() => {
    connect();
    return () => {
      wsRef.current?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { status, recent };
}
