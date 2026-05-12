"""驗 supabase_writer batch flush — append 5 個 row → 等 1 秒 → 驗 signals_log 有 5 筆。"""
from __future__ import annotations

import asyncio
import sys
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

from services.supabase_client import get_supabase
from services.supabase_writer import get_supabase_writer


async def main() -> None:
    sb = get_supabase()
    sb.init()
    if sb.status.value != "ok":
        print(f"FAIL supabase: {sb.last_error}")
        sys.exit(1)

    # 先看當前 signals_log 有幾筆
    res = await asyncio.to_thread(
        lambda: sb.client.table("signals_log").select("id", count="exact").execute()
    )
    before = res.count or 0
    print(f"[before] signals_log count = {before}")

    writer = get_supabase_writer()
    await writer.start()

    print("[append] 5 個 mock signal rows")
    for i in range(5):
        writer.append({
            "active_signal_id": None,
            "symbol": "2330",
            "trigger_price": 580 + i * 0.1,
            "trigger_volume": 100,
            "context_json": {"probe": True, "i": i},
        })

    print("[wait] 1.5s 給 writer flush")
    await asyncio.sleep(1.5)

    res = await asyncio.to_thread(
        lambda: sb.client.table("signals_log").select("id", count="exact").execute()
    )
    after = res.count or 0
    print(f"[after] signals_log count = {after}")

    diff = after - before
    if diff == 5:
        print(f"  ✓ 5 rows inserted (diff={diff})")
    else:
        print(f"  ✗ expected diff=5, got {diff}")
        sys.exit(1)

    await writer.shutdown()
    print("\nAll supabase_writer tests passed ✓")


if __name__ == "__main__":
    asyncio.run(main())
