"""Mock tick 進 signal_engine 驗：
1. 既有 case 1-3 — WindowCondition price_change_pct / cooldown skip / cooldown 過期重觸發
2. 新增 case 4-11 — volume_burst / trade_count / Filter 跨欄位 / 指標 / OR logic / scope / 多 active cooldown / empty window
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
    ActiveFilter, ActiveSignalOut, Condition, SymbolsScope, WatchlistScope, WindowCondition,
)
from services.ring_buffer import Tick, get_ring_buffer
from services.signal_engine import get_signal_engine


def make_active(
    aid: str,
    *,
    conditions=None,
    window_conditions=None,
    logic="AND",
    scope=None,
    cooldown=300,
) -> ActiveSignalOut:
    return ActiveSignalOut(
        id=aid,
        name=f"test-{aid}",
        filter_json=ActiveFilter(
            conditions=conditions or [],
            window_conditions=window_conditions or [],
            logic=logic,
        ),
        scope=scope or WatchlistScope(type="watchlist"),
        cooldown_seconds=cooldown,
        ignore_auctions=True,
        enabled=True,
        created_at="2026-05-12T00:00:00",
    )


def reset_engine(engine, rb, symbols=("TEST",)):
    """每個 case 開頭呼叫:清乾淨 engine state + ring_buffer。"""
    engine._cooldown.clear()
    engine._field_cache = {s: {} for s in symbols}
    for s in symbols:
        rb.discard(s)
        rb.ensure(s)


async def main() -> None:
    rb = get_ring_buffer()
    engine = get_signal_engine()

    # 共用的 mock_fanout — record only
    fanout_called: list = []
    async def mock_fanout(a, s, t):
        fanout_called.append((a.id, s, t.price))
    engine._fanout = mock_fanout

    # ========== 既有 case 1-3:price_change_pct + cooldown ==========
    print("[1] price_change_pct gt 1.0 (60s) — 預期 fan-out")
    reset_engine(engine, rb)
    engine._active = [make_active("c1", window_conditions=[
        WindowCondition(type="price_change_pct", window_seconds=60, operator="gt", value=1.0),
    ])]
    base = time.time()
    rb.append("TEST", Tick(price=100, size=10, time=base - 30))
    final = Tick(price=102, size=10, time=base)
    fanout_called.clear()
    await engine._evaluate("TEST", final)
    if len(fanout_called) == 1: print(f"  ✓ fanout: {fanout_called[0]}")
    else: print(f"  ✗ expected 1 fanout, got {len(fanout_called)}"); sys.exit(1)

    print("[2] cooldown — 同 (active, symbol) 立刻又達成應 skip")
    fanout_called.clear()
    await engine._evaluate("TEST", final)
    if len(fanout_called) == 0: print("  ✓ skipped (cooldown 內)")
    else: print(f"  ✗ expected 0 fanout, got {len(fanout_called)}"); sys.exit(1)

    print("[3] cooldown 過期後再達成應觸發")
    engine._cooldown.clear()
    fanout_called.clear()
    await engine._evaluate("TEST", final)
    if len(fanout_called) == 1: print("  ✓ triggered again after cooldown")
    else: print(f"  ✗ expected 1 after cooldown clear, got {len(fanout_called)}"); sys.exit(1)

    # ========== case 4:volume_burst window ==========
    print("[4] volume_burst gt 5000 (60s) — 5 個 tick volume 共 10000")
    reset_engine(engine, rb)
    engine._active = [make_active("c4", window_conditions=[
        WindowCondition(type="volume_burst", window_seconds=60, operator="gt", value=5000),
    ])]
    base = time.time()
    for i in range(5):
        rb.append("TEST", Tick(price=100, size=2000, time=base - 50 + i * 10))
    final = Tick(price=100, size=2000, time=base)
    fanout_called.clear()
    await engine._evaluate("TEST", final)
    if len(fanout_called) == 1: print("  ✓ fanout (volume_burst)")
    else: print(f"  ✗ expected 1, got {len(fanout_called)}"); sys.exit(1)

    # ========== case 5:trade_count window ==========
    print("[5] trade_count gte 10 (60s) — 60s 內塞 12 ticks")
    reset_engine(engine, rb)
    engine._active = [make_active("c5", window_conditions=[
        WindowCondition(type="trade_count", window_seconds=60, operator="gte", value=10),
    ])]
    base = time.time()
    for i in range(11):
        rb.append("TEST", Tick(price=100, size=1, time=base - 55 + i * 5))
    final = Tick(price=100, size=1, time=base)
    fanout_called.clear()
    await engine._evaluate("TEST", final)
    if len(fanout_called) == 1: print("  ✓ fanout (trade_count)")
    else: print(f"  ✗ expected 1, got {len(fanout_called)}"); sys.exit(1)

    # ========== case 6:Filter close gt cdp_ah(跨欄位) ==========
    print("[6] Filter close gt cdp_ah — close=2285, cdp_ah=2280")
    reset_engine(engine, rb)
    engine._field_cache["TEST"]["cdp_ah"] = 2280.0
    engine._active = [make_active("c6", conditions=[
        Condition(field="close", operator="gt", value="cdp_ah"),
    ])]
    base = time.time()
    rb.append("TEST", Tick(price=2285, size=1, time=base))
    final = Tick(price=2285, size=1, time=base)
    fanout_called.clear()
    await engine._evaluate("TEST", final)
    if len(fanout_called) == 1: print("  ✓ fanout (cross-field)")
    else: print(f"  ✗ expected 1, got {len(fanout_called)}"); sys.exit(1)

    # ========== case 7:Filter rsi_14 lt 30 ==========
    print("[7] Filter rsi_14 lt 30 — cache rsi=25")
    reset_engine(engine, rb)
    engine._field_cache["TEST"]["rsi_14"] = 25.0
    engine._active = [make_active("c7", conditions=[
        Condition(field="rsi_14", operator="lt", value=30.0),
    ])]
    base = time.time()
    final = Tick(price=100, size=1, time=base)
    fanout_called.clear()
    await engine._evaluate("TEST", final)
    if len(fanout_called) == 1: print("  ✓ fanout (indicator)")
    else: print(f"  ✗ expected 1, got {len(fanout_called)}"); sys.exit(1)

    # ========== case 8:OR logic 至少一個達成 ==========
    print("[8] OR logic — rsi=25 (<30 True) + close gt 99999 (False)")
    reset_engine(engine, rb)
    engine._field_cache["TEST"]["rsi_14"] = 25.0
    engine._active = [make_active("c8", logic="OR", conditions=[
        Condition(field="rsi_14", operator="lt", value=30.0),
        Condition(field="close", operator="gt", value=99999.0),
    ])]
    base = time.time()
    final = Tick(price=100, size=1, time=base)
    fanout_called.clear()
    await engine._evaluate("TEST", final)
    if len(fanout_called) == 1: print("  ✓ fanout (OR)")
    else: print(f"  ✗ expected 1, got {len(fanout_called)}"); sys.exit(1)

    # ========== case 8b:同條件改 AND 應 skip ==========
    print("[8b] AND logic 反例 — 同條件但 AND")
    reset_engine(engine, rb)
    engine._field_cache["TEST"]["rsi_14"] = 25.0
    engine._active = [make_active("c8b", logic="AND", conditions=[
        Condition(field="rsi_14", operator="lt", value=30.0),
        Condition(field="close", operator="gt", value=99999.0),
    ])]
    base = time.time()
    final = Tick(price=100, size=1, time=base)
    fanout_called.clear()
    await engine._evaluate("TEST", final)
    if len(fanout_called) == 0: print("  ✓ skipped (AND 第二個 False)")
    else: print(f"  ✗ expected 0, got {len(fanout_called)}"); sys.exit(1)

    # ========== case 9:symbols scope 不包就 skip ==========
    print("[9] symbols scope = [AAA] — 灌 BBB tick 應 skip")
    reset_engine(engine, rb, symbols=("AAA", "BBB"))
    engine._field_cache["AAA"]["rsi_14"] = 25.0
    engine._field_cache["BBB"]["rsi_14"] = 25.0
    engine._active = [make_active(
        "c9",
        scope=SymbolsScope(type="symbols", symbols=["AAA"]),
        conditions=[Condition(field="rsi_14", operator="lt", value=30.0)],
    )]
    base = time.time()
    final = Tick(price=100, size=1, time=base)
    fanout_called.clear()
    await engine._evaluate("BBB", final)
    if len(fanout_called) == 0: print("  ✓ BBB skipped (not in scope)")
    else: print(f"  ✗ expected 0, got {len(fanout_called)}"); sys.exit(1)
    # 補:灌 AAA 應觸發,確認 scope 邏輯雙向都對
    await engine._evaluate("AAA", final)
    if len(fanout_called) == 1: print("  ✓ AAA fanout (in scope)")
    else: print(f"  ✗ AAA expected 1, got {len(fanout_called)}"); sys.exit(1)

    # ========== case 10:多 active 各自獨立 cooldown ==========
    print("[10] 多 active 各自 cooldown — A + B 都 fire,A 進 cooldown 後再灌只 B fire")
    reset_engine(engine, rb)
    engine._field_cache["TEST"]["rsi_14"] = 25.0
    engine._active = [
        make_active("A", conditions=[Condition(field="rsi_14", operator="lt", value=30.0)]),
        make_active("B", conditions=[Condition(field="rsi_14", operator="lt", value=30.0)]),
    ]
    base = time.time()
    final = Tick(price=100, size=1, time=base)
    fanout_called.clear()
    await engine._evaluate("TEST", final)
    if len(fanout_called) == 2: print(f"  ✓ 首次:A+B 都 fanout: {[x[0] for x in fanout_called]}")
    else: print(f"  ✗ expected 2, got {len(fanout_called)}: {fanout_called}"); sys.exit(1)
    # 第二次:兩個都應在 cooldown
    fanout_called.clear()
    await engine._evaluate("TEST", final)
    if len(fanout_called) == 0: print("  ✓ 第二次:兩個都 cooldown skip")
    else: print(f"  ✗ expected 0, got {len(fanout_called)}"); sys.exit(1)
    # 只清 A 的 cooldown
    del engine._cooldown[("A", "TEST")]
    fanout_called.clear()
    await engine._evaluate("TEST", final)
    if len(fanout_called) == 1 and fanout_called[0][0] == "A":
        print(f"  ✓ 清 A cooldown 後:只 A fanout")
    else:
        print(f"  ✗ expected only A, got {fanout_called}"); sys.exit(1)

    # ========== case 11:empty ring_buffer 不噴錯 ==========
    print("[11] empty ring_buffer — window_condition 應該 return False 不噴錯")
    reset_engine(engine, rb)  # rb.ensure(TEST) 後立刻沒 append → buf 空
    engine._active = [make_active("c11", window_conditions=[
        WindowCondition(type="price_change_pct", window_seconds=60, operator="gt", value=1.0),
    ])]
    base = time.time()
    final = Tick(price=100, size=1, time=base)
    fanout_called.clear()
    try:
        await engine._evaluate("TEST", final)
    except Exception as e:
        print(f"  ✗ raised {type(e).__name__}: {e}"); sys.exit(1)
    if len(fanout_called) == 0: print("  ✓ no fanout, no exception")
    else: print(f"  ✗ expected 0, got {len(fanout_called)}"); sys.exit(1)

    print("\nAll signal_engine smoke tests passed ✓")


if __name__ == "__main__":
    asyncio.run(main())
