"""探勘 Fubon API 哪個 endpoint 給內外盤總量資訊。

候選:
1. intraday.quote(symbol) — 已用,看 response 是否有 buy/sell volume
2. intraday.volumes(symbol) — 名字看起來最像
3. intraday.trades(symbol) — 逐筆,可本地分類但成本高

跑這支會印每個 endpoint 的完整 keys + sample data。
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

sdk = FubonSDK()
sdk.apikey_dma_login(
    os.getenv("FUBON_PERSONAL_ID", "").strip() or os.getenv("FUBON_ACCOUNT", "").strip(),
    os.getenv("FUBON_API_KEY", "").strip(),
)
sdk.init_realtime()

intraday = sdk.marketdata.rest_client.stock.intraday

SYMBOL = "2330"

print(f"=== intraday.quote({SYMBOL}) full response ===")
try:
    r = intraday.quote(symbol=SYMBOL)
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str)[:3000])
except Exception as e:
    print(f"  FAILED: {type(e).__name__}: {e}")

print()
print(f"=== intraday.volumes({SYMBOL}) full response ===")
try:
    r = intraday.volumes(symbol=SYMBOL)
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str)[:3000])
except Exception as e:
    print(f"  FAILED: {type(e).__name__}: {e}")

print()
print(f"=== intraday.trades({SYMBOL}, limit=5) sample ===")
try:
    r = intraday.trades(symbol=SYMBOL, limit=5)
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str)[:2000])
except Exception as e:
    print(f"  FAILED: {type(e).__name__}: {e}")
