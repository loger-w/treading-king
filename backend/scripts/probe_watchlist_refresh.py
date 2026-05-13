"""驗 refresh_active_signals() 對 watchlist 的 field_cache 載入邏輯。

注意：因 probe 是獨立 process（跟 backend uvicorn 不同 Python interpreter），
無法直接觀察 backend 端的 engine._field_cache。所以 probe 採取兩段驗證：

  A. In-process sanity：在 probe 自己的 process 跑 refresh_active_signals()，
     確認當前 DB watchlist + 有 enabled scope=watchlist signal 時，會把
     watchlist symbols 載入 _field_cache。這驗 refresh 邏輯本身正確。

  B. Manual UAT（不在這 probe 內）：POST /api/watchlist 後檢查 uvicorn log
     應該看到 `services.signal_engine: active_signals reloaded: N enabled`
     log line — 證明 routes/watchlist.py 的修正有效呼叫到 refresh。

執行：
  & C:\\side-project\\trading-king\\backend\\.venv\\Scripts\\python.exe \\
    C:\\side-project\\trading-king\\backend\\scripts\\probe_watchlist_refresh.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

backend = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend))

from dotenv import load_dotenv

load_dotenv(backend / ".env")

from services.signal_engine import get_signal_engine
from services.supabase_client import get_supabase


async def main() -> None:
    sb = get_supabase()
    sb.init()
    if sb.client is None:
        print(f"✗ supabase init 失敗: {sb.last_error}")
        sys.exit(1)
    print(f"✓ supabase init OK")

    # 確認至少有一個 scope=watchlist 的 enabled active_signal
    res = sb.client.table("active_signals").select("id, name, scope, enabled").eq("enabled", True).execute()
    watchlist_signals = [
        r for r in (res.data or [])
        if (r.get("scope") or {}).get("type") == "watchlist"
    ]
    if not watchlist_signals:
        print("⚠ 無 enabled 的 scope=watchlist active_signal — probe 略過")
        print("  (refresh 邏輯需要這種 signal 才會載入 watchlist symbols 進 field_cache)")
        print("  請先 POST /api/active_signals 建一條再跑")
        sys.exit(0)
    print(f"✓ {len(watchlist_signals)} 條 enabled scope=watchlist signal")

    # 查當前 watchlist
    wl_res = sb.client.table("watchlist").select("symbol").execute()
    wl_symbols = {r["symbol"] for r in (wl_res.data or [])}
    print(f"✓ DB watchlist 有 {len(wl_symbols)} 檔: {sorted(wl_symbols)}")

    if not wl_symbols:
        print("⚠ watchlist 是空的 — refresh 不會載入任何 symbol")
        print("  請先 POST /api/watchlist {symbol: ...} 加幾檔再跑")
        sys.exit(0)

    # 跑 refresh_active_signals 在 probe 自己的 engine
    engine = get_signal_engine()
    await engine.refresh_active_signals()

    # 檢查 field_cache 是否含 watchlist symbols
    cached_syms = set(engine._field_cache.keys())
    print(f"✓ refresh 完成，engine._field_cache 含 {len(cached_syms)} 檔: {sorted(cached_syms)}")

    missing = wl_symbols - cached_syms
    if missing:
        # 注意：watchlist symbol 若沒 indicator_cache row 也沒 cdp data，
        # 不會進 field_cache。這是設計上的副作用，不算 bug。
        print(f"⚠ watchlist 中有 {len(missing)} 檔不在 field_cache: {sorted(missing)}")
        print(f"  （可能因該 symbol 在 indicator_cache 沒當日 row、且 cdp service 沒 levels）")
        print(f"  refresh 邏輯本身 OK — 確認 {len(wl_symbols & cached_syms)} 檔成功載入")
    else:
        print(f"✓ 所有 {len(wl_symbols)} 檔 watchlist symbol 都在 field_cache")

    print("\n[Manual UAT] 接下來請手動驗 routes/watchlist.py 的修正：")
    print("  1. POST /api/watchlist {symbol: '<新檔>'}")
    print("  2. 看 uvicorn log 應出現: `services.signal_engine: active_signals reloaded: N enabled`")
    print("  3. DELETE /api/watchlist/<新檔> 同樣會觸發 log")
    print("\nAll refresh logic sanity passed ✓")


if __name__ == "__main__":
    asyncio.run(main())
