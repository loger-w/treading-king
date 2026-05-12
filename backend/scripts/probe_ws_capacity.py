"""Probe: 單條 fubon WS 真實 subscribe 上限 (one-by-one mode)

承 probe_ws_singleton (commit c156070) — singleton 已驗，batch sub 不可用。
這支用 phase 3 production 同樣的 per-symbol sub 方式跑 300 個 symbol，
找 RPC raise 或 server error event 的分界，回真實單條 ws sub 上限。

策略:
- one-by-one sub，每次 30ms delay
- RPC raise → 立即 break，print 第幾個失敗
- server 回 error event → 下一個 sub 前 detect，break
- 跑完等 20s 收 ack，比對 ack 收到 vs 已 sub 數

盤後跑：subscribe ack 仍會回（phase 3 probe_ws_pool 已驗），但 data tick 不會。
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
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


PROBE_LIMIT = 1962  # 全市場個股 (4位純數字, 非 ETF/ETN/特別股)
DELAY_MS = 20  # 1962 × 20ms ≈ 40s
WAIT_FOR_ACKS = 20


async def fetch_symbols(n: int) -> list[str]:
    """用 .range() 分頁拉，避開 PostgREST 1000 cap（同 indicator_cache_job 做法）。"""
    sb = get_supabase()
    sb.init()
    if sb.status.value != "ok":
        raise RuntimeError(f"supabase init failed: {sb.last_error}")

    PAGE_SIZE = 1000
    all_syms: list[str] = []
    page = 0
    while True:
        start = page * PAGE_SIZE
        end = start + PAGE_SIZE - 1
        res = await asyncio.to_thread(
            lambda s=start, e=end: sb.client.table("symbols")
            .select("symbol")
            .eq("is_active", True)
            .eq("is_etf", False)
            .order("symbol")
            .range(s, e)
            .execute()
        )
        rows = res.data or []
        all_syms.extend(r["symbol"] for r in rows)
        if len(rows) < PAGE_SIZE:
            break
        page += 1

    syms = [s for s in all_syms if s.isdigit() and len(s) == 4]
    return syms[:n]


async def main() -> None:
    print("=" * 64)
    print("Phase 3 fubon WS 單條 sub 容量 probe (one-by-one)")
    print("=" * 64)

    # ---- 1. init ----
    print("\n[1] fubon.init() + supabase init ...")
    fubon = get_fubon()
    await fubon.init()
    if fubon.status.value != "ok":
        print(f"  FAIL: fubon not OK: {fubon.last_error}")
        sys.exit(1)
    print(f"  OK fubon status={fubon.status.value}")

    print(f"\n[2] 抓 active 個股 (4位純數字, 非 ETF) 前 {PROBE_LIMIT} 檔 ...")
    try:
        symbols = await fetch_symbols(PROBE_LIMIT)
    except Exception as e:
        print(f"  FAIL: {e}")
        sys.exit(1)
    print(f"  OK 拿到 {len(symbols)} 檔")
    if len(symbols) < PROBE_LIMIT:
        print(f"  注意：實際數量 {len(symbols)} < {PROBE_LIMIT}，無法測超過 {len(symbols)} 的上限")

    # ---- 3. wire ws ----
    print(f"\n[3] connect + wire callbacks")
    ws = fubon.sdk.marketdata.websocket_client.stock

    acks: list[str] = []
    server_errors: list[dict] = []
    auth_events: list[dict] = []
    other_events: list[dict] = []

    def on_message(raw: object) -> None:
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        ev = payload.get("event")
        if ev == "subscribed":
            data = payload.get("data") or {}
            sym = data.get("symbol") or payload.get("symbol")
            if sym:
                acks.append(str(sym))
        elif ev == "error":
            server_errors.append(payload)
        elif ev == "authenticated":
            auth_events.append(payload)
        elif ev != "data" and ev != "pong":
            other_events.append({"event": ev, "keys": list(payload.keys())[:5]})

    def on_error(*args: object) -> None:
        server_errors.append({"event": "_sdk_error", "args": str(args)})

    ws.on("message", on_message)
    ws.on("error", on_error)
    ws.on("disconnect", lambda *a: None)  # 盤後可能 disconnect，靜默

    try:
        await asyncio.to_thread(ws.connect)
    except Exception as e:
        print(f"  FAIL connect: {type(e).__name__}: {e}")
        sys.exit(1)
    await asyncio.sleep(2.0)
    print("  connected")

    # ---- 4. one-by-one sub loop ----
    print(f"\n[4] one-by-one sub up to {len(symbols)} symbols (delay {DELAY_MS}ms)")
    last_ok = 0
    first_failure: dict | None = None
    err_count_before_sub = 0
    t0 = time.time()

    # NOTE: SDK 在 process raw message 時對某些 list 格式 raise AttributeError，
    # 從 on_error callback 觸發。這是 SDK noise，不代表 sub 真的失敗 — RPC 仍 OK。
    # 真正 break 條件只有 RPC raise（fire-and-forget 的 RPC 失敗）。
    for i, sym in enumerate(symbols):
        try:
            await asyncio.to_thread(
                ws.subscribe, {"channel": "trades", "symbols": [sym]}
            )
            last_ok = i + 1
        except Exception as e:
            first_failure = {
                "kind": "rpc_raise",
                "sub_index": i + 1,
                "sym": sym,
                "err": f"{type(e).__name__}: {e}",
                "last_ok": last_ok,
            }
            break

        await asyncio.sleep(DELAY_MS / 1000)
        # 進度標
        if (i + 1) % 50 == 0:
            print(f"  ... #{i+1} sub RPC OK (acks={len(acks)}, sdk_errs={len(server_errors)})")

    elapsed = time.time() - t0
    print(f"\n  sub loop done in {elapsed:.1f}s, last_ok={last_ok}")
    if first_failure:
        print(f"  first failure: {first_failure}")

    # ---- 5. 等 ack 收齊 ----
    print(f"\n[5] 等 {WAIT_FOR_ACKS}s 收剩餘 ack ...")
    await asyncio.sleep(WAIT_FOR_ACKS)

    # ---- 6. 結論 ----
    print("\n" + "=" * 64)
    print("結論")
    print("=" * 64)
    print(f"  已 sub RPC OK:       {last_ok} / {len(symbols)}")
    print(f"  ack 收到:            {len(acks)}")
    print(f"  unique ack symbols:  {len(set(acks))}")
    print(f"  server error:        {len(server_errors)}")
    print(f"  auth events:         {len(auth_events)}")
    print(f"  其他 event:           {len(other_events)}")

    if server_errors:
        print("\n  server error 詳情 (前 5):")
        for e in server_errors[:5]:
            s = json.dumps(e, ensure_ascii=False) if isinstance(e, dict) else str(e)
            print(f"    - {s[:300]}")

    if other_events:
        print("\n  其他 event (前 5):")
        for e in other_events[:5]:
            print(f"    - {e}")

    print("\n" + "=" * 64)
    print("對 Phase 3 ws_pool 架構建議")
    print("=" * 64)

    print("  NOTE: SDK parse bug 會 drop 所有 list format response（包括 subscribed ack）")
    print("        → ack 數量不可靠，只能信 RPC raise / 盤中 data tick")
    print()
    if first_failure:
        cap = first_failure["last_ok"]
        print(f"  - 單條 ws RPC 上限 = {cap} symbol")
        print(f"    (#{first_failure['sub_index']} {first_failure['sym']} RPC raise: {first_failure['err'][:120]})")
        if cap < 200:
            print(f"  - {cap} < 200，phase 3 watchlist 軟上限應縮到 {cap}")
        elif cap < 250:
            print(f"  - {cap} 在 200~250 之間，phase 3 設 WS_PER_CONN_CAP=200 安全")
        else:
            print(f"  - {cap} ≥ 250，phase 3 設 WS_PER_CONN_CAP=200 留有 buffer")
    elif last_ok >= len(symbols):
        print(f"  - 跑完 {last_ok} 全部 RPC 都成功，RPC 層面無上限到 {last_ok}")
        print(f"  - 但 RPC fire-and-forget 不代表 server 真的接受，要盤中收 data tick 才能驗")
        print(f"  - 結論: 單條 ws sub RPC 至少可承載 {last_ok} 個 symbol")
        print(f"  - phase 3 WS_PER_CONN_CAP=200 為保守值；正式上限要盤中跑 probe 看 tick 數")
    else:
        print(f"  - sub RPC 跑了 {last_ok}/{len(symbols)} 中斷 (probe 內部問題)")

    try:
        await asyncio.to_thread(ws.disconnect)
    except Exception:
        pass

    print("\nprobe done.")


if __name__ == "__main__":
    asyncio.run(main())
