"""端到端：建一個寬鬆 active_signal → 加股票進 watchlist → 等 10 分鐘 → 看 signals_log。

需要在交易時段跑（盤中），盤後沒 tick 不會觸發。
"""
from __future__ import annotations

import asyncio
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

from services.fubon_client import get_fubon
from services.fubon_ws import get_ws_pool
from services.signal_engine import get_signal_engine
from services.supabase_client import get_supabase
from services.supabase_writer import get_supabase_writer


async def main() -> None:
    fubon = get_fubon()
    await fubon.init()
    sb = get_supabase()
    sb.init()
    if fubon.status.value != "ok" or sb.status.value != "ok":
        print(f"FAIL fubon={fubon.status.value} sb={sb.status.value}")
        sys.exit(1)

    pool = get_ws_pool()
    await pool.start()
    writer = get_supabase_writer()
    await writer.start()
    engine = get_signal_engine()
    await engine.start()

    print("[setup] 加 2330 進 watchlist")
    sb.client.table("watchlist").upsert({"symbol": "2330"}).execute()
    await pool.subscribe("2330", owner_id="watchlist")

    print("[setup] 建一個寬鬆 active_signal: 1 分鐘漲 0.001%")
    res = sb.client.table("active_signals").insert({
        "name": "[probe-e2e] 寬鬆漲幅",
        "filter_json": {
            "schema_version": 1,
            "market": ["TWSE", "OTC"], "exclude_etf": True,
            "conditions": [],
            "window_conditions": [{
                "type": "price_change_pct", "window_seconds": 60,
                "operator": "gt", "value": 0.001,
            }],
            "logic": "AND", "limit": 200,
        },
        "scope": {"type": "watchlist"},
        "cooldown_seconds": 60, "ignore_auctions": True, "enabled": True,
    }).execute()
    asid = res.data[0]["id"]
    await pool.subscribe("2330", owner_id=asid)
    await engine.refresh_active_signals()

    print("[wait] 等 10 分鐘看 signals_log...")
    end = time.time() + 600
    seen_before = sb.client.table("signals_log").select("id", count="exact").eq("active_signal_id", asid).execute().count or 0
    while time.time() < end:
        await asyncio.sleep(30)
        cnt = sb.client.table("signals_log").select("id", count="exact").eq("active_signal_id", asid).execute().count or 0
        h = engine.health()
        print(f"  t+{int(end-time.time())}s | signals={cnt-seen_before} | queue={h['queue_depth']} | lag={h['lag_ms']}ms")
        if cnt - seen_before >= 1:
            print("  ✓ 收到至少 1 筆訊號")
            break

    print("[cleanup] 移除 probe 用的 active_signal + watchlist")
    sb.client.table("active_signals").delete().eq("id", asid).execute()
    sb.client.table("watchlist").delete().eq("symbol", "2330").execute()
    sb.client.table("signals_log").delete().eq("active_signal_id", asid).execute()

    await engine.shutdown()
    await writer.shutdown()
    await pool.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
