"""驗證 intraday.candles + historical.candles 行為，看分時走勢可否直接吃 candle 不用 tick 聚合。"""
from __future__ import annotations

import json
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

sdk = FubonSDK()
sdk.apikey_dma_login(
    os.getenv("FUBON_PERSONAL_ID", "").strip() or os.getenv("FUBON_ACCOUNT", "").strip(),
    os.getenv("FUBON_API_KEY", "").strip(),
)
sdk.init_realtime()

intraday = sdk.marketdata.rest_client.stock.intraday
hist = sdk.marketdata.rest_client.stock.historical

print("=== intraday.candles(2330) — 看分時 1 分 K ===")
try:
    r = intraday.candles(symbol="2330")
    print(f"  keys: {list(r.keys())}")
    if "data" in r:
        data = r["data"]
        print(f"  data length: {len(data)}")
        if data:
            print(f"  data[0] keys: {list(data[0].keys())}")
            print(f"  data[0]: {data[0]}")
            print(f"  data[-1]: {data[-1]}")
except Exception as e:
    print(f"  FAILED: {type(e).__name__}: {e}")

print()
print("=== intraday.candles(2330, timeframe='1') — 強制 1 分 ===")
try:
    r = intraday.candles(symbol="2330", timeframe="1")
    print(f"  keys: {list(r.keys()) if isinstance(r, dict) else type(r).__name__}")
    if isinstance(r, dict) and "data" in r:
        print(f"  data length: {len(r['data'])}")
        if r["data"]:
            print(f"  data[0]: {r['data'][0]}")
except Exception as e:
    print(f"  FAILED: {type(e).__name__}: {e}")

print()
print("=== historical.candles(2330, 2026-05-11 ~ 2026-05-11) — 單日 ===")
try:
    r = hist.candles(symbol="2330", from_="2026-05-11", to="2026-05-11")
    print(f"  keys: {list(r.keys())}")
    print(f"  timeframe: {r.get('timeframe')}")
    print(f"  data length: {len(r['data'])}")
    for row in r["data"]:
        print(f"    {row}")
except Exception as e:
    print(f"  FAILED: {type(e).__name__}: {e}")

print()
print("=== intraday.trades(2330) — 看當日逐筆成交 ===")
try:
    r = intraday.trades(symbol="2330", limit=5)
    print(f"  keys: {list(r.keys()) if isinstance(r, dict) else type(r).__name__}")
    if isinstance(r, dict) and "data" in r:
        print(f"  data length: {len(r['data'])}")
        if r["data"]:
            print(f"  data[0] keys: {list(r['data'][0].keys())}")
            print(f"  data[0]: {r['data'][0]}")
except Exception as e:
    print(f"  FAILED: {type(e).__name__}: {e}")

print()
print("=== intraday.volumes(2330) — 不知道是什麼 ===")
try:
    r = intraday.volumes(symbol="2330")
    print(f"  type: {type(r).__name__}")
    if isinstance(r, dict):
        print(f"  keys: {list(r.keys())}")
        if "data" in r and r["data"]:
            print(f"  data[0]: {r['data'][0]}")
except Exception as e:
    print(f"  FAILED: {type(e).__name__}: {e}")
