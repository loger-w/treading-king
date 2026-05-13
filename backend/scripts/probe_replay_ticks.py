"""端到端 replay — 拉今日 1 分 K 跑 user 的 active_signals + watchlist。

盤後可跑。讀 supabase + Fubon `intraday.candles` API。
不寫 signals_log / 不 WS broadcast(monkey-patch fanout)。
不啟動 ws_pool / writer / engine background tasks。

關鍵設計:FakeClock module-level patch ring_buffer/signal_engine 的 time,
讓歷史時間 candle 也能正確跑 ring_buffer.window() 跟 cooldown 邏輯。
"""
from __future__ import annotations

import asyncio
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
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

import services.ring_buffer as rb_mod
import services.signal_engine as se_mod
from services.fubon_client import get_fubon
from services.ring_buffer import Tick, get_ring_buffer
from services.signal_engine import get_signal_engine
from services.supabase_client import get_supabase
from services.user_context import get_user_label


class FakeClock:
    """Module-level monkey-patch target — 取代 ring_buffer/signal_engine 的 time 模組。"""

    def __init__(self) -> None:
        self.now: float = 0.0

    def time(self) -> float:
        return self.now


@dataclass
class Trigger:
    triggered_at: float       # epoch seconds (tick.time)
    symbol: str
    active_signal_id: str
    active_signal_name: str
    trigger_price: float
    trigger_volume: int
    summary: str              # window/cache context (human-readable)


def _get(obj, key, default=None):
    """從 Pydantic model 或 dict 取屬性。filter_json 從 DB 讀回是 dict,直接 mock 是 model。"""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


async def main() -> None:
    fubon = get_fubon()
    await fubon.init()
    sb = get_supabase()
    sb.init()
    if fubon.status.value != "ok" or sb.status.value != "ok":
        print(f"FAIL fubon={fubon.status.value} sb={sb.status.value}")
        sys.exit(1)

    label = get_user_label()

    # Watchlist (給 report 顯示用)
    wl_res = await asyncio.to_thread(
        lambda: sb.client.table("watchlist").select("symbol").eq("user_label", label).execute()
    )
    watchlist = sorted(row["symbol"] for row in (wl_res.data or []))
    if not watchlist:
        print(f"USER_LABEL={label} 沒 watchlist — nothing to replay")
        sys.exit(0)

    engine = get_signal_engine()
    await engine.refresh_active_signals()
    if not engine._active:
        print(f"USER_LABEL={label} 沒 enabled active_signals — nothing to replay")
        sys.exit(0)

    print(f"[init OK] USER_LABEL={label}, watchlist={len(watchlist)} symbols, "
          f"active_signals={len(engine._active)} enabled")
    print(f"[stub] replay loop 還沒實作 — Task 3 補")


if __name__ == "__main__":
    asyncio.run(main())
