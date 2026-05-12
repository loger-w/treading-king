"""驗證 cdp.backfill_from_fubon 對 2330 跑一次能拿資料 + 算 5 線。"""
from __future__ import annotations

import asyncio
import os
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

from services.cdp import get_cdp_service
from services.fubon_client import get_fubon
from services.supabase_client import get_supabase


async def main() -> None:
    fubon = get_fubon()
    await fubon.init()
    sb = get_supabase()
    sb.init()
    if fubon.status.value != "ok" or sb.status.value != "ok":
        print(f"FAIL fubon={fubon.status.value} sb={sb.status.value}")
        sys.exit(1)

    cdp = get_cdp_service()
    print("[1] backfill 2330 from fubon")
    ok = await cdp.backfill_from_fubon("2330")
    if not ok:
        print("  ✗ backfill failed")
        sys.exit(1)
    print("  ✓ backfill OK")

    print("[2] cdp.get('2330') — 5 值")
    levels = await cdp.get("2330")
    if levels is None:
        print("  ✗ get returned None")
        sys.exit(1)
    print(f"  ah={levels['ah']:.2f}  nh={levels['nh']:.2f}  cdp={levels['cdp']:.2f}  nl={levels['nl']:.2f}  al={levels['al']:.2f}  (as_of {levels['as_of_date']})")
    assert levels["ah"] > levels["nh"] > levels["cdp"] > levels["nl"] > levels["al"], "順序不對"
    print("  ✓ ordering 正確")

    print("\nAll cdp backfill tests passed ✓")


if __name__ == "__main__":
    asyncio.run(main())
