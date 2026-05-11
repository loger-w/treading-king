"""驗證 services/screener.py — 用 RSI 超賣 filter 跑一次，跟 RSI route 結果對齊。"""
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

sys.path.insert(0, str(backend))

from services.supabase_client import get_supabase
from services.screener import screen
from models.condition import Condition, Filter

sb = get_supabase()
sb.init()
assert sb.status.value == "ok", f"supabase not ok: {sb.last_error}"

# Test 1：RSI 超賣（跟 hard-coded 的 routes/screen.py 應一致）
print("=== Test 1: rsi<30 AND change>=0 (對標 phase 2a hard-coded) ===")
f = Filter(
    conditions=[
        Condition(field="rsi_14", operator="lt", value=30),
        Condition(field="change_pct", operator="gte", value=0),
    ]
)
r = screen(sb.client, f)
print(f"as_of: {r['as_of_date']} | scanned: {r['total_scanned']} | matched: {r['count']}")
for x in r["results"][:10]:
    print(f"  {x['symbol']} {x['name']:8s}  rsi={x['rsi_14']:.1f}  chg={x['change_pct']:+.2f}%")

# Test 2：跨欄位（close > sma_20，黃金交叉位置之一）
print()
print("=== Test 2: close > sma_20 AND change > 0 (個股在均線上方且收紅) ===")
f2 = Filter(
    conditions=[
        Condition(field="close", operator="gt", value="sma_20"),
        Condition(field="change_pct", operator="gt", value=0),
    ],
    limit=10,
)
r2 = screen(sb.client, f2)
print(f"matched: {r2['count']} (top 10)")
for x in r2["results"]:
    print(f"  {x['symbol']} {x['name']:8s}  close={x['close']}  sma_20={x['sma_20']}  chg={x['change_pct']:+.2f}%")

# Test 3：OR 邏輯
print()
print("=== Test 3: OR — rsi<25 OR rsi>75 (極值) ===")
f3 = Filter(
    conditions=[
        Condition(field="rsi_14", operator="lt", value=25),
        Condition(field="rsi_14", operator="gt", value=75),
    ],
    logic="OR",
    limit=10,
)
r3 = screen(sb.client, f3)
print(f"matched: {r3['count']} (top 10 by rsi)")
for x in r3["results"]:
    tag = "超賣" if x["rsi_14"] < 25 else "超買"
    print(f"  {x['symbol']} {x['name']:8s}  rsi={x['rsi_14']:.1f}  [{tag}]")

print()
print("All screener probes done.")
