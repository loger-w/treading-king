"""Probe — print raw technical responses for 2330, so we know exact field names
to extract in indicator_cache_job._fetch_symbol().

跑法（從 backend/）：
    .venv\\Scripts\\Activate.ps1
    python scripts/probe_technical_fields.py
"""
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

personal_id = (
    os.getenv("FUBON_PERSONAL_ID", "").strip()
    or os.getenv("FUBON_ACCOUNT", "").strip()
)
api_key = os.getenv("FUBON_API_KEY", "").strip()
assert personal_id and api_key, ".env missing FUBON_PERSONAL_ID/FUBON_ACCOUNT or FUBON_API_KEY"

sdk = FubonSDK()
sdk.apikey_dma_login(personal_id, api_key)
sdk.init_realtime()

tech = sdk.marketdata.rest_client.stock.technical
intraday = sdk.marketdata.rest_client.stock.intraday

calls = [
    ("intraday.quote", lambda: intraday.quote(symbol="2330")),
    ("rsi(14)", lambda: tech.rsi(symbol="2330", period=14)),
    ("macd(12,26,9)", lambda: tech.macd(symbol="2330", fast=12, slow=26, signal=9)),
    ("kdj(9,3,3)", lambda: tech.kdj(symbol="2330", rPeriod=9, kPeriod=3, dPeriod=3)),
    ("sma(20)", lambda: tech.sma(symbol="2330", period=20)),
    ("bb(20,2)", lambda: tech.bb(symbol="2330", period=20, stdDev=2)),
]

for label, fn in calls:
    print(f"\n=== {label} ===")
    try:
        r = fn()
        if isinstance(r, dict):
            top_keys = list(r.keys())
            print(f"top-level keys: {top_keys}")
            data = r.get("data")
            if isinstance(data, list) and data:
                last = data[-1]
                print(f"data[-1] keys: {list(last.keys()) if isinstance(last, dict) else type(last).__name__}")
                # Pretty-print last row (compact)
                print(f"data[-1]: {json.dumps(last, ensure_ascii=False, default=str)[:500]}")
            elif label == "intraday.quote":
                # quote response is flat, not data[]
                preview = {k: v for k, v in r.items() if k not in ("bids", "asks")}
                print(f"flat: {json.dumps(preview, ensure_ascii=False, default=str)[:500]}")
            else:
                print(f"raw: {str(r)[:500]}")
        else:
            print(f"non-dict {type(r).__name__}: {str(r)[:500]}")
    except Exception as e:
        print(f"FAILED: {type(e).__name__}: {e}")
