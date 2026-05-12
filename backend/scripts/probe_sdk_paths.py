"""探索富邦 SDK 完整 method 路徑，看有沒有：
1. CDP / pivot 之類的指標
2. historical daily candle (給 backfill 用)
3. 任何 phase 3 可能用到的 endpoint
"""
from __future__ import annotations

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

from fubon_neo.sdk import FubonSDK  # type: ignore[import-not-found]

personal_id = (
    os.getenv("FUBON_PERSONAL_ID", "").strip()
    or os.getenv("FUBON_ACCOUNT", "").strip()
)
api_key = os.getenv("FUBON_API_KEY", "").strip()
sdk = FubonSDK()
sdk.apikey_dma_login(personal_id, api_key)
sdk.init_realtime()


def show(label: str, obj: object) -> None:
    print(f"\n=== {label} ===")
    members = [m for m in dir(obj) if not m.startswith("_")]
    print(f"  {members}")


# Top level
show("sdk.marketdata.rest_client.stock", sdk.marketdata.rest_client.stock)

# Each child
for child_name in ("intraday", "historical", "technical", "snapshot", "tickers"):
    child = getattr(sdk.marketdata.rest_client.stock, child_name, None)
    if child is None:
        print(f"\n=== sdk...stock.{child_name} ===\n  (not present)")
        continue
    show(f"sdk...stock.{child_name}", child)


# 試 historical.candles
hist = getattr(sdk.marketdata.rest_client.stock, "historical", None)
if hist is not None:
    candles = getattr(hist, "candles", None)
    if candles is not None:
        print("\n=== historical.candles(2330, 2026-05-04 ~ 2026-05-11) ===")
        try:
            r = candles(
                symbol="2330",
                from_="2026-05-04",
                to="2026-05-11",
            )
            print(f"  type: {type(r).__name__}")
            if isinstance(r, dict):
                print(f"  keys: {list(r.keys())}")
                if "data" in r and isinstance(r["data"], list) and r["data"]:
                    print(f"  data[0] keys: {list(r['data'][0].keys())}")
                    print(f"  data sample: {r['data'][0]}")
                    print(f"  data len: {len(r['data'])}")
        except Exception as e:
            print(f"  FAILED: {type(e).__name__}: {e}")

# 試其他可能的 historical method
if hist is not None:
    for m in ("quotes", "stats", "open", "close", "trades"):
        fn = getattr(hist, m, None)
        if fn is None:
            continue
        print(f"\n=== historical.{m}(2330) ===")
        try:
            r = fn(symbol="2330")
            print(f"  type: {type(r).__name__}")
            if isinstance(r, dict):
                print(f"  keys: {list(r.keys())}")
        except Exception as e:
            print(f"  FAILED: {type(e).__name__}: {e}")
