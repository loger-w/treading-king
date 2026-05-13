"""驗 GET /api/signals/today_counts 回 today TW 的 signals_log raw rows。

預期：
1. 起初 today_counts 回 N 筆
2. 插 3 筆 mock signals_log (triggered_at=now, context_json.probe=true)
3. today_counts 應回 N+3 筆
4. cleanup: DELETE WHERE context_json->>'probe' = 'true'

執行：
  & C:\\side-project\\trading-king\\backend\\.venv\\Scripts\\python.exe \\
    C:\\side-project\\trading-king\\backend\\scripts\\probe_today_counts.py
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

backend = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend))

from dotenv import load_dotenv

load_dotenv(backend / ".env")

import httpx

from services.supabase_client import get_supabase


BASE_URL = "http://localhost:8000"


async def main() -> None:
    sb = get_supabase()
    sb.init()
    if sb.client is None:
        print(f"✗ supabase init 失敗: {sb.last_error}"); sys.exit(1)
    print(f"✓ supabase init OK")

    # cleanup leftovers
    sb.client.table("signals_log").delete().eq("context_json->>probe", "true").execute()

    # baseline
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as cli:
        r = await cli.get("/api/signals/today_counts")
        if r.status_code != 200:
            print(f"  ✗ status {r.status_code}: {r.text}"); sys.exit(1)
        data = r.json()
        baseline_count = len(data.get("counts", []))
        print(f"[baseline] today_counts: {baseline_count} 筆")
        print(f"  today_start: {data.get('today_start')}")

    # 需要至少一個 active_signal 才能插 signals_log (FK)
    sig_res = sb.client.table("active_signals").select("id").limit(1).execute()
    if not (sig_res.data or []):
        print("⚠ 無 active_signal — 無法插 mock signals_log。先建一條再跑")
        sys.exit(0)
    fake_signal_id = sig_res.data[0]["id"]

    # 插 3 筆 mock today TW （用真實 symbol 2330 避開 FK 違反）
    now_tw = datetime.now(ZoneInfo("Asia/Taipei"))
    mock_rows = []
    for i in range(3):
        mock_rows.append({
            "active_signal_id": fake_signal_id,
            "symbol": "2330",
            "triggered_at": now_tw.isoformat(),
            "trigger_price": 100.0 + i,
            "trigger_volume": 10,
            "context_json": {"probe": "true", "iter": i},
        })
    sb.client.table("signals_log").insert(mock_rows).execute()
    print(f"[insert] 3 筆 mock signals_log (probe=true)")

    # re-fetch
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as cli:
        r = await cli.get("/api/signals/today_counts")
        data = r.json()
        new_count = len(data.get("counts", []))
        print(f"[after] today_counts: {new_count} 筆")
        if new_count - baseline_count != 3:
            print(f"  ✗ FAIL: expected +3 (got +{new_count - baseline_count})"); sys.exit(1)

    # verify shape: each row has symbol + active_signal_id
    sample = data["counts"][0] if data["counts"] else {}
    if "symbol" not in sample or "active_signal_id" not in sample:
        print(f"  ✗ FAIL: row shape wrong, got keys: {list(sample.keys())}"); sys.exit(1)
    print(f"  ✓ row shape OK: {list(sample.keys())}")

    # cleanup
    sb.client.table("signals_log").delete().eq("context_json->>probe", "true").execute()
    print("[cleanup] 已刪除 probe=true rows")

    print("\nAll today_counts probe passed ✓")


if __name__ == "__main__":
    asyncio.run(main())
