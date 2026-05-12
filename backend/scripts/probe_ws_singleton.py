"""Probe: 富邦 SDK WebSocket client singleton + 單條 sub 容量

驗 Phase 3 ws_pool 架構假設（services/fubon_ws.py 踩雷 #4）：
1. ws_client.stock 取兩次 id() 比對 → 是否 singleton
2. 單條 ws subscribe 250 個 symbol 看富邦回什麼（ack / error / 上限）
3. 給 services/fubon_ws.py 架構建議

注意：盤後跑 — 可能會看到 disconnect event，正常；subscribe RPC 仍能下達。
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from collections import Counter
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from dotenv import load_dotenv

backend = Path(__file__).resolve().parent.parent
load_dotenv(backend / ".env")
sys.path.insert(0, str(backend))

from services.fubon_client import get_fubon  # noqa: E402
from services.supabase_client import get_supabase  # noqa: E402


def _init_supabase_sync() -> None:
    """probe 是獨立 script 沒走 lifespan，要手動 init supabase。"""
    sb = get_supabase()
    sb.init()
    if sb.status.value != "ok":
        raise RuntimeError(f"supabase init failed: {sb.last_error}")


N_SYMBOLS = 250
WAIT_SECONDS = 10


async def fetch_symbols(n: int) -> list[str]:
    """從 symbols 表抓 active + 4 位純數字 + 非 ETF 的前 n 檔。"""
    sb = get_supabase()
    if sb.status.value != "ok" or sb.client is None:
        raise RuntimeError(f"supabase not OK: {sb.last_error}")
    res = await asyncio.to_thread(
        lambda: sb.client.table("symbols")
        .select("symbol")
        .eq("is_active", True)
        .eq("is_etf", False)
        .order("symbol")
        .limit(n * 2)  # 多抓一點，filter 後切到 n
        .execute()
    )
    syms = [
        r["symbol"]
        for r in (res.data or [])
        if r["symbol"].isdigit() and len(r["symbol"]) == 4
    ]
    return syms[:n]


async def main() -> None:
    print("=" * 64)
    print("Phase 3 SDK WS singleton + capacity probe")
    print("=" * 64)

    # ---- 1. fubon init ----
    print("\n[1] fubon.init() ...")
    fubon = get_fubon()
    await fubon.init()
    if fubon.status.value != "ok":
        print(f"  FAIL: fubon not OK: {fubon.last_error}")
        sys.exit(1)
    print(f"  OK status={fubon.status.value}")

    # ---- 2. ws_client.stock id() 比對 ----
    print("\n[2] ws_client.stock 取兩次比對 id()")
    ws1 = fubon.sdk.marketdata.websocket_client.stock
    ws2 = fubon.sdk.marketdata.websocket_client.stock
    is_singleton = id(ws1) is id(ws2) or id(ws1) == id(ws2)
    print(f"  id(ws1) = {id(ws1):#x}")
    print(f"  id(ws2) = {id(ws2):#x}")
    print(f"  type    = {type(ws1).__module__}.{type(ws1).__name__}")
    print(f"  -> singleton: {'YES' if is_singleton else 'NO'}")

    # 順便試第三個 — 確認是 process-level 持續 cache
    ws3 = fubon.sdk.marketdata.websocket_client.stock
    print(f"  id(ws3) = {id(ws3):#x} (3rd fetch, same={'YES' if id(ws3) == id(ws1) else 'NO'})")

    # ---- 3. 抓 symbols ----
    print(f"\n[3] 抓 active 個股 (4位純數字, 非 ETF) 前 {N_SYMBOLS} 檔")
    try:
        _init_supabase_sync()
        symbols = await fetch_symbols(N_SYMBOLS)
    except Exception as e:
        print(f"  FAIL: {e}")
        sys.exit(1)
    print(f"  OK 拿到 {len(symbols)} 檔: {symbols[:5]} ... {symbols[-3:]}")

    # ---- 4. 對單條 ws subscribe ----
    print(f"\n[4] 對單條 ws subscribe {len(symbols)} 個 symbol")

    events_all: list[dict] = []
    errors: list[str] = []
    subscribed_ack: list[str] = []
    data_ticks: set[str] = set()  # 收到 data event 的 symbols

    def on_message(raw: object) -> None:
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            events_all.append({"event": "_unparsable", "raw": str(raw)[:200]})
            return
        if not isinstance(payload, dict):
            events_all.append({"event": "_non-dict", "type": type(payload).__name__})
            return
        ev = payload.get("event")
        if ev == "subscribed":
            data = payload.get("data") or {}
            sym = data.get("symbol") or payload.get("symbol")
            if sym:
                subscribed_ack.append(str(sym))
        elif ev == "error":
            errors.append(json.dumps(payload, ensure_ascii=False)[:300])
        elif ev == "data":
            data = payload.get("data") or {}
            sym = data.get("symbol")
            if sym:
                data_ticks.add(str(sym))
        else:
            events_all.append({"event": ev, "keys": list(payload.keys())[:5]})

    def on_disconnect(*args: object) -> None:
        print(f"    [event] disconnect: {args}")

    def on_error(*args: object) -> None:
        print(f"    [event] error: {args}")
        errors.append(f"on_error: {args}")

    ws1.on("message", on_message)
    ws1.on("disconnect", on_disconnect)
    ws1.on("error", on_error)

    print("    connect ...")
    try:
        await asyncio.to_thread(ws1.connect)
    except Exception as e:
        print(f"    FAIL connect: {type(e).__name__}: {e}")
        sys.exit(1)
    await asyncio.sleep(2.0)

    # subscribe — 一次塞 batch
    print(f"    subscribe batch ({len(symbols)} symbols) ...")
    t0 = time.time()
    sub_ok = True
    try:
        await asyncio.to_thread(
            ws1.subscribe,
            {"channel": "trades", "symbols": symbols},
        )
        print(f"    subscribe RPC 回 OK ({time.time() - t0:.2f}s)")
    except Exception as e:
        sub_ok = False
        print(f"    subscribe RPC raised: {type(e).__name__}: {e}")
        errors.append(f"subscribe raised: {type(e).__name__}: {e}")

    print(f"    等 {WAIT_SECONDS}s 收 ack / error / data event ...")
    await asyncio.sleep(WAIT_SECONDS)

    # ---- 5. 結論 ----
    print("\n" + "=" * 64)
    print("結論")
    print("=" * 64)
    print(f"  singleton:           {'YES' if is_singleton else 'NO'}")
    print(f"  subscribe RPC:       {'OK' if sub_ok else 'RAISED'}")
    print(f"  subscribed ack:      {len(subscribed_ack)} / {len(symbols)}")
    if subscribed_ack:
        print(f"    sample ack:        {subscribed_ack[:3]}")
    print(f"  data tick events:    {len(data_ticks)} unique symbols")
    if data_ticks:
        print(f"    sample data:       {list(data_ticks)[:3]}")
    print(f"  error events:        {len(errors)}")
    for e in errors[:5]:
        print(f"    - {e}")
    print(f"  其他 event:           {len(events_all)}")
    counter = Counter(e.get("event", "?") for e in events_all)
    for k, v in counter.most_common(5):
        print(f"    {k}: {v}")

    # ---- 6. 對 Phase 3 ws_pool 架構建議 ----
    print("\n" + "=" * 64)
    print("對 Phase 3 services/fubon_ws.py 架構建議")
    print("=" * 64)
    if is_singleton:
        if not sub_ok:
            print("  - SDK 是 singleton + subscribe RPC raise")
            print("  - Phase 3 ws_pool MAX_CONNS=3 是「假分流」(3 個 var 指同一個 ws)")
            print("  - 建議: 改 multi-process (每 process 一個 SDK)，或詢問富邦真實上限")
        elif len(errors) > 0:
            print("  - SDK 是 singleton + subscribe RPC 成功但富邦回 error")
            print("  - 看 error 訊息判斷是 quota 還是 format 問題")
            print(f"  - 第一個 error: {errors[0]}")
            print("  - 建議: 縮 watchlist 軟上限到富邦回的真實上限")
        else:
            ack_rate = len(subscribed_ack) / len(symbols) if symbols else 0
            print(f"  - SDK 是 singleton + 單條 ws 看似能吃 {len(symbols)} 檔 (ack rate {ack_rate:.0%})")
            print("  - Phase 3 ws_pool 多連線是視覺假象，但單條容量充足")
            print("  - 建議:")
            print("    [A] 簡化: 拿掉 WSPool MAX_CONNS=3 設定，留 refcount + 單條 ws")
            print("    [B] 保守: watchlist 軟上限維持 200")
            print("    [C] 進一步: 盤中再跑一次 probe 驗 data event 流量 (盤後拿不到 tick)")
    else:
        print("  - SDK 不是 singleton (每次 getattr 拿到新物件)")
        print("  - Phase 3 ws_pool MAX_CONNS=3 × 200 = 600 容量設計可行")
        print("  - 建議: 現狀保留，無需更動")

    try:
        await asyncio.to_thread(ws1.disconnect)
    except Exception:
        pass

    print("\nprobe done.")


if __name__ == "__main__":
    asyncio.run(main())
