"""驗證 fubon_ws.WSPool 的 refcount registry — 兩 owner 同 symbol 退一個不互踩。"""
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

from services.fubon_client import get_fubon
from services.fubon_ws import get_ws_pool


async def main() -> None:
    fubon = get_fubon()
    await fubon.init()
    if fubon.status.value != "ok":
        print(f"FAIL: fubon not OK: {fubon.last_error}")
        sys.exit(1)

    pool = get_ws_pool()
    await pool.start()

    print("[1] subscribe(2330, owner=A) — 預期真打富邦訂閱")
    await pool.subscribe("2330", "A")
    assert pool.total_subscribed() == 1, "expected 1 sub"
    print(f"  ✓ total_subscribed={pool.total_subscribed()}")

    print("[2] subscribe(2330, owner=B) — 同 symbol 不再打富邦，但 refcount=2")
    await pool.subscribe("2330", "B")
    assert pool.total_subscribed() == 1, "should still be 1 (same symbol)"
    assert "A" in pool._refcount["2330"] and "B" in pool._refcount["2330"]
    print(f"  ✓ refcount: {pool._refcount['2330']}")

    print("[3] unsubscribe(2330, owner=A) — refcount 1，仍訂閱中")
    await pool.unsubscribe("2330", "A")
    assert pool.total_subscribed() == 1, "still subscribed (B owns)"
    assert "B" in pool._refcount["2330"] and "A" not in pool._refcount["2330"]
    print(f"  ✓ refcount: {pool._refcount['2330']}")

    print("[4] unsubscribe(2330, owner=B) — refcount 0，真退訂")
    await pool.unsubscribe("2330", "B")
    assert pool.total_subscribed() == 0, "should be 0"
    assert "2330" not in pool._refcount
    print(f"  ✓ total_subscribed={pool.total_subscribed()}, refcount cleared")

    await pool.shutdown()
    print("\nAll WS pool refcount tests passed ✓")


if __name__ == "__main__":
    asyncio.run(main())
