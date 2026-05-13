"""Probe /api/quote/{symbol} 回傳的 bids/asks shape。

確認：
- bids/asks 是 array of {price, size}
- 五檔順序：bids[0] 最高、asks[0] 最低 (best bid/ask)
- 量單位是「張」(假設)
"""
from __future__ import annotations

import asyncio
import json
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


async def main(symbol: str = "2330") -> None:
    fubon = get_fubon()
    await fubon.init()
    if fubon.status.value != "ok":
        print(f"FAIL: fubon status={fubon.status.value}, error={fubon.last_error}")
        return

    result = await fubon.intraday_quote(symbol)
    print(f"=== quote({symbol}) keys ===")
    print(sorted(result.keys()))

    print(f"\n=== bids ({len(result.get('bids', []))}) ===")
    for i, b in enumerate(result.get("bids", [])):
        print(f"  [{i}] price={b.get('price')} size={b.get('size')}  keys={list(b.keys())}")

    print(f"\n=== asks ({len(result.get('asks', []))}) ===")
    for i, a in enumerate(result.get("asks", [])):
        print(f"  [{i}] price={a.get('price')} size={a.get('size')}  keys={list(a.keys())}")

    print(f"\n=== raw (truncated) ===")
    print(json.dumps(result, ensure_ascii=False, indent=2)[:1500])


if __name__ == "__main__":
    sym = sys.argv[1] if len(sys.argv) > 1 else "2330"
    asyncio.run(main(sym))
