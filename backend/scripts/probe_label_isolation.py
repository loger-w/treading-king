"""Probe: 兩個 label (alice / bob) 隔離煙霧。

直接打 Supabase service_role client，不走 backend HTTP — 模擬「alice 跟 bob 各自
跑 backend，連同一個 Supabase」的真實情境。

驗證：
  1. alice 寫 watchlist + strategies → bob 看不到
  2. bob 寫一筆 → alice 也看不到
  3. 結束清理乾淨

跑法（在 backend/ 內）：
  ./.venv/Scripts/python.exe scripts/probe_label_isolation.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from services.supabase_client import get_supabase  # noqa: E402

ALICE = "probe_alice"
BOB = "probe_bob"


def cleanup(client) -> None:
    for tbl in ("signals_log", "active_signals", "strategies", "watchlist"):
        for label in (ALICE, BOB):
            client.table(tbl).delete().eq("user_label", label).execute()


def assert_eq(actual, expected, label: str) -> None:
    if actual != expected:
        print(f"FAIL {label}: expected {expected}, got {actual}")
        sys.exit(1)
    print(f"OK   {label}")


def main() -> None:
    sb = get_supabase()
    sb.init()
    client = sb.client
    if client is None:
        print("FATAL: supabase client init failed")
        sys.exit(2)

    print("=== cleanup before run ===")
    cleanup(client)

    print("\n=== alice writes ===")
    client.table("watchlist").insert({
        "symbol": "2330", "note": "alice", "user_label": ALICE,
    }).execute()
    client.table("strategies").insert({
        "name": "alice_strategy",
        "description": None,
        "filter_json": {"market": ["TWSE"], "exclude_etf": False,
                        "conditions": [], "logic": "AND"},
        "user_label": ALICE,
    }).execute()
    print("alice inserted watchlist + strategy")

    print("\n=== bob writes ===")
    client.table("watchlist").insert({
        "symbol": "2454", "note": "bob", "user_label": BOB,
    }).execute()
    client.table("strategies").insert({
        "name": "bob_strategy",
        "description": None,
        "filter_json": {"market": ["TWSE"], "exclude_etf": False,
                        "conditions": [], "logic": "AND"},
        "user_label": BOB,
    }).execute()
    print("bob inserted watchlist + strategy")

    print("\n=== isolation checks ===")
    alice_wl = client.table("watchlist").select("symbol").eq("user_label", ALICE).execute().data
    bob_wl   = client.table("watchlist").select("symbol").eq("user_label", BOB).execute().data
    assert_eq([r["symbol"] for r in alice_wl], ["2330"], "alice watchlist = [2330]")
    assert_eq([r["symbol"] for r in bob_wl],   ["2454"], "bob watchlist   = [2454]")

    alice_st = client.table("strategies").select("name").eq("user_label", ALICE).execute().data
    bob_st   = client.table("strategies").select("name").eq("user_label", BOB).execute().data
    assert_eq([r["name"] for r in alice_st], ["alice_strategy"], "alice strategies")
    assert_eq([r["name"] for r in bob_st],   ["bob_strategy"],   "bob strategies")

    print("\n=== cleanup after run ===")
    cleanup(client)
    print("\nOK probe_label_isolation passed")


if __name__ == "__main__":
    main()
