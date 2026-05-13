# Probe Signal Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 加兩支盤後可跑的訊號驗證 probe — `probe_signal_engine.py` 擴充 8 個 mock case + `probe_replay_ticks.py` 新增以今日 1 分 K replay user 的 active_signals。

**Architecture:** 沿用既有 `backend/scripts/probe_*.py` 慣例(非 pytest)。新 probe 完全 in-process,不啟動 ws_pool / background tasks,用 module-level monkey-patch 把 `time` 模組換成 FakeClock,讓歷史時間 candle 也能正確跑 ring_buffer.window() 跟 cooldown 邏輯。`engine._fanout` 也 monkey-patch 成 record-only,不污染 signals_log / WS broadcast。

**Tech Stack:** Python 3.12 / asyncio / Fubon SDK (`fubon_neo`) / Supabase Python client / Pydantic v2

**Spec:** `docs/superpowers/specs/2026-05-13-probe-signal-coverage-design.md`

---

## File Structure

| 路徑 | 動作 | 責任 |
|---|---|---|
| `backend/scripts/probe_signal_engine.py` | 修改(擴充) | 既有 3 case + 新增 8 mock case 涵蓋 window 類型 / filter / OR logic / scope / 多 active cooldown |
| `backend/scripts/probe_replay_ticks.py` | 新建 | 讀 DB 的 watchlist + active_signals → 拉今日 1 分 K → in-process replay → 印報告 |

兩支都不動 prod 程式碼(`backend/services/`),完全靠 monkey-patch + 直接呼叫 `engine._evaluate()` 跑邏輯。

---

## Task 1: 擴充 probe_signal_engine.py 加 8 mock case

**Files:**
- Modify: `backend/scripts/probe_signal_engine.py`(整檔重寫,保留既有 3 case 結構)

**目標:** 加 case 4-11(共 9 個 — case 8 + 8b 拆兩個 sub-case)。所有 case 純 mock,< 2 秒跑完。

### Step 1: 重寫 probe_signal_engine.py

完整覆寫(保留檔頭 docstring + import + 既有 case 1-3 邏輯 + 新增):

```python
"""Mock tick 進 signal_engine 驗:
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
```

- [ ] **Step 2: 跑 probe 驗全綠燈**

Run:
```powershell
& .\backend\.venv\Scripts\python.exe .\backend\scripts\probe_signal_engine.py
```

Expected: 印 11 個 case 各自 `✓`,最後 `All signal_engine smoke tests passed ✓`,exit 0。

若有 case 失敗 → 看哪個 `✗`,先檢查是 probe mock setup 錯還是 engine 真有 bug。常見地雷:
- `make_active` 沒帶 watchlist scope 時 → 預設 watchlist 但 field_cache 沒這 symbol → `_scope_includes` 回 False。`reset_engine()` 已 fix 這個。
- `Condition(value="cdp_ah")` 需要 cdp_ah 在 `ALL_FIELDS`(`backend/models/condition.py:48`) — 已驗在內。
- `make_active(... scope=SymbolsScope(...))` 時 `_scope_includes` 用 dict path? 走 Pydantic path,看 `signal_engine.py:247-260`。

- [ ] **Step 3: Commit**

```powershell
git add backend/scripts/probe_signal_engine.py
git commit -m "test(probe): extend probe_signal_engine 8 new mock cases

Cover volume_burst / trade_count / cross-field / indicator / OR-AND /
symbols scope / multi-active cooldown / empty ring_buffer.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: 建 probe_replay_ticks.py 骨架(env / init / read DB / FakeClock 結構)

**Files:**
- Create: `backend/scripts/probe_replay_ticks.py`

**目標:** 寫好基本架構 — load_dotenv / fubon+sb init / 讀 watchlist + active_signals / FakeClock 類別 / mock_fanout / Trigger dataclass。**還沒有 replay loop 跟 report**,執行時應該印「nothing to replay」或「(no triggers yet)」其中之一。

### Step 1: 建檔

Create `backend/scripts/probe_replay_ticks.py`:

```python
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
```

- [ ] **Step 2: 跑 probe 確認 init + DB 讀取都通**

Run:
```powershell
& .\backend\.venv\Scripts\python.exe .\backend\scripts\probe_replay_ticks.py
```

Expected(三選一,看 user 目前 DB 狀態):
- `USER_LABEL=loger 沒 watchlist — nothing to replay`(若沒自選股)
- `USER_LABEL=loger 沒 enabled active_signals — nothing to replay`(若有 watchlist 沒規則)
- `[init OK] USER_LABEL=loger, watchlist=N symbols, active_signals=M enabled` + `[stub] replay loop 還沒實作`(有規則)

任一情況都應該 exit 0 不噴 exception。

若 fubon init 失敗 → 看 `backend/.env` `FUBON_PERSONAL_ID` / `FUBON_API_KEY` 是否填正確。
若 supabase init 失敗 → 看 `SUPABASE_URL` / `SUPABASE_KEY`。

- [ ] **Step 3: Commit**

```powershell
git add backend/scripts/probe_replay_ticks.py
git commit -m "test(probe): probe_replay_ticks skeleton — env/init/db-read

骨架:load_dotenv + fubon/sb init + 讀 watchlist + refresh active_signals +
FakeClock class + Trigger dataclass。replay loop 跟 report Task 3-4 補。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: 加 replay loop + candle fetch + monkey-patch 到 probe_replay_ticks.py

**Files:**
- Modify: `backend/scripts/probe_replay_ticks.py`

**目標:** 加實際 replay 邏輯 — monkey-patch `engine._fanout`、安裝 FakeClock、fetch candles、轉 Tick、跑 `engine._evaluate()`。執行後 `triggers` list 會有資料。**還沒有 report formatting**,先印 raw triggers list 確認 replay 跑得通。

### Step 1: 改 main() 加 replay loop

把 `probe_replay_ticks.py` 的整個 `main()` 函數(從 `async def main()` 一路到 `[stub] replay loop 還沒實作` 那行)直接 replace 成下面這個完整版本:

```python
async def main() -> None:
    fubon = get_fubon()
    await fubon.init()
    sb = get_supabase()
    sb.init()
    if fubon.status.value != "ok" or sb.status.value != "ok":
        print(f"FAIL fubon={fubon.status.value} sb={sb.status.value}")
        sys.exit(1)

    label = get_user_label()

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

    # 要 replay 的 symbols = 所有 active_signal scope 涉及的 symbols(由 _refill_field_cache 算)
    replay_symbols = sorted(engine._field_cache.keys())
    if not replay_symbols:
        print(f"[warn] field_cache 空 — 沒任何 active_signal 的 scope 包到 symbol")
        sys.exit(0)

    # ---------- Monkey-patch engine._fanout 改成 record-only ----------
    triggers: list[Trigger] = []

    async def mock_fanout(active, symbol, tick):
        triggers.append(Trigger(
            triggered_at=tick.time,
            symbol=symbol,
            active_signal_id=active.id,
            active_signal_name=active.name,
            trigger_price=tick.price,
            trigger_volume=tick.size,
            summary=_summary(active, symbol, tick),
        ))

    engine._fanout = mock_fanout

    # ---------- 安裝 FakeClock(module-level patch)----------
    fake = FakeClock()
    orig_rb_time = rb_mod.time
    orig_se_time = se_mod.time
    rb_mod.time = fake
    se_mod.time = fake

    candle_total = 0
    candle_failed: list[str] = []
    rb = get_ring_buffer()

    try:
        for symbol in replay_symbols:
            try:
                resp = fubon.sdk.marketdata.rest_client.stock.intraday.candles(
                    symbol=symbol, timeframe="1"
                )
            except Exception as e:
                print(f"  [warn] {symbol}: API failed — {type(e).__name__}: {e}")
                candle_failed.append(symbol)
                continue

            data = resp.get("data") if isinstance(resp, dict) else None
            if not data:
                print(f"  [warn] {symbol}: no candles data (盤前 / 停牌?)")
                candle_failed.append(symbol)
                continue

            data_sorted = sorted(data, key=lambda x: x.get("date", ""))
            candle_total += len(data_sorted)

            # clear ring_buffer for this symbol 避免互污染
            rb.discard(symbol)
            rb.ensure(symbol)

            for c in data_sorted:
                date_str = c.get("date", "")
                if not date_str:
                    continue
                # Fubon `date` 可能是 ISO8601 帶 TZ(`2026-05-13T09:00:00+08:00`)
                # 也可能是純 date-time 字串。fromisoformat 兩種都能吃,Z 要先換成 +00:00
                try:
                    dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                    epoch = dt.timestamp()
                except (ValueError, TypeError):
                    continue
                tick = Tick(
                    price=float(c.get("close", 0)),
                    size=int(c.get("volume", 0)),
                    time=epoch,
                )
                fake.now = tick.time
                rb.append(symbol, tick)
                await engine._evaluate(symbol, tick)
    finally:
        rb_mod.time = orig_rb_time
        se_mod.time = orig_se_time

    print(f"\n[replay done] symbols={len(replay_symbols)} candles={candle_total} "
          f"failed={len(candle_failed)} triggers={len(triggers)}")
    # raw dump for sanity check (Task 4 改成正式 report)
    for t in triggers[:20]:
        ts = datetime.fromtimestamp(t.triggered_at).strftime("%H:%M:%S")
        print(f"  {ts} {t.symbol} {t.active_signal_name} @{t.trigger_price} | {t.summary}")
    if len(triggers) > 20:
        print(f"  ... (還有 {len(triggers) - 20} 筆)")
```

### Step 2: 加 `_summary` helper function

在 `_get(...)` helper 之後、`main()` 之前加:

```python
def _summary(active, symbol, tick) -> str:
    """產 human-readable 觸發摘要 — 給 report 用。"""
    parts: list[str] = []
    f = active.filter_json
    rb = get_ring_buffer()
    engine = get_signal_engine()
    cache = engine._field_cache.get(symbol, {})

    wcs = _get(f, "window_conditions", []) or []
    for wc in wcs:
        wc_type = _get(wc, "type")
        wc_secs = _get(wc, "window_seconds")
        ticks = rb.window(symbol, wc_secs) if wc_secs else []
        if wc_type == "price_change_pct" and ticks:
            start = ticks[0].price
            pct = (tick.price - start) / start * 100 if start else 0.0
            parts.append(f"start={start:.2f}({pct:+.2f}%)")
        elif wc_type == "volume_burst":
            vol = sum(t.size for t in ticks)
            parts.append(f"vol={vol}")
        elif wc_type == "trade_count":
            parts.append(f"ticks={len(ticks)}")

    cs = _get(f, "conditions", []) or []
    for c in cs:
        field = _get(c, "field")
        if field and field != "close":
            val = cache.get(field)
            if val is not None:
                parts.append(f"{field}={val:.2f}" if isinstance(val, (int, float)) else f"{field}={val}")

    return " ".join(parts) if parts else "-"
```

- [ ] **Step 3: 跑 probe 看 replay 通不通**

Run:
```powershell
& .\backend\.venv\Scripts\python.exe .\backend\scripts\probe_replay_ticks.py
```

Expected:
- `[init OK]` 跟 Task 2 一致
- 接著看到 `[replay done] symbols=N candles=M failed=K triggers=T`
- 如果 T > 0 → 印觸發行(時間 + symbol + 規則 + 價)
- 整個跑完應 exit 0,**不**寫進 signals_log

關鍵 sanity 檢查:
1. `candles=M` 不為 0(M 大約 = replay_symbols × 270,2026-05-13 是交易日盤後跑的話)
2. `failed` 為 0 或少數(若某檔停牌則合理 fail)
3. **跑完直接到 supabase 查**:
   ```sql
   select count(*) from signals_log where created_at > now() - interval '5 minutes';
   ```
   應該 = 0(monkey-patch 攔截成功)

若 `candles=0 failed=N` → 看 warning 訊息。常見:
- API 401:check `backend/.env` 的 `FUBON_API_KEY`
- "no candles data":今天還沒開盤 / 那檔停牌 / `timeframe` 參數錯
- 跑日:非交易日(週末)Fubon 可能回空 — 換個交易日盤後跑

若 triggers=0 但你預期會有 → 可能 `_field_cache` 沒包到的 symbol(看 `[replay done] symbols=`)。

- [ ] **Step 4: Commit**

```powershell
git add backend/scripts/probe_replay_ticks.py
git commit -m "test(probe): probe_replay_ticks replay loop + FakeClock

灌 Fubon 今日 1 分 K candles → ring_buffer → engine._evaluate(),
module-level monkey-patch ring_buffer.time / signal_engine.time 成 FakeClock
讓 cooldown / window cutoff 用模擬 tick time 而非 wall-clock。
mock_fanout 攔截 fanout 改成 in-memory record(不寫 signals_log)。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: 加正式 report formatting

**Files:**
- Modify: `backend/scripts/probe_replay_ticks.py`

**目標:** 把 Task 3 末段那 5 行 raw dump 換成 §4.4 spec 那種完整 console table 報告(規則清單 + 觸發明細 + 每規則統計)。

### Step 1: 加 `_print_report` function

在 `_summary` 之後、`main()` 之前加:

```python
def _render_rule_summary(active) -> str:
    """單行 render 一條 active_signal 的條件,給 report header 用。"""
    f = active.filter_json
    logic = _get(f, "logic", "AND")
    parts: list[str] = []
    for wc in (_get(f, "window_conditions", []) or []):
        parts.append(
            f"{_get(wc, 'type')} {_get(wc, 'operator')} {_get(wc, 'value')} "
            f"({_get(wc, 'window_seconds')}s)"
        )
    for c in (_get(f, "conditions", []) or []):
        parts.append(f"{_get(c, 'field')} {_get(c, 'operator')} {_get(c, 'value')}")
    return (" " + logic + " ").join(parts)


def _print_report(
    *,
    label: str,
    watchlist: list[str],
    actives: list,
    replay_symbols: list[str],
    candle_total: int,
    candle_failed: list[str],
    triggers: list[Trigger],
) -> None:
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"\n=== probe_replay_ticks 報告 ({today}, USER_LABEL={label}) ===")

    wl_show = ", ".join(watchlist[:10]) + ("..." if len(watchlist) > 10 else "")
    print(f"Watchlist: {len(watchlist)} symbols ({wl_show})")

    print(f"Active signals: {len(actives)} enabled")
    for a in actives:
        rule = _render_rule_summary(a)
        print(f'  - "{a.name}" — {rule}, cooldown={a.cooldown_seconds}s')

    fail_show = f", failed: {', '.join(candle_failed)}" if candle_failed else ""
    print(f"\nReplayed: {len(replay_symbols)} symbols | "
          f"candles fetched: {candle_total} | failed: {len(candle_failed)}{fail_show}")

    if not triggers:
        print("\n觸發明細: (無 — 沒有規則命中)")
    else:
        print(f"\n觸發明細(按時間排序):")
        print(f"  {'時間':<8}  {'symbol':<6}  {'規則':<28}  {'價':>10}  window/cache 摘要")
        print(f"  {'-'*8}  {'-'*6}  {'-'*28}  {'-'*10}  {'-'*30}")
        for t in sorted(triggers, key=lambda x: x.triggered_at):
            ts = datetime.fromtimestamp(t.triggered_at).strftime("%H:%M:%S")
            name = (t.active_signal_name[:26] + "..") if len(t.active_signal_name) > 28 else t.active_signal_name
            print(f"  {ts:<8}  {t.symbol:<6}  {name:<28}  {t.trigger_price:>10.2f}  {t.summary}")

    print("\n每規則統計:")
    counts = Counter(t.active_signal_id for t in triggers)
    for a in actives:
        symbols_hit = {t.symbol for t in triggers if t.active_signal_id == a.id}
        n = counts.get(a.id, 0)
        suffix = f" ({len(symbols_hit)} symbols)" if n > 0 else "  ← 今日完全沒觸發"
        print(f"  {a.name:<30} × {n} 次{suffix}")
```

### Step 2: 把 main() 末段 raw dump 換成正式 report

在 `probe_replay_ticks.py` main() 的最後,**刪掉**:

```python
    print(f"\n[replay done] symbols={len(replay_symbols)} candles={candle_total} "
          f"failed={len(candle_failed)} triggers={len(triggers)}")
    # raw dump for sanity check (Task 4 改成正式 report)
    for t in triggers[:20]:
        ts = datetime.fromtimestamp(t.triggered_at).strftime("%H:%M:%S")
        print(f"  {ts} {t.symbol} {t.active_signal_name} @{t.trigger_price} | {t.summary}")
    if len(triggers) > 20:
        print(f"  ... (還有 {len(triggers) - 20} 筆)")
```

換成:

```python
    _print_report(
        label=label,
        watchlist=watchlist,
        actives=engine._active,
        replay_symbols=replay_symbols,
        candle_total=candle_total,
        candle_failed=candle_failed,
        triggers=triggers,
    )
```

- [ ] **Step 3: 跑 probe 驗報告格式**

Run:
```powershell
& .\backend\.venv\Scripts\python.exe .\backend\scripts\probe_replay_ticks.py
```

Expected: 印類似 spec §4.4 的格式:

```
=== probe_replay_ticks 報告 (2026-05-13, USER_LABEL=loger) ===
Watchlist: 5 symbols (2330, 6505, 0050, 3008, 2454)
Active signals: 3 enabled
  - "60s 漲 1%" — price_change_pct gt 1.0 (60s), cooldown=300s
  - "rsi<30 + 收跌" — rsi_14 lt 30.0 AND close lt cdp, cooldown=600s
  - "close > cdp_ah" — close gt cdp_ah, cooldown=1800s

Replayed: 5 symbols | candles fetched: 1342 | failed: 0

觸發明細(按時間排序):
  時間      symbol  規則                          價          window/cache 摘要
  --------  ------  ----------------------------  ----------  ------------------------------
  09:23:00  2330    60s 漲 1%                          635.00  start=628.50(+1.03%)
  ...

每規則統計:
  60s 漲 1%                      × 8 次 (3 symbols)
  rsi<30 + 收跌                  × 2 次 (1 symbols)
  close > cdp_ah                 × 0 次  ← 今日完全沒觸發
```

對齊不完美也沒關係(中文寬度問題),只要看得懂。

- [ ] **Step 4: Commit**

```powershell
git add backend/scripts/probe_replay_ticks.py
git commit -m "test(probe): probe_replay_ticks report formatting

加 _print_report — 規則清單 + 觸發明細 table + 每規則統計。
取代 Task 3 留下的 raw dump。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: 驗收 — 雙 probe 都跑一次 + supabase 確認無污染

**Files:** (無修改,只驗證)

- [ ] **Step 1: 跑 probe_signal_engine.py**

Run:
```powershell
& .\backend\.venv\Scripts\python.exe .\backend\scripts\probe_signal_engine.py
```

Expected: 11 case 全綠燈 + `All signal_engine smoke tests passed ✓`,exit 0。

- [ ] **Step 2: 跑 probe_replay_ticks.py**

Run:
```powershell
& .\backend\.venv\Scripts\python.exe .\backend\scripts\probe_replay_ticks.py
```

Expected: 印正式報告 + exit 0。**不**噴 exception。

- [ ] **Step 3: 確認 supabase 沒被污染**

到 https://supabase.com/dashboard/project/xtiekjxpbrchjvtlnbbg/sql/new 跑:

```sql
select count(*) as recent_count
from signals_log
where created_at > now() - interval '5 minutes';
```

Expected: `recent_count` = 0(probe 不應寫 row)。

若 > 0 → 表示 `mock_fanout` 沒裝對。檢查 `engine._fanout = mock_fanout` 是在 `_evaluate()` 第一次呼叫**之前**設好。

- [ ] **Step 4: (可選) 用 git log 確認 commit 鏈乾淨**

```powershell
git log -5 --oneline
```

Expected:
```
<hash> test(probe): 雙 probe 驗收(若這步要 commit)
<hash> test(probe): probe_replay_ticks report formatting
<hash> test(probe): probe_replay_ticks replay loop + FakeClock
<hash> test(probe): probe_replay_ticks skeleton — env/init/db-read
<hash> test(probe): extend probe_signal_engine 8 new mock cases
```

Task 5 本身不需要 commit(沒檔案改動)。

---

## 驗收標準總結

對應 spec §7:
- ✅ `probe_signal_engine.py` 跑完印 `All signal_engine smoke tests passed ✓` 且 exit 0
- ✅ `probe_replay_ticks.py` 盤後跑完 不噴 exception / 印報告 / signals_log 沒新 row
