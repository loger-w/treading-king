"""Mock tick 進 signal_engine 驗：
1. WindowCondition price_change_pct 達成
2. cooldown 第二次 skip
3. AND 邏輯
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

backend = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend))

from models.condition import (
    ActiveFilter, ActiveSignalOut, Condition, WatchlistScope, WindowCondition,
)
from services.ring_buffer import Tick, get_ring_buffer
from services.signal_engine import get_signal_engine


async def main() -> None:
    rb = get_ring_buffer()
    engine = get_signal_engine()

    # mock 一個 active signal: 60 秒內漲 1% (寬鬆好觸發)
    fake_active = ActiveSignalOut(
        id="fake-001", name="test-1pct-60s",
        filter_json=ActiveFilter(
            conditions=[],
            window_conditions=[WindowCondition(
                type="price_change_pct", window_seconds=60, operator="gt", value=1.0,
            )],
            logic="AND",
        ),
        scope=WatchlistScope(type="watchlist"),
        cooldown_seconds=300,
        ignore_auctions=True,
        enabled=True,
        created_at="2026-05-12T00:00:00",
    )
    # 手動塞進 engine（跳過 supabase）
    engine._active = [fake_active]
    engine._field_cache["TEST"] = {}  # watchlist scope 需要 field_cache 有這 symbol

    # 灌 ticks: 100 → 102 (漲 2%)
    rb.ensure("TEST")
    base = time.time()
    rb.append("TEST", Tick(price=100, size=10, time=base - 30))  # 30 秒前 100
    final = Tick(price=102, size=10, time=base)  # 現在 102

    print("[1] 達成條件 — 預期 fan-out")
    fanout_called = []
    orig = engine._fanout
    async def mock_fanout(a, s, t):
        fanout_called.append((a.id, s, t.price))
    engine._fanout = mock_fanout

    await engine._evaluate("TEST", final)
    if len(fanout_called) == 1:
        print(f"  ✓ fanout called: {fanout_called[0]}")
    else:
        print(f"  ✗ expected 1 fanout, got {len(fanout_called)}")
        sys.exit(1)

    print("[2] cooldown — 同 (active, symbol) 立刻又達成應 skip")
    fanout_called.clear()
    await engine._evaluate("TEST", final)
    if len(fanout_called) == 0:
        print("  ✓ skipped (cooldown 內)")
    else:
        print(f"  ✗ expected 0 fanout, got {len(fanout_called)}")
        sys.exit(1)

    print("[3] cooldown 過期後再達成應觸發")
    engine._cooldown.clear()  # 模擬 cooldown 過期
    fanout_called.clear()
    await engine._evaluate("TEST", final)
    if len(fanout_called) == 1:
        print("  ✓ triggered again after cooldown")
    else:
        print(f"  ✗ expected 1 after cooldown clear, got {len(fanout_called)}")
        sys.exit(1)

    print("\nAll signal_engine smoke tests passed ✓")


if __name__ == "__main__":
    asyncio.run(main())
