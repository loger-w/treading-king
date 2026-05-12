# Phase 3 Realtime + Intraday Chart Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 trading-king 加入即時 WebSocket 訂閱、訊號 evaluator、Watchlist 內嵌分時走勢圖（含 VWAP + CDP toggle），讓 user 可組合「時窗條件」+「跨指標條件」的訊號規則並即時收到推播。

**Architecture:** 後端 fubon WS pool（refcount registry + circuit breaker）→ ring_buffer per-symbol time-window deque → signal_engine async evaluator → 前端 WS broadcaster + supabase batch writer。分時走勢用 REST `intraday.candles`（含富邦算好的 `average` = VWAP）每分鐘輪詢 + WS tick 更新末端。CDP 從 `daily_ohlc` 表（watchlist 加入時 backfill）算出 5 條水平線。

**Tech Stack:** Python 3.13 / FastAPI / fubon-neo SDK v2.2.8 (sync, asyncio.to_thread wrap) / supabase-py v2 / Pydantic v2 / React 18 / TypeScript 5 / Tailwind 3 / Vite 5

**Spec:** `docs/superpowers/specs/2026-05-12-phase-3-realtime-design.md`

**Style conventions（沿用 Phase 2a/2b）:**
- Backend：`from __future__ import annotations`、type hint、logger、singleton via `get_X()`、所有 sync SDK call wrap `asyncio.to_thread`
- 測試風格：inline `if __name__ == "__main__":` smoke（沿用 `services/rate_limiter.py` 跟 `scripts/probe_*.py` 模式），不引 pytest
- Frontend：純 React + Tailwind + 純 SVG（不引 chart library），Editorial Dark + 台股紅綠 + 中文 UI
- 驗證：backend 用 module import + smoke script；frontend 用 `npx vite build`（不用 `tsc -b` 因 tsconfig.node.json 既有 bug）

---

## File Structure

### Backend (建立)

```
backend/
├── models/
│   └── condition.py                    [MODIFY] 加 5 cdp_* fields + WindowCondition + Scope + ActiveSignalCreate/Out
├── services/
│   ├── ring_buffer.py                  [CREATE] per-symbol time-window deque + thread lock
│   ├── fubon_ws.py                     [CREATE] WS pool + refcount + reconnect + circuit breaker
│   ├── signal_engine.py                [CREATE] bounded queue + evaluator + cooldown + backpressure
│   ├── supabase_writer.py              [CREATE] 500ms batch flush 到 signals_log
│   ├── cdp.py                          [CREATE] 5 線算 + in-memory cache + backfill
│   └── overnight.py                    [CREATE] 8:25 cron — fubon relogin + ws reconnect
├── routes/
│   ├── watchlist.py                    [CREATE] GET/POST/DELETE
│   ├── active_signals.py               [CREATE] GET/POST/PUT/DELETE
│   ├── signals_history.py              [CREATE] GET /api/signals/history
│   ├── candles.py                      [CREATE] GET /api/candles/{symbol}/intraday
│   ├── cdp.py                          [CREATE] GET /api/cdp/{symbol}
│   ├── ws.py                           [CREATE] WS /ws/realtime
│   └── health.py                       [MODIFY] 加 ws_connections + signal_engine 欄
├── main.py                             [MODIFY] 註冊新 routes + lifespan startup 順序
├── scripts/
│   ├── probe_ring_buffer.py            [CREATE] (其實是 inline __main__)
│   ├── probe_signal_engine.py          [CREATE]
│   ├── probe_ws_pool.py                [CREATE]
│   ├── probe_writer.py                 [CREATE]
│   ├── probe_cdp.py                    [CREATE]
│   └── probe_e2e_signal.py             [CREATE]
└── ws_broadcaster.py                   [CREATE] 前端 WS connection set + broadcast helper
```

### Frontend (建立)

```
frontend/src/
├── lib/
│   └── api.ts                          [MODIFY] 加 ActiveSignal/WindowCondition/IntradayCandle/etc types + methods
├── hooks/
│   ├── useWatchlist.ts                 [CREATE]
│   ├── useIntradayCandles.ts           [CREATE]
│   ├── useSignalsStream.ts             [CREATE]
│   └── useActiveSignals.ts             [CREATE]
├── pages/
│   ├── Watchlist.tsx                   [CREATE]
│   └── Signals.tsx                     [CREATE]
├── components/
│   ├── IntradayChart.tsx               [CREATE]
│   ├── Sparkline.tsx                   [CREATE]
│   ├── SignalCard.tsx                  [CREATE]
│   ├── ActiveSignalEditor.tsx          [CREATE]
│   └── SymbolSearch.tsx                [CREATE]
├── pages/Health.tsx                    [MODIFY] 加 ws_connections + signal_engine row
└── App.tsx                             [MODIFY] 啟用 watchlist/signals tab
```

### Supabase (建立)

```
supabase/migrations/
└── 0004_realtime_signals.sql           [CREATE] active_signals + signals_log + daily_ohlc + RLS
```

---

## Task Group 1 — Migration 0004 + Pydantic 擴充

### Task 1.1: Migration 0004（建表 + RLS）

**Files:**
- Create: `supabase/migrations/0004_realtime_signals.sql`

- [ ] **Step 1: 寫 migration 檔**

```sql
-- supabase/migrations/0004_realtime_signals.sql
-- Phase 3 — active_signals + signals_log + daily_ohlc

create table if not exists active_signals (
  id                uuid primary key default gen_random_uuid(),
  name              text not null,
  filter_json       jsonb not null,
  scope             jsonb not null,
  cooldown_seconds  int  default 1800 check (cooldown_seconds between 60 and 86400),
  ignore_auctions   boolean default true,
  enabled           boolean default true,
  created_at        timestamptz default now()
);

create index if not exists idx_active_signals_enabled
  on active_signals(enabled) where enabled;

create table if not exists signals_log (
  id                bigserial primary key,
  active_signal_id  uuid references active_signals(id),
  symbol            text references symbols(symbol),
  triggered_at      timestamptz default now(),
  trigger_price     numeric,
  trigger_volume    bigint,
  context_json      jsonb
);

create index if not exists idx_signals_log_triggered_desc
  on signals_log(triggered_at desc);
create index if not exists idx_signals_log_symbol_time
  on signals_log(symbol, triggered_at desc);
create index if not exists idx_signals_log_active_signal_time
  on signals_log(active_signal_id, triggered_at desc);

create table if not exists daily_ohlc (
  symbol  text not null references symbols(symbol),
  date    date not null,
  open    numeric,
  high    numeric,
  low     numeric,
  close   numeric,
  primary key (symbol, date)
);

create index if not exists idx_daily_ohlc_date on daily_ohlc(date);

alter table active_signals enable row level security;
alter table signals_log    enable row level security;
alter table daily_ohlc     enable row level security;

create policy "anon can read active_signals" on active_signals for select to anon, authenticated using (true);
create policy "anon can read signals_log"    on signals_log    for select to anon, authenticated using (true);
create policy "anon can read daily_ohlc"     on daily_ohlc     for select to anon, authenticated using (true);
```

- [ ] **Step 2: 套到 supabase**

如果 supabase MCP 已 auth：
```
mcp__supabase__apply_migration(name="realtime_signals", query=<above SQL>)
```
如果 MCP 沒 auth：先 `mcp__supabase__authenticate` → user 開瀏覽器授權 → 再 apply。
完全不行就 user 走 https://supabase.com/dashboard/project/xtiekjxpbrchjvtlnbbg/sql/new 手動套。

- [ ] **Step 3: 驗證表存在**

```
mcp__supabase__list_tables(schemas=["public"], verbose=False)
```
Expected: tables 含 `active_signals`(0 列), `signals_log`(0 列), `daily_ohlc`(0 列)，三者 rls_enabled=true。

- [ ] **Step 4: Commit**

```bash
git add supabase/migrations/0004_realtime_signals.sql
git commit -m "feat(phase3): migration 0004 — active_signals + signals_log + daily_ohlc"
```

---

### Task 1.2: Pydantic DSL 擴充

**Files:**
- Modify: `backend/models/condition.py`

- [ ] **Step 1: 擴充 ConditionField 加 5 個 cdp_***

Read `backend/models/condition.py`，找到既有 `ConditionField = Literal[...]` 區塊，把 16 個 field 改成 21 個（加 cdp_ah/nh/cdp/nl/al）。同時擴 ALL_FIELDS tuple。

```python
# backend/models/condition.py 改動段落

ConditionField = Literal[
    "close", "change_pct", "volume", "amount",
    "rsi_14", "macd", "macd_signal",
    "kdj_k", "kdj_d", "kdj_j",
    "sma_5", "sma_20", "sma_60",
    "bbands_upper", "bbands_middle", "bbands_lower",
    # Phase 3 新增（從 daily_ohlc 算出的 5 線）
    "cdp_ah", "cdp_nh", "cdp", "cdp_nl", "cdp_al",
]

ALL_FIELDS: tuple[ConditionField, ...] = (
    "close", "change_pct", "volume", "amount",
    "rsi_14", "macd", "macd_signal",
    "kdj_k", "kdj_d", "kdj_j",
    "sma_5", "sma_20", "sma_60",
    "bbands_upper", "bbands_middle", "bbands_lower",
    "cdp_ah", "cdp_nh", "cdp", "cdp_nl", "cdp_al",
)
```

- [ ] **Step 2: 加 WindowCondition**

在 condition.py 既有 `Filter` 之後加：

```python
WindowConditionType = Literal["price_change_pct", "volume_burst", "trade_count"]
WindowSeconds = Literal[60, 180, 300, 600, 1800]


class WindowCondition(BaseModel):
    """即時時窗條件 — 從 ring_buffer 算 N 秒內的數值。

    type:
      - price_change_pct: (latest_price / window_start_price - 1) * 100
      - volume_burst: 窗口累積成交量 / 過去 N 個窗口平均成交量
      - trade_count: 窗口內成交筆數
    """
    type: WindowConditionType
    window_seconds: WindowSeconds
    operator: Literal["gt", "gte", "lt", "lte"]
    value: float
```

- [ ] **Step 3: 加 ActiveFilter**

在 WindowCondition 之後：

```python
class ActiveFilter(Filter):
    """即時訊號專用 Filter — 在 Filter 之上加時窗條件。"""
    window_conditions: list[WindowCondition] = Field(default_factory=list)
```

- [ ] **Step 4: 加 Scope discriminated union**

```python
class WatchlistScope(BaseModel):
    type: Literal["watchlist"]


class SymbolsScope(BaseModel):
    type: Literal["symbols"]
    symbols: list[str] = Field(min_length=1, max_length=500)


Scope = WatchlistScope | SymbolsScope
```

- [ ] **Step 5: 加 ActiveSignalCreate / ActiveSignalOut**

```python
class ActiveSignalCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    filter_json: ActiveFilter
    scope: Scope
    cooldown_seconds: int = Field(default=1800, ge=60, le=86400)
    ignore_auctions: bool = True
    enabled: bool = True


class ActiveSignalOut(ActiveSignalCreate):
    id: str
    created_at: str
```

- [ ] **Step 6: 驗證 import 跟 serialize**

```powershell
$env:PYTHONPATH = "C:\side-project\trading-king\backend"; & "C:\side-project\trading-king\backend\.venv\Scripts\python.exe" -c "from models.condition import ActiveSignalCreate, ActiveFilter, WindowCondition, WatchlistScope, Condition; s = ActiveSignalCreate(name='test', filter_json=ActiveFilter(conditions=[Condition(field='cdp_ah', operator='gt', value=600)], window_conditions=[WindowCondition(type='price_change_pct', window_seconds=300, operator='gt', value=2)]), scope=WatchlistScope(type='watchlist')); print(s.model_dump_json(indent=2))"
```

Expected: 一段 JSON 印出來，含 `cdp_ah` field 跟 `window_conditions` 跟 `scope: {type: 'watchlist'}`。

- [ ] **Step 7: Commit**

```bash
git add backend/models/condition.py
git commit -m "feat(phase3): pydantic DSL 擴充 — cdp fields + WindowCondition + Scope + ActiveSignal models"
```

---

## Task Group 2 — ring_buffer service

### Task 2.1: ring_buffer 實作 + inline smoke

**Files:**
- Create: `backend/services/ring_buffer.py`

- [ ] **Step 1: 寫 inline smoke test 框架（先佔位，跑會 fail）**

新建 `backend/services/ring_buffer.py`：

```python
"""Per-symbol 時間窗 deque — 給 signal_engine 算 N 秒內的價量變化用。

Plan §Phase 3 §4.2。callback path 永遠不建 entry/lock；subscribe 時預建。
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque

logger = logging.getLogger(__name__)

MAX_WINDOW_SECONDS = 1800   # 30 分鐘
DEQUE_MAXLEN = 5000          # OOM 防呆


@dataclass
class Tick:
    price: float
    size: int
    time: float  # epoch seconds


class RingBuffer:
    """thread-safe per-symbol tick buffer。"""

    def __init__(self) -> None:
        self._buffers: dict[str, Deque[Tick]] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._registry_lock = threading.Lock()

    def ensure(self, symbol: str) -> None:
        """subscribe 時呼叫，預建 entry+lock。callback path 不可呼叫。"""
        with self._registry_lock:
            if symbol not in self._buffers:
                self._buffers[symbol] = deque(maxlen=DEQUE_MAXLEN)
                self._locks[symbol] = threading.Lock()

    def discard(self, symbol: str) -> None:
        """unsubscribe 且 refcount==0 才呼叫。"""
        with self._registry_lock:
            self._buffers.pop(symbol, None)
            self._locks.pop(symbol, None)

    def append(self, symbol: str, tick: Tick) -> None:
        """從 fubon WS callback 餵進來。symbol 必須先 ensure 過。"""
        lock = self._locks.get(symbol)
        if lock is None:
            logger.warning("append before ensure: %s — dropping tick", symbol)
            return
        buf = self._buffers[symbol]
        with lock:
            buf.append(tick)
            # tail trim：砍超過 max window 的舊 tick
            cutoff = tick.time - MAX_WINDOW_SECONDS
            while buf and buf[0].time < cutoff:
                buf.popleft()

    def window(self, symbol: str, seconds: int) -> list[Tick]:
        """拿過去 N 秒的 ticks（含 latest），ascending by time。"""
        lock = self._locks.get(symbol)
        if lock is None:
            return []
        buf = self._buffers[symbol]
        with lock:
            if not buf:
                return []
            cutoff = time.time() - seconds
            return [t for t in buf if t.time >= cutoff]

    def latest(self, symbol: str) -> Tick | None:
        lock = self._locks.get(symbol)
        if lock is None:
            return None
        buf = self._buffers[symbol]
        with lock:
            return buf[-1] if buf else None

    def has(self, symbol: str) -> bool:
        return symbol in self._buffers


_default: RingBuffer | None = None


def get_ring_buffer() -> RingBuffer:
    global _default
    if _default is None:
        _default = RingBuffer()
    return _default


# ----------------------- inline smoke -----------------------

if __name__ == "__main__":
    import sys
    from concurrent.futures import ThreadPoolExecutor

    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    GREEN, RED, YELLOW, RESET = "\033[92m", "\033[91m", "\033[93m", "\033[0m"

    def step(n, t): print(f"\n{YELLOW}[Test {n}] {t}{RESET}")
    def ok(m): print(f"{GREEN}  ✓ {m}{RESET}")
    def fail(m): print(f"{RED}  ✗ {m}{RESET}"); sys.exit(1)

    buf = RingBuffer()

    step(1, "ensure 後 append 跟 latest 對齊")
    buf.ensure("2330")
    now = time.time()
    buf.append("2330", Tick(price=580, size=10, time=now))
    last = buf.latest("2330")
    if last and last.price == 580: ok("latest 拿得到")
    else: fail(f"latest 不對: {last}")

    step(2, "append 沒 ensure → drop + warn")
    buf.append("9999", Tick(price=1, size=1, time=now))
    if buf.latest("9999") is None: ok("沒 leak entry")
    else: fail("9999 entry 被建出來了")

    step(3, "window 截取過去 N 秒")
    buf.ensure("2317")
    base = time.time()
    for i in range(10):
        buf.append("2317", Tick(price=100 + i, size=1, time=base - (9 - i) * 1.0))
    w = buf.window("2317", seconds=5)
    if 4 <= len(w) <= 6: ok(f"window(5s) → {len(w)} 筆 (預期 5±1)")
    else: fail(f"window 不對: 拿到 {len(w)} 筆")

    step(4, "tail trim — 超過 1800s 的會被砍")
    buf.ensure("2454")
    old_time = time.time() - 2000  # 33 分鐘前
    buf.append("2454", Tick(price=1, size=1, time=old_time))
    buf.append("2454", Tick(price=2, size=1, time=time.time()))
    w = buf.window("2454", seconds=2000)
    if len(w) == 1: ok("舊 tick 已被 trim")
    else: fail(f"trim 失敗: {len(w)} 筆")

    step(5, "多執行緒同時 append + window 不爆")
    buf.ensure("3008")
    base = time.time()
    def writer():
        for i in range(200):
            buf.append("3008", Tick(price=1000 + i % 10, size=1, time=base + i * 0.001))
    def reader():
        for _ in range(200):
            buf.window("3008", seconds=1)
    with ThreadPoolExecutor(max_workers=8) as ex:
        for _ in range(4):
            ex.submit(writer)
            ex.submit(reader)
    last = buf.latest("3008")
    if last is not None: ok(f"無爆，latest price={last.price}")
    else: fail("multithread race 跑完沒資料")

    step(6, "discard")
    buf.discard("2330")
    if buf.latest("2330") is None and not buf.has("2330"): ok("entry 移除")
    else: fail("discard 沒清乾淨")

    print(f"\n{GREEN}All ring_buffer smoke tests passed ✓{RESET}")
```

- [ ] **Step 2: 跑 smoke 驗證 6 個 test 都 pass**

```powershell
& "C:\side-project\trading-king\backend\.venv\Scripts\python.exe" "C:\side-project\trading-king\backend\services\ring_buffer.py"
```

Expected: 6 個 `✓` + `All ring_buffer smoke tests passed ✓`。

- [ ] **Step 3: Commit**

```bash
git add backend/services/ring_buffer.py
git commit -m "feat(phase3): ring_buffer — per-symbol time-window deque + thread-safe + 6 smoke tests"
```

---
## Task Group 3 — fubon_ws WS pool

### Task 3.1: WS pool basic + refcount

**Files:**
- Create: `backend/services/fubon_ws.py`

- [ ] **Step 1: 建檔含 WSPool class skeleton + refcount**

```python
"""富邦 WS 連線池 — refcount registry 解 plan §R1（兩 owner 同 symbol 取消其一不互踩）。

重要：富邦 WS callback 是 sync (在 fubon SDK thread)，要 bridge 到 asyncio。
我們在 startup 時 cache main loop reference，sync callback 用 loop.call_soon_threadsafe
把 tick 寫進 ring_buffer + signal_engine queue。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from enum import Enum
from typing import Any, Awaitable, Callable

from services import alerts
from services.fubon_client import FubonStatus, get_fubon
from services.ring_buffer import Tick, get_ring_buffer

logger = logging.getLogger(__name__)

# Plan §Phase 3：每條 WS 200 sub 上限、最多 3 連線（容量 600 cover ≤500 + buffer）
WS_PER_CONN_CAP = 200
MAX_CONNS = 3
RECONNECT_DELAYS = (1, 2, 4, 8, 16, 30, 60)  # exponential backoff，cap 60
CIRCUIT_OPEN_THRESHOLD = 5  # 連續失敗次數


class WSPoolStatus(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    CIRCUIT_OPEN = "circuit_open"


class WSPool:
    """單例。Owner 是字串（"watchlist" / active_signal_id 等），refcount 解 R1。"""

    def __init__(self) -> None:
        # symbol → set of owner_id
        self._refcount: dict[str, set[str]] = defaultdict(set)
        # symbol → 屬於哪條 WS connection (0..MAX_CONNS-1)
        self._symbol_to_conn: dict[str, int] = {}
        # connection idx → list of subscribed symbols
        self._conn_subs: dict[int, set[str]] = defaultdict(set)
        # ws handles (lazy create)
        self._ws_handles: dict[int, Any] = {}
        self._lock = asyncio.Lock()
        self._status = WSPoolStatus.OK
        self._reconnect_failures = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._on_tick: Callable[[str, Tick], Awaitable[None]] | None = None

    @property
    def status(self) -> WSPoolStatus:
        return self._status

    def total_subscribed(self) -> int:
        return sum(len(s) for s in self._conn_subs.values())

    def conn_count(self) -> int:
        return len(self._ws_handles)

    def set_tick_callback(self, fn: Callable[[str, Tick], Awaitable[None]]) -> None:
        """signal_engine.start() 時呼叫，註冊 tick 處理 callback。"""
        self._on_tick = fn

    async def start(self) -> None:
        """lifespan startup 時呼叫。"""
        self._loop = asyncio.get_running_loop()
        logger.info("WSPool started, capacity=%d", MAX_CONNS * WS_PER_CONN_CAP)

    async def subscribe(self, symbol: str, owner_id: str) -> None:
        """加 owner；refcount 0→1 才真打富邦訂閱。"""
        async with self._lock:
            if symbol in self._refcount and owner_id in self._refcount[symbol]:
                return  # already
            need_real_sub = symbol not in self._symbol_to_conn
            self._refcount[symbol].add(owner_id)
            if need_real_sub:
                conn_idx = self._pick_conn_with_capacity()
                if conn_idx is None:
                    self._refcount[symbol].discard(owner_id)
                    raise RuntimeError(
                        f"WS pool capacity full ({MAX_CONNS * WS_PER_CONN_CAP})"
                    )
                self._symbol_to_conn[symbol] = conn_idx
                self._conn_subs[conn_idx].add(symbol)
                # ring_buffer 一定要 ensure 在 sub 之前，才不會 callback 拿不到 lock
                get_ring_buffer().ensure(symbol)
                await self._real_subscribe(conn_idx, [symbol])

    async def unsubscribe(self, symbol: str, owner_id: str) -> None:
        async with self._lock:
            owners = self._refcount.get(symbol)
            if not owners or owner_id not in owners:
                return
            owners.discard(owner_id)
            if owners:
                return  # still has other owners
            # last owner — really unsubscribe
            del self._refcount[symbol]
            conn_idx = self._symbol_to_conn.pop(symbol, None)
            if conn_idx is not None:
                self._conn_subs[conn_idx].discard(symbol)
                await self._real_unsubscribe(conn_idx, [symbol])
            get_ring_buffer().discard(symbol)

    def _pick_conn_with_capacity(self) -> int | None:
        for idx in range(MAX_CONNS):
            if len(self._conn_subs.get(idx, set())) < WS_PER_CONN_CAP:
                return idx
        return None

    # --------- 真打富邦的 sync 動作（asyncio.to_thread wrap） ---------

    async def _real_subscribe(self, conn_idx: int, symbols: list[str]) -> None:
        ws = await self._ensure_handle(conn_idx)
        if ws is None:
            return
        try:
            await asyncio.to_thread(
                ws.subscribe, {"channel": "trades", "symbols": symbols}
            )
            logger.info("conn[%d] subscribed: %s (total=%d)", conn_idx, symbols, len(self._conn_subs[conn_idx]))
        except Exception as e:
            logger.error("subscribe failed conn[%d] symbols=%s: %s", conn_idx, symbols, e)

    async def _real_unsubscribe(self, conn_idx: int, symbols: list[str]) -> None:
        ws = self._ws_handles.get(conn_idx)
        if ws is None:
            return
        try:
            await asyncio.to_thread(
                ws.unsubscribe, {"channel": "trades", "symbols": symbols}
            )
            logger.info("conn[%d] unsubscribed: %s", conn_idx, symbols)
        except Exception as e:
            logger.warning("unsubscribe failed (ignored) conn[%d] %s: %s", conn_idx, symbols, e)

    async def _ensure_handle(self, conn_idx: int) -> Any:
        """lazy create + connect。"""
        if conn_idx in self._ws_handles:
            return self._ws_handles[conn_idx]
        fubon = get_fubon()
        if fubon.status != FubonStatus.OK or fubon.sdk is None:
            logger.error("cannot create ws conn[%d]: fubon SDK not OK", conn_idx)
            return None
        try:
            ws = fubon.sdk.marketdata.websocket_client.stock
            self._ws_handles[conn_idx] = ws
            self._wire_callbacks(conn_idx, ws)
            await asyncio.to_thread(ws.connect)
            logger.info("ws conn[%d] connected", conn_idx)
            self._reconnect_failures = 0
            return ws
        except Exception as e:
            logger.error("ws conn[%d] connect failed: %s", conn_idx, e)
            return None

    def _wire_callbacks(self, conn_idx: int, ws: Any) -> None:
        def on_message(raw: object) -> None:
            self._handle_raw_message(raw)

        def on_disconnect(*args: object) -> None:
            logger.warning("ws conn[%d] disconnected: %s", conn_idx, args)
            if self._loop is not None:
                self._loop.call_soon_threadsafe(
                    asyncio.create_task, self._reconnect(conn_idx)
                )

        def on_error(*args: object) -> None:
            logger.error("ws conn[%d] error: %s", conn_idx, args)

        ws.on("message", on_message)
        ws.on("disconnect", on_disconnect)
        ws.on("error", on_error)

    def _handle_raw_message(self, raw: object) -> None:
        """sync callback (在 fubon SDK thread) → bridge to asyncio。"""
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        if payload.get("event") != "data":
            return
        data = payload.get("data") or {}
        symbol = data.get("symbol")
        price = data.get("price")
        size = data.get("size", 0)
        if not symbol or price is None:
            return
        tick = Tick(price=float(price), size=int(size), time=time.time())

        # 1. ring_buffer (sync, thread-safe)
        get_ring_buffer().append(symbol, tick)

        # 2. signal_engine queue (async, 用 loop bridge)
        if self._on_tick is not None and self._loop is not None:
            self._loop.call_soon_threadsafe(
                asyncio.create_task, self._on_tick(symbol, tick)
            )

    async def _reconnect(self, conn_idx: int) -> None:
        for delay in RECONNECT_DELAYS:
            await asyncio.sleep(delay)
            try:
                # discard old handle, ensure 重新建
                self._ws_handles.pop(conn_idx, None)
                ws = await self._ensure_handle(conn_idx)
                if ws is None:
                    raise RuntimeError("ensure_handle returned None")
                # 重訂閱 conn_subs[conn_idx]
                syms = list(self._conn_subs.get(conn_idx, set()))
                if syms:
                    await asyncio.to_thread(
                        ws.subscribe, {"channel": "trades", "symbols": syms}
                    )
                logger.info("ws conn[%d] reconnected, %d symbols restored", conn_idx, len(syms))
                self._status = WSPoolStatus.OK
                self._reconnect_failures = 0
                return
            except Exception as e:
                logger.warning("ws conn[%d] reconnect attempt failed: %s", conn_idx, e)
                self._reconnect_failures += 1
                if self._reconnect_failures >= CIRCUIT_OPEN_THRESHOLD:
                    self._status = WSPoolStatus.CIRCUIT_OPEN
                    await alerts.notify_critical(
                        f"WS pool circuit breaker open after {CIRCUIT_OPEN_THRESHOLD} reconnect failures",
                        conn_idx=conn_idx,
                    )
                    return
        self._status = WSPoolStatus.DEGRADED

    async def shutdown(self) -> None:
        for idx, ws in list(self._ws_handles.items()):
            try:
                await asyncio.to_thread(ws.disconnect)
            except Exception:
                pass
        self._ws_handles.clear()
        self._conn_subs.clear()
        self._symbol_to_conn.clear()
        self._refcount.clear()


_pool: WSPool | None = None


def get_ws_pool() -> WSPool:
    global _pool
    if _pool is None:
        _pool = WSPool()
    return _pool
```

- [ ] **Step 2: import 驗證**

```powershell
$env:PYTHONPATH = "C:\side-project\trading-king\backend"; & "C:\side-project\trading-king\backend\.venv\Scripts\python.exe" -c "from services.fubon_ws import get_ws_pool, WSPool, WSPoolStatus; p = get_ws_pool(); print('OK', p.status, 'cap', p.total_subscribed(), '/', 600)"
```

Expected: `OK ok cap 0 / 600`

- [ ] **Step 3: Commit**

```bash
git add backend/services/fubon_ws.py
git commit -m "feat(phase3): fubon_ws WS pool — refcount registry + reconnect + circuit breaker"
```

---

### Task 3.2: probe_ws_pool integration smoke

**Files:**
- Create: `backend/scripts/probe_ws_pool.py`

- [ ] **Step 1: 寫 probe（refcount 驗證）**

```python
"""驗證 fubon_ws.WSPool 的 refcount registry — 兩 owner 同 symbol 退一個不互踩。"""
from __future__ import annotations

import asyncio
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

from services.fubon_client import get_fubon
from services.fubon_ws import get_ws_pool


async def main() -> None:
    fubon = get_fubon()
    await fubon.init()
    if fubon.status.value != "ok":
        print(f"FAIL: fubon not OK: {fubon.last_error}")
        sys.exit(1)

    pool = get_ws_pool()
    await pool.start()

    print("[1] subscribe(2330, owner=A) — 預期真打富邦訂閱")
    await pool.subscribe("2330", "A")
    assert pool.total_subscribed() == 1, "expected 1 sub"
    print(f"  ✓ total_subscribed={pool.total_subscribed()}")

    print("[2] subscribe(2330, owner=B) — 同 symbol 不再打富邦，但 refcount=2")
    await pool.subscribe("2330", "B")
    assert pool.total_subscribed() == 1, "should still be 1 (same symbol)"
    assert "A" in pool._refcount["2330"] and "B" in pool._refcount["2330"]
    print(f"  ✓ refcount: {pool._refcount['2330']}")

    print("[3] unsubscribe(2330, owner=A) — refcount 1，仍訂閱中")
    await pool.unsubscribe("2330", "A")
    assert pool.total_subscribed() == 1, "still subscribed (B owns)"
    assert "B" in pool._refcount["2330"] and "A" not in pool._refcount["2330"]
    print(f"  ✓ refcount: {pool._refcount['2330']}")

    print("[4] unsubscribe(2330, owner=B) — refcount 0，真退訂")
    await pool.unsubscribe("2330", "B")
    assert pool.total_subscribed() == 0, "should be 0"
    assert "2330" not in pool._refcount
    print(f"  ✓ total_subscribed={pool.total_subscribed()}, refcount cleared")

    await pool.shutdown()
    print("\nAll WS pool refcount tests passed ✓")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: 跑 probe**

```powershell
& "C:\side-project\trading-king\backend\.venv\Scripts\python.exe" "C:\side-project\trading-king\backend\scripts\probe_ws_pool.py"
```

Expected: 4 個 `✓` + final pass。盤後跑時不會收到 tick 但 subscribe/unsubscribe 流程仍能驗。

- [ ] **Step 3: Commit**

```bash
git add backend/scripts/probe_ws_pool.py
git commit -m "test(phase3): probe_ws_pool — refcount integration smoke"
```

---

### Task 3.3: overnight reconnect cron task

**Files:**
- Create: `backend/services/overnight.py`

- [ ] **Step 1: 寫 overnight task scheduler**

```python
"""8:25 過夜重連 — fubon relogin + ws pool 重訂閱所有 active symbols。

Plan §Phase 3 §5.6。固定 8:25 觸發（盤前 5 分鐘），給 8:30 集合競價開始時 token 是新的。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time as dtime, timedelta

from services import alerts
from services.fubon_client import FubonStatus, get_fubon
from services.fubon_ws import get_ws_pool

logger = logging.getLogger(__name__)

OVERNIGHT_HOUR = 8
OVERNIGHT_MINUTE = 25


async def run_overnight_reconnect() -> bool:
    """執行一次過夜重連流程。Return True if success."""
    pool = get_ws_pool()
    fubon = get_fubon()

    logger.info("overnight reconnect starting…")

    # 1. 重 login + init_realtime（重用 Phase 1 fubon_client 的 retry）
    await fubon.init()
    if fubon.status != FubonStatus.OK:
        logger.error("overnight relogin failed: %s", fubon.last_error)
        await alerts.notify_critical(
            "overnight reconnect: fubon relogin failed",
            error=fubon.last_error or "(no detail)",
        )
        return False

    # 2. 重連所有 ws connection + 重訂閱
    try:
        for conn_idx in list(pool._ws_handles.keys()):
            symbols = list(pool._conn_subs.get(conn_idx, set()))
            pool._ws_handles.pop(conn_idx, None)  # discard old
            ws = await pool._ensure_handle(conn_idx)
            if ws is None:
                raise RuntimeError(f"conn[{conn_idx}] ensure_handle None")
            if symbols:
                await asyncio.to_thread(
                    ws.subscribe, {"channel": "trades", "symbols": symbols}
                )
                logger.info("conn[%d] re-subscribed %d symbols", conn_idx, len(symbols))
        logger.info("overnight reconnect OK")
        return True
    except Exception as e:
        logger.error("overnight ws reconnect failed: %s", e)
        await alerts.notify_critical(
            "overnight reconnect: ws re-subscribe failed",
            error=f"{type(e).__name__}: {e}",
        )
        return False


def _next_run_at(now: datetime) -> datetime:
    target = datetime.combine(now.date(), dtime(OVERNIGHT_HOUR, OVERNIGHT_MINUTE))
    if now >= target:
        target = target + timedelta(days=1)
    return target


async def overnight_loop() -> None:
    """背景 task — 每天 8:25 觸發 reconnect。lifespan 啟動時 create_task。"""
    while True:
        now = datetime.now()
        next_run = _next_run_at(now)
        sleep_sec = (next_run - now).total_seconds()
        logger.info("overnight: sleeping %.0fs until %s", sleep_sec, next_run.isoformat())
        try:
            await asyncio.sleep(sleep_sec)
        except asyncio.CancelledError:
            logger.info("overnight loop cancelled")
            return
        await run_overnight_reconnect()
```

- [ ] **Step 2: import 驗證**

```powershell
$env:PYTHONPATH = "C:\side-project\trading-king\backend"; & "C:\side-project\trading-king\backend\.venv\Scripts\python.exe" -c "from services.overnight import run_overnight_reconnect, overnight_loop, _next_run_at; from datetime import datetime; print('next:', _next_run_at(datetime.now()))"
```

Expected: 印出明天或今天 8:25 的 datetime。

- [ ] **Step 3: Commit**

```bash
git add backend/services/overnight.py
git commit -m "feat(phase3): overnight 8:25 reconnect cron task"
```

---

## Task Group 4 — cdp service + backfill

### Task 4.1: cdp 公式 + inline smoke

**Files:**
- Create: `backend/services/cdp.py`

- [ ] **Step 1: 寫 cdp service**

```python
"""CDP 5 線 — 從昨日 OHLC 算 5 個值，盤中為固定值。

Plan §Phase 3 §4.5。
公式（台股 / 港股慣例）：
  CDP = (H + L + 2C) / 4
  AH (最高值) = CDP + (H − L)
  NH (近高值) = 2 × CDP − L
  NL (近低值) = 2 × CDP − H
  AL (最低值) = CDP − (H − L)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from typing import Any, TypedDict

logger = logging.getLogger(__name__)


class CdpLevels(TypedDict):
    ah: float
    nh: float
    cdp: float
    nl: float
    al: float
    as_of_date: str  # 昨日 ISO date string


def compute_cdp(o: float, h: float, l: float, c: float) -> dict[str, float]:
    """純函式 — 給 OHLC 算 5 線值。"""
    cdp = (h + l + 2 * c) / 4
    ah = cdp + (h - l)
    nh = 2 * cdp - l
    nl = 2 * cdp - h
    al = cdp - (h - l)
    return {"ah": ah, "nh": nh, "cdp": cdp, "nl": nl, "al": al}


class CdpService:
    """In-memory cache + 從 daily_ohlc 抓昨日 OHLC 算 5 線。"""

    def __init__(self) -> None:
        self._cache: dict[str, CdpLevels] = {}
        self._lock = asyncio.Lock()

    async def get(self, symbol: str) -> CdpLevels | None:
        """回 cache 中的 5 值，沒有就 lazy load 一次。"""
        if symbol in self._cache:
            return self._cache[symbol]
        await self.refresh(symbol)
        return self._cache.get(symbol)

    async def refresh(self, symbol: str) -> None:
        """從 daily_ohlc 抓最近一筆 OHLC → 算 → 進 cache。"""
        from services.supabase_client import get_supabase

        sb = get_supabase()
        if sb.client is None:
            logger.warning("cdp.refresh: supabase not ready")
            return

        # 抓最近一筆 daily_ohlc（昨日）
        res = (
            sb.client.table("daily_ohlc")
            .select("date, open, high, low, close")
            .eq("symbol", symbol)
            .order("date", desc=True)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        if not rows:
            logger.info("cdp.refresh: no daily_ohlc for %s yet", symbol)
            return
        row = rows[0]
        try:
            levels = compute_cdp(
                float(row["open"]), float(row["high"]),
                float(row["low"]), float(row["close"]),
            )
            self._cache[symbol] = {
                "ah": levels["ah"], "nh": levels["nh"], "cdp": levels["cdp"],
                "nl": levels["nl"], "al": levels["al"],
                "as_of_date": row["date"],
            }
            logger.debug("cdp cached %s: %s", symbol, self._cache[symbol])
        except (ValueError, TypeError) as e:
            logger.warning("cdp.refresh %s: bad data %s — %s", symbol, row, e)

    def discard(self, symbol: str) -> None:
        self._cache.pop(symbol, None)

    def has(self, symbol: str) -> bool:
        return symbol in self._cache


_service: CdpService | None = None


def get_cdp_service() -> CdpService:
    global _service
    if _service is None:
        _service = CdpService()
    return _service


# ----------------------- inline smoke -----------------------

if __name__ == "__main__":
    import sys

    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    GREEN, RED, YELLOW, RESET = "\033[92m", "\033[91m", "\033[93m", "\033[0m"

    def step(n, t): print(f"\n{YELLOW}[Test {n}] {t}{RESET}")
    def ok(m): print(f"{GREEN}  ✓ {m}{RESET}")
    def fail(m): print(f"{RED}  ✗ {m}{RESET}"); sys.exit(1)

    step(1, "compute_cdp(O=2300, H=2320, L=2280, C=2290) — 對 spec 範例")
    r = compute_cdp(2300, 2320, 2280, 2290)
    # CDP = (2320+2280+2*2290)/4 = (2320+2280+4580)/4 = 9180/4 = 2295
    # AH = 2295 + (2320-2280) = 2295 + 40 = 2335
    # NH = 2*2295 - 2280 = 4590 - 2280 = 2310
    # NL = 2*2295 - 2320 = 4590 - 2320 = 2270
    # AL = 2295 - 40 = 2255
    expected = {"ah": 2335, "nh": 2310, "cdp": 2295, "nl": 2270, "al": 2255}
    for k, v in expected.items():
        if abs(r[k] - v) > 0.001:
            fail(f"{k} 不對: 算到 {r[k]}, 預期 {v}")
    ok(f"5 線都對: {r}")

    step(2, "compute_cdp 對極端值不爆")
    r = compute_cdp(0.01, 0.02, 0.01, 0.015)
    if all(isinstance(v, float) for v in r.values()):
        ok("極小值 OK")
    else: fail("type 不對")

    step(3, "ordering — AH > NH > CDP > NL > AL（H>L 時）")
    r = compute_cdp(580, 600, 560, 590)
    if r["ah"] > r["nh"] > r["cdp"] > r["nl"] > r["al"]:
        ok(f"順序正確: {r}")
    else: fail(f"順序錯: {r}")

    print(f"\n{GREEN}All cdp smoke tests passed ✓{RESET}")
```

- [ ] **Step 2: 跑 smoke**

```powershell
& "C:\side-project\trading-king\backend\.venv\Scripts\python.exe" "C:\side-project\trading-king\backend\services\cdp.py"
```

Expected: 3 個 `✓`。

- [ ] **Step 3: Commit**

```bash
git add backend/services/cdp.py
git commit -m "feat(phase3): cdp service — 5 線公式 + supabase load + smoke"
```

---

### Task 4.2: backfill flow（watchlist 加入時）

**Files:**
- Modify: `backend/services/cdp.py` (加 backfill_from_fubon)

- [ ] **Step 1: 加 backfill 函式**

在 `cdp.py` 的 `CdpService` class 內加 method（接 `Step 1` 的 class 之後）：

```python
    async def backfill_from_fubon(self, symbol: str) -> bool:
        """打富邦 historical.candles 拉昨日 OHLC → INSERT daily_ohlc → refresh cache。

        Return True if successful, False if no data / fubon error。
        """
        from services.fubon_client import FubonStatus, get_fubon
        from services.supabase_client import get_supabase

        fubon = get_fubon()
        sb = get_supabase()
        if fubon.status != FubonStatus.OK or fubon.sdk is None:
            logger.warning("cdp.backfill: fubon not OK")
            return False
        if sb.client is None:
            logger.warning("cdp.backfill: supabase not OK")
            return False

        today = date.today()
        last_week = today - timedelta(days=10)  # 抓 10 天範圍，確保至少抓到上個交易日

        try:
            r = await asyncio.to_thread(
                fubon.sdk.marketdata.rest_client.stock.historical.candles,
                symbol=symbol,
                from_=last_week.isoformat(),
                to=today.isoformat(),
            )
        except Exception as e:
            logger.warning("cdp.backfill %s: fubon error %s", symbol, e)
            return False

        rows = (r or {}).get("data") or []
        if not rows:
            logger.info("cdp.backfill %s: no historical data", symbol)
            return False

        # 富邦 historical.candles 預設 desc by date，最新在 index 0；
        # 過濾掉「今日」（不能用今天的 H/L/C 算今天的 CDP）
        upserts = []
        for row in rows:
            d = row.get("date")
            if not d or d == today.isoformat():
                continue
            upserts.append({
                "symbol": symbol, "date": d,
                "open": row.get("open"), "high": row.get("high"),
                "low": row.get("low"), "close": row.get("close"),
            })

        if not upserts:
            logger.info("cdp.backfill %s: only today data (no past)", symbol)
            return False

        # upsert 進 daily_ohlc
        try:
            await asyncio.to_thread(
                lambda: sb.client.table("daily_ohlc")
                .upsert(upserts, on_conflict="symbol,date")
                .execute()
            )
        except Exception as e:
            logger.error("cdp.backfill %s: supabase upsert failed: %s", symbol, e)
            return False

        await self.refresh(symbol)
        logger.info("cdp.backfill %s: %d days OHLC stored", symbol, len(upserts))
        return True
```

- [ ] **Step 2: 寫 probe_cdp.py**

```python
# backend/scripts/probe_cdp.py
"""驗證 cdp.backfill_from_fubon 對 2330 跑一次能拿資料 + 算 5 線。"""
from __future__ import annotations

import asyncio
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

from services.cdp import get_cdp_service
from services.fubon_client import get_fubon
from services.supabase_client import get_supabase


async def main() -> None:
    fubon = get_fubon()
    await fubon.init()
    sb = get_supabase()
    sb.init()
    if fubon.status.value != "ok" or sb.status.value != "ok":
        print(f"FAIL fubon={fubon.status.value} sb={sb.status.value}")
        sys.exit(1)

    cdp = get_cdp_service()
    print("[1] backfill 2330 from fubon")
    ok = await cdp.backfill_from_fubon("2330")
    if not ok:
        print("  ✗ backfill failed")
        sys.exit(1)
    print("  ✓ backfill OK")

    print("[2] cdp.get('2330') — 5 值")
    levels = await cdp.get("2330")
    if levels is None:
        print("  ✗ get returned None")
        sys.exit(1)
    print(f"  ah={levels['ah']:.2f}  nh={levels['nh']:.2f}  cdp={levels['cdp']:.2f}  nl={levels['nl']:.2f}  al={levels['al']:.2f}  (as_of {levels['as_of_date']})")
    assert levels["ah"] > levels["nh"] > levels["cdp"] > levels["nl"] > levels["al"], "順序不對"
    print("  ✓ ordering 正確")

    print("\nAll cdp backfill tests passed ✓")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 3: 跑 probe（要求 supabase MCP 已 auth + fubon 能 login）**

```powershell
& "C:\side-project\trading-king\backend\.venv\Scripts\python.exe" "C:\side-project\trading-king\backend\scripts\probe_cdp.py"
```

Expected: 兩個 `✓` + 印出實際 CDP 5 值（譬如 ah=2335 cdp=2295 al=2255 那種）。

- [ ] **Step 4: 驗 daily_ohlc 表有 2330 row**

```
mcp__supabase__execute_sql(query="SELECT count(*), max(date) FROM daily_ohlc WHERE symbol='2330'")
```

Expected: count > 0, max(date) 是昨日（譬如 2026-05-11）。

- [ ] **Step 5: Commit**

```bash
git add backend/services/cdp.py backend/scripts/probe_cdp.py
git commit -m "feat(phase3): cdp backfill from fubon historical.candles + probe"
```

---
## Task Group 5 — signal_engine

### Task 5.1: ws_broadcaster + signal_engine skeleton

**Files:**
- Create: `backend/ws_broadcaster.py`
- Create: `backend/services/signal_engine.py` (skeleton, 之後 task 補 evaluator)

- [ ] **Step 1: 建 ws_broadcaster.py（前端 WS 連線管理）**

```python
"""管理 /ws/realtime 的所有前端連線 + broadcast helper。"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class Broadcaster:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def add(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.add(ws)
        logger.info("ws client connected (total=%d)", len(self._clients))

    async def remove(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)
        logger.info("ws client disconnected (total=%d)", len(self._clients))

    async def broadcast(self, payload: dict[str, Any]) -> None:
        async with self._lock:
            clients = list(self._clients)
        dead: list[WebSocket] = []
        for ws in clients:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._clients.discard(ws)


_b: Broadcaster | None = None


def get_broadcaster() -> Broadcaster:
    global _b
    if _b is None:
        _b = Broadcaster()
    return _b
```

- [ ] **Step 2: 建 signal_engine.py 骨架（含 Health metrics）**

```python
"""訊號 evaluator — 消費 tick → 跑 WindowCondition + Filter.conditions → 達成 fan-out。

Plan §Phase 3 §4.3 / §5.1。
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

from models.condition import (
    ActiveSignalOut, Condition, Filter, WindowCondition,
)
from services import alerts
from services.cdp import get_cdp_service
from services.ring_buffer import Tick, get_ring_buffer
from services.supabase_client import get_supabase
from ws_broadcaster import get_broadcaster

logger = logging.getLogger(__name__)

QUEUE_MAXSIZE = 5000
BACKPRESSURE_LAG_MS = 5000
BACKPRESSURE_DURATION_S = 30


class SignalEngine:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[tuple[str, Tick]] = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        self._consumer: asyncio.Task | None = None
        self._monitor: asyncio.Task | None = None
        self._active: list[ActiveSignalOut] = []
        # cooldown: (active_signal_id, symbol) → last_triggered_at (epoch s)
        self._cooldown: dict[tuple[str, str], float] = {}
        # in-memory cache: symbol → field → value (indicator + cdp 共用)
        self._field_cache: dict[str, dict[str, float]] = {}
        # metrics
        self._dropped_today = 0
        self._last_lag_ms = 0.0
        self._lag_violation_started: float | None = None
        self._degraded = False

    # ---------- 公開 API ----------

    async def start(self) -> None:
        from services.fubon_ws import get_ws_pool
        get_ws_pool().set_tick_callback(self.enqueue)
        self._consumer = asyncio.create_task(self._consume_loop())
        self._monitor = asyncio.create_task(self._monitor_loop())
        await self.refresh_active_signals()
        logger.info("SignalEngine started")

    async def shutdown(self) -> None:
        for t in (self._consumer, self._monitor):
            if t and not t.done():
                t.cancel()

    async def enqueue(self, symbol: str, tick: Tick) -> None:
        try:
            self._queue.put_nowait((symbol, tick))
        except asyncio.QueueFull:
            self._dropped_today += 1

    async def refresh_active_signals(self) -> None:
        """從 supabase 讀 enabled active_signals，刷新 in-memory list 跟 field cache。"""
        sb = get_supabase()
        if sb.client is None:
            self._active = []
            return
        res = await asyncio.to_thread(
            lambda: sb.client.table("active_signals")
            .select("id, name, filter_json, scope, cooldown_seconds, ignore_auctions, enabled, created_at")
            .eq("enabled", True)
            .execute()
        )
        rows = res.data or []
        self._active = [self._row_to_active(r) for r in rows]
        await self._refill_field_cache()
        logger.info("active_signals reloaded: %d enabled", len(self._active))

    def health(self) -> dict[str, Any]:
        return {
            "queue_depth": self._queue.qsize(),
            "lag_ms": int(self._last_lag_ms),
            "dropped_today": self._dropped_today,
            "degraded": self._degraded,
            "active_count": len(self._active),
        }

    # ---------- internal ----------

    def _row_to_active(self, r: dict) -> ActiveSignalOut:
        return ActiveSignalOut(
            id=r["id"], name=r["name"],
            filter_json=r["filter_json"], scope=r["scope"],
            cooldown_seconds=r.get("cooldown_seconds", 1800),
            ignore_auctions=r.get("ignore_auctions", True),
            enabled=r.get("enabled", True),
            created_at=str(r.get("created_at", "")),
        )

    async def _refill_field_cache(self) -> None:
        """為每個 active 涉及的 (symbol, field) 載入最新值進 cache。
        - indicator 欄位 → 從 indicator_cache 讀最後成功 date 的 row
        - cdp_* 欄位 → 從 cdp service 讀
        """
        from services.indicator_cache_job import get_latest_done_run
        sb = get_supabase()
        if sb.client is None:
            return

        # 蒐集所有 active 涉及的 symbol 跟 fields
        symbols_needed: set[str] = set()
        for a in self._active:
            scope = a.scope
            if isinstance(scope, dict):
                if scope.get("type") == "symbols":
                    symbols_needed.update(scope.get("symbols", []))
                elif scope.get("type") == "watchlist":
                    # watchlist 全部
                    res = await asyncio.to_thread(
                        lambda: sb.client.table("watchlist").select("symbol").execute()
                    )
                    for row in (res.data or []):
                        symbols_needed.add(row["symbol"])

        # indicator_cache 最後 done date
        latest = await asyncio.to_thread(get_latest_done_run, sb.client)
        if latest:
            target_date = latest["run_date"]
            ic_res = await asyncio.to_thread(
                lambda: sb.client.table("indicator_cache")
                .select("symbol, close, change_pct, volume, amount, rsi_14, macd, macd_signal, kdj_k, kdj_d, kdj_j, sma_5, sma_20, sma_60, bbands_upper, bbands_middle, bbands_lower")
                .eq("date", target_date)
                .in_("symbol", list(symbols_needed))
                .execute()
            )
            for row in (ic_res.data or []):
                sym = row.pop("symbol")
                self._field_cache[sym] = {k: v for k, v in row.items() if v is not None}

        # cdp 5 值
        cdp = get_cdp_service()
        for sym in symbols_needed:
            levels = await cdp.get(sym)
            if levels:
                d = self._field_cache.setdefault(sym, {})
                d["cdp_ah"] = levels["ah"]
                d["cdp_nh"] = levels["nh"]
                d["cdp"] = levels["cdp"]
                d["cdp_nl"] = levels["nl"]
                d["cdp_al"] = levels["al"]

    async def _consume_loop(self) -> None:
        """主消費迴圈 — 從 queue 拉 tick → evaluate → fan-out。"""
        while True:
            try:
                symbol, tick = await self._queue.get()
            except asyncio.CancelledError:
                return
            self._last_lag_ms = (time.time() - tick.time) * 1000.0
            await self._evaluate(symbol, tick)

    async def _evaluate(self, symbol: str, tick: Tick) -> None:
        """對每個涉及這 symbol 的 active_signal 跑條件。"""
        for active in self._active:
            if not self._scope_includes(active, symbol):
                continue
            if not self._eval_conditions(active, symbol, tick):
                continue
            # cooldown 檢查
            key = (active.id, symbol)
            now = time.time()
            last_ts = self._cooldown.get(key, 0)
            if now - last_ts < active.cooldown_seconds:
                continue
            self._cooldown[key] = now
            await self._fanout(active, symbol, tick)

    def _scope_includes(self, active: ActiveSignalOut, symbol: str) -> bool:
        s = active.scope
        if isinstance(s, dict):
            t = s.get("type")
            if t == "watchlist":
                return symbol in self._field_cache  # watchlist refill 過就在
            if t == "symbols":
                return symbol in s.get("symbols", [])
        return False

    def _eval_conditions(self, active: ActiveSignalOut, symbol: str, tick: Tick) -> bool:
        # WindowCondition + Filter.conditions
        f = active.filter_json
        results: list[bool] = []
        for wc in (f.get("window_conditions") if isinstance(f, dict) else getattr(f, "window_conditions", [])):
            results.append(self._eval_window(symbol, tick, wc))
        for c in (f.get("conditions") if isinstance(f, dict) else getattr(f, "conditions", [])):
            results.append(self._eval_filter_cond(symbol, tick, c))
        if not results:
            return False
        logic = (f.get("logic") if isinstance(f, dict) else getattr(f, "logic", "AND"))
        return all(results) if logic == "AND" else any(results)

    def _eval_window(self, symbol: str, tick: Tick, wc) -> bool:
        wc_type = wc.get("type") if isinstance(wc, dict) else wc.type
        wc_secs = wc.get("window_seconds") if isinstance(wc, dict) else wc.window_seconds
        op = wc.get("operator") if isinstance(wc, dict) else wc.operator
        val = wc.get("value") if isinstance(wc, dict) else wc.value

        ticks = get_ring_buffer().window(symbol, seconds=wc_secs)
        if not ticks:
            return False

        if wc_type == "price_change_pct":
            start = ticks[0].price
            if start == 0:
                return False
            actual = (tick.price - start) / start * 100
            return _cmp(actual, op, val)
        if wc_type == "volume_burst":
            current_vol = sum(t.size for t in ticks)
            return _cmp(current_vol, op, val)  # 簡化：跟絕對 value 比，未來可加歷史平均
        if wc_type == "trade_count":
            return _cmp(len(ticks), op, val)
        return False

    def _eval_filter_cond(self, symbol: str, tick: Tick, c) -> bool:
        field = c.get("field") if isinstance(c, dict) else c.field
        op = c.get("operator") if isinstance(c, dict) else c.operator
        value = c.get("value") if isinstance(c, dict) else c.value

        # field 'close' 用即時 tick.price，其他從 cache
        if field == "close":
            lhs = tick.price
        else:
            lhs = self._field_cache.get(symbol, {}).get(field)
        if lhs is None:
            return False

        if isinstance(value, str):
            # 跨欄位（含 cdp_*）
            if value == "close":
                rhs = tick.price
            else:
                rhs = self._field_cache.get(symbol, {}).get(value)
            if rhs is None:
                return False
        else:
            rhs = float(value)

        return _cmp(lhs, op, rhs)

    async def _fanout(self, active: ActiveSignalOut, symbol: str, tick: Tick) -> None:
        from services.supabase_writer import get_supabase_writer
        payload = {
            "event": "signal",
            "data": {
                "active_signal_id": active.id,
                "active_signal_name": active.name,
                "symbol": symbol,
                "triggered_at": datetime.now(timezone.utc).isoformat(),
                "trigger_price": tick.price,
                "trigger_volume": tick.size,
            },
        }
        # 1. 前端 WS broadcast
        await get_broadcaster().broadcast(payload)
        # 2. supabase writer
        get_supabase_writer().append({
            "active_signal_id": active.id,
            "symbol": symbol,
            "trigger_price": tick.price,
            "trigger_volume": tick.size,
            "context_json": {"latest_tick_time": tick.time},
        })

    async def _monitor_loop(self) -> None:
        """監控 lag — 超過 5s 連續 30s → 自動 disable + alerts。"""
        while True:
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                return
            if self._last_lag_ms > BACKPRESSURE_LAG_MS:
                if self._lag_violation_started is None:
                    self._lag_violation_started = time.time()
                elif time.time() - self._lag_violation_started > BACKPRESSURE_DURATION_S:
                    if not self._degraded:
                        await self._auto_disable_all()
            else:
                self._lag_violation_started = None

    async def _auto_disable_all(self) -> None:
        sb = get_supabase()
        if sb.client is None:
            return
        try:
            await asyncio.to_thread(
                lambda: sb.client.table("active_signals").update({"enabled": False}).eq("enabled", True).execute()
            )
        except Exception as e:
            logger.error("auto disable failed: %s", e)
        self._active = []
        self._degraded = True
        await alerts.notify_critical(
            "evaluator overload — all active_signals auto-disabled",
            lag_ms=str(self._last_lag_ms),
        )


def _cmp(lhs: float, op: str, rhs: float) -> bool:
    if op == "gt": return lhs > rhs
    if op == "gte": return lhs >= rhs
    if op == "lt": return lhs < rhs
    if op == "lte": return lhs <= rhs
    if op == "eq": return lhs == rhs
    return False


_engine: SignalEngine | None = None


def get_signal_engine() -> SignalEngine:
    global _engine
    if _engine is None:
        _engine = SignalEngine()
    return _engine
```

- [ ] **Step 3: import 驗證**

```powershell
$env:PYTHONPATH = "C:\side-project\trading-king\backend"; & "C:\side-project\trading-king\backend\.venv\Scripts\python.exe" -c "from services.signal_engine import get_signal_engine, _cmp; e = get_signal_engine(); print('engine OK', e.health()); print('cmp 5 > 3:', _cmp(5, 'gt', 3))"
```

Expected: `engine OK {...queue_depth=0,...degraded=False,...}` + `cmp 5 > 3: True`。

- [ ] **Step 4: Commit**

```bash
git add backend/ws_broadcaster.py backend/services/signal_engine.py
git commit -m "feat(phase3): signal_engine + ws_broadcaster — evaluator + cooldown + backpressure monitor"
```

---

### Task 5.2: probe_signal_engine — mock tick 驗 evaluator

**Files:**
- Create: `backend/scripts/probe_signal_engine.py`

- [ ] **Step 1: 寫 probe**

```python
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
```

- [ ] **Step 2: 跑 probe**

```powershell
& "C:\side-project\trading-king\backend\.venv\Scripts\python.exe" "C:\side-project\trading-king\backend\scripts\probe_signal_engine.py"
```

Expected: 3 個 `✓` + final pass。

- [ ] **Step 3: Commit**

```bash
git add backend/scripts/probe_signal_engine.py
git commit -m "test(phase3): probe_signal_engine — WindowCondition + cooldown smoke"
```

---

## Task Group 6 — supabase_writer

### Task 6.1: writer 實作 + smoke

**Files:**
- Create: `backend/services/supabase_writer.py`

- [ ] **Step 1: 建 writer**

```python
"""Async batch flush writer — evaluator 命中時 append，500ms 或 ≥100 列觸發 INSERT signals_log。

Plan §Phase 3 §4.4。
失敗 retry 1 次仍失敗 → alerts + buffer 保留待下次 flush 重送。
buffer > 1000 列 → FIFO drop + metric。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from services import alerts
from services.supabase_client import get_supabase

logger = logging.getLogger(__name__)

FLUSH_INTERVAL_S = 0.5
FLUSH_THRESHOLD = 100
BUFFER_HARD_CAP = 1000


class SupabaseWriter:
    def __init__(self) -> None:
        self._buffer: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._dropped_writes = 0
        self._failed_flushes = 0

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())
            logger.info("SupabaseWriter started")

    async def shutdown(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        # 最後 flush
        await self._flush()

    def append(self, row: dict[str, Any]) -> None:
        """同步 append（從 evaluator 呼叫）。超過 hard cap → drop 最舊。"""
        if len(self._buffer) >= BUFFER_HARD_CAP:
            self._buffer.pop(0)
            self._dropped_writes += 1
        self._buffer.append(row)

    def metrics(self) -> dict[str, int]:
        return {
            "buffer_size": len(self._buffer),
            "dropped_writes": self._dropped_writes,
            "failed_flushes": self._failed_flushes,
        }

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(FLUSH_INTERVAL_S)
            except asyncio.CancelledError:
                return
            if len(self._buffer) >= FLUSH_THRESHOLD or self._buffer:
                await self._flush()

    async def _flush(self) -> None:
        if not self._buffer:
            return
        sb = get_supabase()
        if sb.client is None:
            return
        async with self._lock:
            batch = self._buffer[:]
        try:
            await asyncio.to_thread(
                lambda: sb.client.table("signals_log").insert(batch).execute()
            )
            async with self._lock:
                # 只清 batch size 那麼多，新進來的 row 留著
                self._buffer = self._buffer[len(batch):]
            logger.debug("flushed %d signal rows", len(batch))
        except Exception as e:
            self._failed_flushes += 1
            logger.warning("flush failed (will retry next cycle): %s", e)
            if self._failed_flushes % 10 == 1:
                await alerts.notify_critical(
                    "supabase_writer flush failing",
                    error=f"{type(e).__name__}: {e}",
                    failed_count=str(self._failed_flushes),
                )


_writer: SupabaseWriter | None = None


def get_supabase_writer() -> SupabaseWriter:
    global _writer
    if _writer is None:
        _writer = SupabaseWriter()
    return _writer
```

- [ ] **Step 2: 寫 probe_writer.py**

```python
"""驗 supabase_writer batch flush — append 5 個 row → 等 1 秒 → 驗 signals_log 有 5 筆。"""
from __future__ import annotations

import asyncio
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
from services.supabase_writer import get_supabase_writer


async def main() -> None:
    sb = get_supabase()
    sb.init()
    if sb.status.value != "ok":
        print(f"FAIL supabase: {sb.last_error}")
        sys.exit(1)

    # 先看當前 signals_log 有幾筆
    res = await asyncio.to_thread(
        lambda: sb.client.table("signals_log").select("id", count="exact").execute()
    )
    before = res.count or 0
    print(f"[before] signals_log count = {before}")

    writer = get_supabase_writer()
    await writer.start()

    print("[append] 5 個 mock signal rows")
    for i in range(5):
        writer.append({
            "active_signal_id": None,
            "symbol": "2330",
            "trigger_price": 580 + i * 0.1,
            "trigger_volume": 100,
            "context_json": {"probe": True, "i": i},
        })

    print("[wait] 1.5s 給 writer flush")
    await asyncio.sleep(1.5)

    res = await asyncio.to_thread(
        lambda: sb.client.table("signals_log").select("id", count="exact").execute()
    )
    after = res.count or 0
    print(f"[after] signals_log count = {after}")

    diff = after - before
    if diff == 5:
        print(f"  ✓ 5 rows inserted (diff={diff})")
    else:
        print(f"  ✗ expected diff=5, got {diff}")
        sys.exit(1)

    await writer.shutdown()
    print("\nAll supabase_writer tests passed ✓")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 3: 跑 probe**

```powershell
& "C:\side-project\trading-king\backend\.venv\Scripts\python.exe" "C:\side-project\trading-king\backend\scripts\probe_writer.py"
```

Expected: `before` 跟 `after` 差 5 + final pass。

- [ ] **Step 4: 清理 probe 留下的 mock signals_log row（optional）**

```
mcp__supabase__execute_sql(query="DELETE FROM signals_log WHERE context_json->>'probe' = 'true'")
```

- [ ] **Step 5: Commit**

```bash
git add backend/services/supabase_writer.py backend/scripts/probe_writer.py
git commit -m "feat(phase3): supabase_writer — 500ms batch flush + retry/alert + smoke"
```

---

## Task Group 7 — backend routes + main.py

### Task 7.1: routes/watchlist.py

**Files:**
- Create: `backend/routes/watchlist.py`

- [ ] **Step 1: 建檔**

```python
"""GET/POST/DELETE /api/watchlist — 自選清單 CRUD。

POST 順手:
  - ws_pool.subscribe(owner='watchlist')
  - 背景 task: cdp.backfill_from_fubon
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.cdp import get_cdp_service
from services.fubon_ws import get_ws_pool
from services.supabase_client import SupabaseStatus, get_supabase

logger = logging.getLogger(__name__)
router = APIRouter()


class WatchlistAdd(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    note: str | None = None


def _ensure_supabase():
    sb = get_supabase()
    if sb.status != SupabaseStatus.OK or sb.client is None:
        raise HTTPException(503, detail={"error": "supabase_unavailable", "last_error": sb.last_error})
    return sb


@router.get("/api/watchlist")
async def list_watchlist() -> dict:
    sb = _ensure_supabase()
    res = await asyncio.to_thread(
        lambda: sb.client.table("watchlist")
        .select("symbol, added_at, note, symbols(name, market, is_etf)")
        .order("added_at", desc=True)
        .execute()
    )
    rows = res.data or []
    out = []
    for r in rows:
        meta = r.get("symbols") or {}
        out.append({
            "symbol": r["symbol"],
            "added_at": r.get("added_at"),
            "note": r.get("note"),
            "name": meta.get("name"),
            "market": meta.get("market"),
            "is_etf": meta.get("is_etf"),
        })
    return {"watchlist": out, "count": len(out)}


@router.post("/api/watchlist", status_code=201)
async def add_watchlist(payload: WatchlistAdd) -> dict:
    sb = _ensure_supabase()
    # symbol 必須存在 symbols 表（FK 會擋但前端體驗差，主動驗）
    sym_res = await asyncio.to_thread(
        lambda: sb.client.table("symbols").select("symbol").eq("symbol", payload.symbol).limit(1).execute()
    )
    if not (sym_res.data or []):
        raise HTTPException(404, detail={"error": "symbol_not_found", "symbol": payload.symbol})

    try:
        await asyncio.to_thread(
            lambda: sb.client.table("watchlist").insert({
                "symbol": payload.symbol, "note": payload.note,
            }).execute()
        )
    except Exception as e:
        # 可能 unique violation（已存在）
        raise HTTPException(409, detail={"error": "already_in_watchlist", "detail": str(e)})

    # WS subscribe (sync, fast)
    try:
        await get_ws_pool().subscribe(payload.symbol, owner_id="watchlist")
    except RuntimeError as e:
        logger.warning("watchlist add: ws subscribe failed: %s", e)
        # 不 rollback watchlist，user 可看到加進來但無即時資料

    # CDP backfill 背景跑（不 block response）
    asyncio.create_task(get_cdp_service().backfill_from_fubon(payload.symbol))

    return {"symbol": payload.symbol, "status": "added"}


@router.delete("/api/watchlist/{symbol}", status_code=204)
async def remove_watchlist(symbol: str) -> None:
    sb = _ensure_supabase()
    await asyncio.to_thread(
        lambda: sb.client.table("watchlist").delete().eq("symbol", symbol).execute()
    )
    # WS unsubscribe（其他 owner 可能還在）
    try:
        await get_ws_pool().unsubscribe(symbol, owner_id="watchlist")
    except Exception as e:
        logger.warning("watchlist remove: ws unsubscribe failed: %s", e)
    # cdp cache 也清
    get_cdp_service().discard(symbol)
    return None
```

- [ ] **Step 2: 驗證 import**

```powershell
$env:PYTHONPATH = "C:\side-project\trading-king\backend"; & "C:\side-project\trading-king\backend\.venv\Scripts\python.exe" -c "from routes.watchlist import router; print('routes:', [r.path for r in router.routes])"
```

Expected: `routes: ['/api/watchlist', '/api/watchlist', '/api/watchlist/{symbol}']`

- [ ] **Step 3: Commit**

```bash
git add backend/routes/watchlist.py
git commit -m "feat(phase3): routes/watchlist — GET/POST/DELETE + WS subscribe + CDP backfill"
```

---

### Task 7.2: routes/active_signals.py

**Files:**
- Create: `backend/routes/active_signals.py`

- [ ] **Step 1: 建檔**

```python
"""GET/POST/PUT/DELETE /api/active_signals — 即時訊號規則 CRUD。

POST/PUT 後呼叫 signal_engine.refresh_active_signals 重新載入規則 + 對 scope 內的 symbols
做 ws_pool.subscribe(owner=active_signal_id)。
DELETE 反過來 unsubscribe。
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException

from models.condition import ActiveSignalCreate
from services.fubon_ws import get_ws_pool
from services.signal_engine import get_signal_engine
from services.supabase_client import SupabaseStatus, get_supabase

logger = logging.getLogger(__name__)
router = APIRouter()


def _ensure_supabase():
    sb = get_supabase()
    if sb.status != SupabaseStatus.OK or sb.client is None:
        raise HTTPException(503, detail={"error": "supabase_unavailable", "last_error": sb.last_error})
    return sb


async def _scope_symbols(scope: dict) -> list[str]:
    """解析 scope dict → symbols list."""
    sb = get_supabase()
    if scope.get("type") == "symbols":
        return list(scope.get("symbols", []))
    if scope.get("type") == "watchlist":
        res = await asyncio.to_thread(
            lambda: sb.client.table("watchlist").select("symbol").execute()
        )
        return [r["symbol"] for r in (res.data or [])]
    return []


@router.get("/api/active_signals")
async def list_active() -> dict:
    sb = _ensure_supabase()
    res = await asyncio.to_thread(
        lambda: sb.client.table("active_signals")
        .select("id, name, filter_json, scope, cooldown_seconds, ignore_auctions, enabled, created_at")
        .order("created_at", desc=True).execute()
    )
    return {"active_signals": res.data or []}


@router.post("/api/active_signals", status_code=201)
async def create_active(payload: ActiveSignalCreate) -> dict:
    sb = _ensure_supabase()
    res = await asyncio.to_thread(
        lambda: sb.client.table("active_signals").insert({
            "name": payload.name,
            "filter_json": payload.filter_json.model_dump(),
            "scope": payload.scope.model_dump(),
            "cooldown_seconds": payload.cooldown_seconds,
            "ignore_auctions": payload.ignore_auctions,
            "enabled": payload.enabled,
        }).execute()
    )
    if not res.data:
        raise HTTPException(500, detail={"error": "insert_failed"})
    new_row = res.data[0]
    # subscribe scope 內 symbols
    if payload.enabled:
        symbols = await _scope_symbols(payload.scope.model_dump())
        for sym in symbols:
            try:
                await get_ws_pool().subscribe(sym, owner_id=new_row["id"])
            except RuntimeError as e:
                logger.warning("active_signal create: ws sub %s failed: %s", sym, e)
    await get_signal_engine().refresh_active_signals()
    return new_row


@router.put("/api/active_signals/{sid}")
async def update_active(sid: str, payload: ActiveSignalCreate) -> dict:
    sb = _ensure_supabase()
    # 拿舊的 scope 算 diff（簡化：先全 unsub 再全 sub）
    old = await asyncio.to_thread(
        lambda: sb.client.table("active_signals").select("scope, enabled").eq("id", sid).single().execute()
    )
    if not old.data:
        raise HTTPException(404, detail={"error": "not_found"})

    old_syms = await _scope_symbols(old.data.get("scope", {})) if old.data.get("enabled") else []
    for sym in old_syms:
        await get_ws_pool().unsubscribe(sym, owner_id=sid)

    res = await asyncio.to_thread(
        lambda: sb.client.table("active_signals").update({
            "name": payload.name,
            "filter_json": payload.filter_json.model_dump(),
            "scope": payload.scope.model_dump(),
            "cooldown_seconds": payload.cooldown_seconds,
            "ignore_auctions": payload.ignore_auctions,
            "enabled": payload.enabled,
        }).eq("id", sid).execute()
    )

    if payload.enabled:
        new_syms = await _scope_symbols(payload.scope.model_dump())
        for sym in new_syms:
            try:
                await get_ws_pool().subscribe(sym, owner_id=sid)
            except RuntimeError as e:
                logger.warning("update: ws sub %s failed: %s", sym, e)
    await get_signal_engine().refresh_active_signals()
    return res.data[0] if res.data else {}


@router.delete("/api/active_signals/{sid}", status_code=204)
async def delete_active(sid: str) -> None:
    sb = _ensure_supabase()
    old = await asyncio.to_thread(
        lambda: sb.client.table("active_signals").select("scope, enabled").eq("id", sid).single().execute()
    )
    if old.data and old.data.get("enabled"):
        for sym in await _scope_symbols(old.data.get("scope", {})):
            await get_ws_pool().unsubscribe(sym, owner_id=sid)

    await asyncio.to_thread(
        lambda: sb.client.table("active_signals").delete().eq("id", sid).execute()
    )
    await get_signal_engine().refresh_active_signals()
    return None
```

- [ ] **Step 2: 驗證**

```powershell
$env:PYTHONPATH = "C:\side-project\trading-king\backend"; & "C:\side-project\trading-king\backend\.venv\Scripts\python.exe" -c "from routes.active_signals import router; print([r.path for r in router.routes])"
```

- [ ] **Step 3: Commit**

```bash
git add backend/routes/active_signals.py
git commit -m "feat(phase3): routes/active_signals CRUD + WS sub diff"
```

---

### Task 7.3: routes/signals_history.py

**Files:**
- Create: `backend/routes/signals_history.py`

- [ ] **Step 1: 建檔**

```python
"""GET /api/signals/history?symbol=&since=&active_signal_id=&limit= — 訊號歷史查詢。"""
from __future__ import annotations

import asyncio
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from services.supabase_client import SupabaseStatus, get_supabase

router = APIRouter()


@router.get("/api/signals/history")
async def signals_history(
    symbol: str | None = Query(None),
    active_signal_id: str | None = Query(None),
    since: str | None = Query(None, description="ISO datetime"),
    limit: int = Query(100, ge=1, le=500),
) -> dict:
    sb = get_supabase()
    if sb.status != SupabaseStatus.OK or sb.client is None:
        raise HTTPException(503, detail={"error": "supabase_unavailable"})

    def _q():
        q = sb.client.table("signals_log").select(
            "id, active_signal_id, symbol, triggered_at, trigger_price, trigger_volume, context_json"
        ).order("triggered_at", desc=True).limit(limit)
        if symbol:
            q = q.eq("symbol", symbol)
        if active_signal_id:
            q = q.eq("active_signal_id", active_signal_id)
        if since:
            q = q.gte("triggered_at", since)
        return q.execute()

    res = await asyncio.to_thread(_q)
    return {"signals": res.data or [], "count": len(res.data or [])}
```

- [ ] **Step 2: Commit**

```bash
git add backend/routes/signals_history.py
git commit -m "feat(phase3): routes/signals_history — GET with filters"
```

---

### Task 7.4: routes/candles.py

**Files:**
- Create: `backend/routes/candles.py`

- [ ] **Step 1: 建檔**

```python
"""GET /api/candles/{symbol}/intraday — proxy 富邦 intraday.candles。

回 266 筆 1m K + average (= VWAP)，給前端 IntradayChart 用。
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from services.fubon_client import FubonStatus, get_fubon

router = APIRouter()


@router.get("/api/candles/{symbol}/intraday")
async def intraday_candles(symbol: str) -> dict:
    fubon = get_fubon()
    if fubon.status != FubonStatus.OK or fubon.sdk is None:
        raise HTTPException(503, detail={"error": "fubon_unavailable", "last_error": fubon.last_error})

    try:
        r = await asyncio.to_thread(
            fubon.sdk.marketdata.rest_client.stock.intraday.candles,
            symbol=symbol,
        )
    except Exception as e:
        raise HTTPException(502, detail={"error": "fubon_call_failed", "detail": str(e)})

    return r or {}
```

- [ ] **Step 2: Commit**

```bash
git add backend/routes/candles.py
git commit -m "feat(phase3): routes/candles — proxy fubon intraday.candles for chart"
```

---

### Task 7.5: routes/cdp.py

**Files:**
- Create: `backend/routes/cdp.py`

- [ ] **Step 1: 建檔**

```python
"""GET /api/cdp/{symbol} — 回 CDP 5 線值。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from services.cdp import get_cdp_service

router = APIRouter()


@router.get("/api/cdp/{symbol}")
async def get_cdp(symbol: str) -> dict:
    levels = await get_cdp_service().get(symbol)
    if levels is None:
        # lazy backfill 一次
        ok = await get_cdp_service().backfill_from_fubon(symbol)
        if not ok:
            raise HTTPException(503, detail={"error": "cdp_data_unavailable", "symbol": symbol})
        levels = await get_cdp_service().get(symbol)
        if levels is None:
            raise HTTPException(503, detail={"error": "cdp_data_unavailable_after_backfill"})
    return levels  # type: ignore[return-value]
```

- [ ] **Step 2: Commit**

```bash
git add backend/routes/cdp.py
git commit -m "feat(phase3): routes/cdp — GET with lazy backfill fallback"
```

---

### Task 7.6: routes/ws.py — WebSocket /ws/realtime

**Files:**
- Create: `backend/routes/ws.py`

- [ ] **Step 1: 建檔**

```python
"""WS /ws/realtime — 前端訂閱即時訊號廣播。

X-API-Key 在 query string（標準 WS handshake 不能帶 header）。
"""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from ws_broadcaster import get_broadcaster

logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws/realtime")
async def realtime_ws(ws: WebSocket, api_key: str = Query("", alias="api_key")):
    expected = os.getenv("BFF_API_KEY", "").strip()
    if expected and api_key != expected:
        await ws.close(code=1008)  # policy violation
        return

    await ws.accept()
    bc = get_broadcaster()
    await bc.add(ws)
    try:
        while True:
            # 等 client 訊息（ping/keep-alive）；忽略內容
            msg = await ws.receive_text()
            if msg == "ping":
                await ws.send_text("pong")
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning("ws client error: %s", e)
    finally:
        await bc.remove(ws)
```

- [ ] **Step 2: Commit**

```bash
git add backend/routes/ws.py
git commit -m "feat(phase3): routes/ws — /ws/realtime broadcast endpoint"
```

---

### Task 7.7: main.py 註冊 + lifespan startup 順序 + Health 加欄

**Files:**
- Modify: `backend/main.py`
- Modify: `backend/routes/health.py`

- [ ] **Step 1: main.py 加 import + lifespan + router 註冊**

把 `backend/main.py` 改成（保留既有 Phase 1-2b 部分，加新東西）：

```python
"""FastAPI app entry."""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv(Path(__file__).resolve().parent / ".env")

from middleware.auth import APIKeyMiddleware  # noqa: E402
from routes import (
    active_signals, cache, candles, cdp as cdp_route, health,
    quote, screen, signals_history, strategies, symbols,
    watchlist, ws,
)  # noqa: E402
from services.fubon_client import get_fubon  # noqa: E402
from services.fubon_ws import get_ws_pool  # noqa: E402
from services.logging_config import configure_logging  # noqa: E402
from services.overnight import overnight_loop  # noqa: E402
from services.signal_engine import get_signal_engine  # noqa: E402
from services.supabase_client import get_supabase  # noqa: E402
from services.supabase_writer import get_supabase_writer  # noqa: E402

configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=" * 60)
    logger.info("treading-king BFF starting up")
    logger.info("=" * 60)

    fubon = get_fubon()
    await fubon.init()
    supabase = get_supabase()
    supabase.init()

    pool = get_ws_pool()
    await pool.start()

    writer = get_supabase_writer()
    await writer.start()

    engine = get_signal_engine()
    await engine.start()

    # 訂閱 watchlist 內所有 symbols（用 watchlist owner）
    if supabase.client is not None:
        try:
            res = await asyncio.to_thread(
                lambda: supabase.client.table("watchlist").select("symbol").execute()
            )
            for r in (res.data or []):
                try:
                    await pool.subscribe(r["symbol"], owner_id="watchlist")
                except RuntimeError as e:
                    logger.warning("startup ws sub %s failed: %s", r["symbol"], e)
        except Exception as e:
            logger.error("startup watchlist sub failed: %s", e)

    # 啟動 overnight 8:25 cron
    overnight_task = asyncio.create_task(overnight_loop())

    logger.info("Startup done — fubon=%s, supabase=%s, ws_pool=%s",
                fubon.status.value, supabase.status.value, pool.status.value)
    yield

    logger.info("Shutting down…")
    overnight_task.cancel()
    await engine.shutdown()
    await writer.shutdown()
    await pool.shutdown()
    await fubon.shutdown()
    logger.info("Shutdown complete")


app = FastAPI(title="treading-king BFF", version="0.3.0", lifespan=lifespan)

allowed_origins = ["http://localhost:5173", "http://127.0.0.1:5173"]
extra_origin = os.getenv("FRONTEND_ORIGIN", "").strip()
if extra_origin:
    allowed_origins.append(extra_origin)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(APIKeyMiddleware)

app.include_router(health.router)
app.include_router(quote.router)
app.include_router(symbols.router)
app.include_router(cache.router)
app.include_router(screen.router)
app.include_router(strategies.router)
app.include_router(watchlist.router)
app.include_router(active_signals.router)
app.include_router(signals_history.router)
app.include_router(candles.router)
app.include_router(cdp_route.router)
app.include_router(ws.router)


@app.get("/")
async def root() -> dict:
    return {
        "name": "treading-king",
        "version": "0.3.0",
        "docs": "/docs",
        "health": "/api/health",
    }
```

- [ ] **Step 2: health.py 加 ws_connections + signal_engine 欄**

打開 `backend/routes/health.py`，在既有 dict return 內加新 fields：

```python
# 在 health.py 既有 health() 函式 return 之前加：

from services.fubon_ws import get_ws_pool, WS_PER_CONN_CAP, MAX_CONNS
from services.signal_engine import get_signal_engine
from services.supabase_writer import get_supabase_writer

# (... 既有邏輯 ...)

ws_pool = get_ws_pool()
engine = get_signal_engine()
writer = get_supabase_writer()

return {
    "status": overall,
    "fubon_status": fubon.status.value,
    "fubon_last_error": fubon.last_error,
    "supabase_status": supabase.status.value,
    "supabase_last_error": supabase.last_error,
    "is_trading_day": is_trading_day,
    "cache_last_success_at": cache_last_success_at,
    "cache_last_run_status": cache_last_run_status,
    # Phase 3 新增
    "ws_connections": {
        "active": ws_pool.conn_count(),
        "subscribed_symbols": ws_pool.total_subscribed(),
        "max_capacity": MAX_CONNS * WS_PER_CONN_CAP,
        "status": ws_pool.status.value,
    },
    "signal_engine": engine.health() | {"writer_buffer": writer.metrics()["buffer_size"]},
}
```

- [ ] **Step 3: import 驗證 main 跟 health**

```powershell
$env:PYTHONPATH = "C:\side-project\trading-king\backend"; & "C:\side-project\trading-king\backend\.venv\Scripts\python.exe" -c "import os; os.chdir('C:/side-project/trading-king/backend'); from main import app; print('routes:', len(app.routes)); print(sorted([r.path for r in app.routes if hasattr(r,'path') and r.path.startswith('/api/') or r.path.startswith('/ws/')]))"
```

Expected: ~14 routes 含 `/api/watchlist`, `/api/active_signals`, `/api/signals/history`, `/api/candles/{symbol}/intraday`, `/api/cdp/{symbol}`, `/ws/realtime`。

- [ ] **Step 4: Commit**

```bash
git add backend/main.py backend/routes/health.py
git commit -m "feat(phase3): main.py lifespan + register all routes + health 加 ws/engine 欄"
```

---
## Task Group 8 — Frontend api + hooks

### Task 8.1: api.ts 擴充 types + methods

**Files:**
- Modify: `frontend/src/lib/api.ts`

- [ ] **Step 1: 加 Phase 3 types（在 ALL_FIELDS / ConditionField 區塊）**

把 `ALL_FIELDS` 加 5 個 cdp，`ConditionField` 自動跟 backend 對齊：

```typescript
// 替換既有 ALL_FIELDS
export const ALL_FIELDS = [
  "close", "change_pct", "volume", "amount",
  "rsi_14", "macd", "macd_signal",
  "kdj_k", "kdj_d", "kdj_j",
  "sma_5", "sma_20", "sma_60",
  "bbands_upper", "bbands_middle", "bbands_lower",
  // Phase 3
  "cdp_ah", "cdp_nh", "cdp", "cdp_nl", "cdp_al",
] as const;
```

- [ ] **Step 2: 在 api.ts 末尾加新 types**

```typescript
// ---------------------------------------------------------------------------
// Phase 3: WindowCondition / ActiveSignal / Watchlist / Candles / CDP
// ---------------------------------------------------------------------------

export type WindowConditionType = "price_change_pct" | "volume_burst" | "trade_count";
export type WindowSeconds = 60 | 180 | 300 | 600 | 1800;

export interface WindowCondition {
  type: WindowConditionType;
  window_seconds: WindowSeconds;
  operator: "gt" | "gte" | "lt" | "lte";
  value: number;
}

export interface ActiveFilter extends Filter {
  window_conditions?: WindowCondition[];
}

export type Scope =
  | { type: "watchlist" }
  | { type: "symbols"; symbols: string[] };

export interface ActiveSignal {
  id: string;
  name: string;
  filter_json: ActiveFilter;
  scope: Scope;
  cooldown_seconds: number;
  ignore_auctions: boolean;
  enabled: boolean;
  created_at: string;
}

export interface ActiveSignalsResponse {
  active_signals: ActiveSignal[];
}

export interface WatchlistRow {
  symbol: string;
  name: string | null;
  market: string | null;
  is_etf: boolean | null;
  added_at: string | null;
  note: string | null;
}

export interface WatchlistResponse {
  watchlist: WatchlistRow[];
  count: number;
}

export interface IntradayCandle {
  date: string;       // ISO with offset, e.g. "2026-05-12T09:00:00.000+08:00"
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  average: number;    // 富邦給的 minute VWAP
}

export interface IntradayCandlesResponse {
  date: string;
  symbol: string;
  data: IntradayCandle[];
}

export interface CdpLevels {
  ah: number;
  nh: number;
  cdp: number;
  nl: number;
  al: number;
  as_of_date: string;
}

export interface SignalLogRow {
  id: number;
  active_signal_id: string | null;
  symbol: string;
  triggered_at: string;
  trigger_price: number | null;
  trigger_volume: number | null;
  context_json: Record<string, unknown> | null;
}

export interface SignalsHistoryResponse {
  signals: SignalLogRow[];
  count: number;
}

// Realtime WS payload
export interface SignalEvent {
  event: "signal";
  data: {
    active_signal_id: string;
    active_signal_name: string;
    symbol: string;
    triggered_at: string;
    trigger_price: number;
    trigger_volume: number;
  };
}
```

- [ ] **Step 3: api 物件加新 methods**

把 `export const api = {...}` 末尾加：

```typescript
  watchlist: {
    list: () => fetchJSON<WatchlistResponse>("/api/watchlist"),
    add: (symbol: string, note?: string) =>
      fetchJSON<{symbol: string; status: string}>("/api/watchlist", {
        method: "POST",
        body: JSON.stringify({ symbol, note: note ?? null }),
      }),
    remove: (symbol: string) =>
      fetchJSON<void>(`/api/watchlist/${encodeURIComponent(symbol)}`, {
        method: "DELETE",
      }),
  },

  activeSignals: {
    list: () => fetchJSON<ActiveSignalsResponse>("/api/active_signals"),
    create: (payload: Omit<ActiveSignal, "id" | "created_at">) =>
      fetchJSON<ActiveSignal>("/api/active_signals", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    update: (id: string, payload: Omit<ActiveSignal, "id" | "created_at">) =>
      fetchJSON<ActiveSignal>(`/api/active_signals/${encodeURIComponent(id)}`, {
        method: "PUT",
        body: JSON.stringify(payload),
      }),
    delete: (id: string) =>
      fetchJSON<void>(`/api/active_signals/${encodeURIComponent(id)}`, {
        method: "DELETE",
      }),
  },

  signalsHistory: (params: {
    symbol?: string; active_signal_id?: string;
    since?: string; limit?: number;
  } = {}) => {
    const qs = new URLSearchParams();
    if (params.symbol) qs.set("symbol", params.symbol);
    if (params.active_signal_id) qs.set("active_signal_id", params.active_signal_id);
    if (params.since) qs.set("since", params.since);
    if (params.limit) qs.set("limit", String(params.limit));
    return fetchJSON<SignalsHistoryResponse>(`/api/signals/history?${qs.toString()}`);
  },

  candlesIntraday: (symbol: string) =>
    fetchJSON<IntradayCandlesResponse>(
      `/api/candles/${encodeURIComponent(symbol)}/intraday`,
    ),

  cdp: (symbol: string) =>
    fetchJSON<CdpLevels>(`/api/cdp/${encodeURIComponent(symbol)}`),
```

- [ ] **Step 4: vite build 驗 type**

```powershell
cd C:\side-project\trading-king\frontend; npx vite build 2>&1 | Select-Object -Last 6
```

Expected: `built in ...ms`，無 error。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/api.ts
git commit -m "feat(phase3): api.ts 擴充 — WindowCondition / ActiveSignal / Watchlist / Candles / CDP types + methods"
```

---

### Task 8.2: useWatchlist + useActiveSignals hook

**Files:**
- Create: `frontend/src/hooks/useWatchlist.ts`
- Create: `frontend/src/hooks/useActiveSignals.ts`

- [ ] **Step 1: useWatchlist.ts**

```typescript
import { useCallback, useEffect, useState } from "react";
import { api, type WatchlistRow } from "../lib/api";

export function useWatchlist() {
  const [items, setItems] = useState<WatchlistRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await api.watchlist.list();
      setItems(r.watchlist);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const add = useCallback(async (symbol: string) => {
    await api.watchlist.add(symbol);
    await refresh();
  }, [refresh]);

  const remove = useCallback(async (symbol: string) => {
    await api.watchlist.remove(symbol);
    await refresh();
  }, [refresh]);

  return { items, loading, error, refresh, add, remove };
}
```

- [ ] **Step 2: useActiveSignals.ts**

```typescript
import { useCallback, useEffect, useState } from "react";
import { api, type ActiveSignal } from "../lib/api";

export function useActiveSignals() {
  const [items, setItems] = useState<ActiveSignal[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await api.activeSignals.list();
      setItems(r.active_signals);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const create = useCallback(async (payload: Omit<ActiveSignal, "id" | "created_at">) => {
    await api.activeSignals.create(payload);
    await refresh();
  }, [refresh]);

  const update = useCallback(async (id: string, payload: Omit<ActiveSignal, "id" | "created_at">) => {
    await api.activeSignals.update(id, payload);
    await refresh();
  }, [refresh]);

  const remove = useCallback(async (id: string) => {
    await api.activeSignals.delete(id);
    await refresh();
  }, [refresh]);

  return { items, loading, error, refresh, create, update, remove };
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/hooks/useWatchlist.ts frontend/src/hooks/useActiveSignals.ts
git commit -m "feat(phase3): useWatchlist + useActiveSignals hooks"
```

---

### Task 8.3: useIntradayCandles hook (REST + WS hybrid)

**Files:**
- Create: `frontend/src/hooks/useIntradayCandles.ts`

- [ ] **Step 1: 建檔**

```typescript
import { useCallback, useEffect, useRef, useState } from "react";
import { api, type IntradayCandle } from "../lib/api";

const REFRESH_MS = 60_000;

export function useIntradayCandles(symbol: string | null) {
  const [candles, setCandles] = useState<IntradayCandle[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchOnce = useCallback(async (s: string) => {
    setLoading(true);
    setError(null);
    try {
      const r = await api.candlesIntraday(s);
      setCandles(r.data ?? []);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!symbol) { setCandles([]); return; }
    fetchOnce(symbol);
    timerRef.current = setInterval(() => fetchOnce(symbol), REFRESH_MS);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [symbol, fetchOnce]);

  // WS tick 更新最後一根 candle close（不重算 average）
  const onTick = useCallback((tickSymbol: string, price: number) => {
    if (tickSymbol !== symbol) return;
    setCandles((prev) => {
      if (prev.length === 0) return prev;
      const last = prev[prev.length - 1];
      const updated = { ...last, close: price };
      if (price > last.high) updated.high = price;
      if (price < last.low) updated.low = price;
      return [...prev.slice(0, -1), updated];
    });
  }, [symbol]);

  return { candles, loading, error, onTick, refetch: () => symbol && fetchOnce(symbol) };
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/hooks/useIntradayCandles.ts
git commit -m "feat(phase3): useIntradayCandles — REST 60s 輪詢 + WS tick 末端更新"
```

---

### Task 8.4: useSignalsStream hook（WS /ws/realtime）

**Files:**
- Create: `frontend/src/hooks/useSignalsStream.ts`

- [ ] **Step 1: 建檔**

```typescript
import { useCallback, useEffect, useRef, useState } from "react";
import { type SignalEvent } from "../lib/api";

const RECONNECT_DELAYS_MS = [1000, 2000, 4000, 8000, 16000, 30000];

export type WSStatus = "connecting" | "open" | "closed";

export function useSignalsStream(opts?: {
  onSignal?: (s: SignalEvent["data"]) => void;
  onTick?: (symbol: string, price: number) => void;  // 預留：未來 backend 廣播 tick 給 chart 用
}) {
  const [status, setStatus] = useState<WSStatus>("connecting");
  const [recent, setRecent] = useState<SignalEvent["data"][]>([]);
  const wsRef = useRef<WebSocket | null>(null);
  const attemptRef = useRef(0);
  const onSignalRef = useRef(opts?.onSignal);
  const onTickRef = useRef(opts?.onTick);

  useEffect(() => { onSignalRef.current = opts?.onSignal; }, [opts?.onSignal]);
  useEffect(() => { onTickRef.current = opts?.onTick; }, [opts?.onTick]);

  const connect = useCallback(() => {
    setStatus("connecting");
    const apiKey = (import.meta.env.VITE_BFF_API_KEY ?? "") as string;
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${location.host}/ws/realtime?api_key=${encodeURIComponent(apiKey)}`;
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      setStatus("open");
      attemptRef.current = 0;
    };

    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.event === "signal") {
          const data = msg.data as SignalEvent["data"];
          setRecent((prev) => [data, ...prev].slice(0, 50));
          onSignalRef.current?.(data);
        } else if (msg.event === "tick") {
          onTickRef.current?.(msg.data.symbol, msg.data.price);
        }
      } catch { /* ignore */ }
    };

    ws.onclose = () => {
      setStatus("closed");
      const delay = RECONNECT_DELAYS_MS[Math.min(attemptRef.current, RECONNECT_DELAYS_MS.length - 1)];
      attemptRef.current += 1;
      setTimeout(connect, delay);
    };

    ws.onerror = () => { /* close 會跟著觸發 */ };
  }, []);

  useEffect(() => {
    connect();
    return () => {
      wsRef.current?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { status, recent };
}
```

- [ ] **Step 2: vite build 驗**

```powershell
cd C:\side-project\trading-king\frontend; npx vite build 2>&1 | Select-Object -Last 4
```

Expected: 無 error。

- [ ] **Step 3: Commit**

```bash
git add frontend/src/hooks/useSignalsStream.ts
git commit -m "feat(phase3): useSignalsStream — /ws/realtime + 自動重連"
```

---

## Task Group 9 — Frontend Watchlist + IntradayChart

### Task 9.1: Sparkline + IntradayChart 元件

**Files:**
- Create: `frontend/src/components/Sparkline.tsx`
- Create: `frontend/src/components/IntradayChart.tsx`

- [ ] **Step 1: Sparkline.tsx**

```tsx
interface Props {
  values: number[];
  width?: number;
  height?: number;
}

export function Sparkline({ values, width = 80, height = 24 }: Props) {
  if (values.length < 2) return <span className="text-ink-dim">—</span>;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const dx = width / (values.length - 1);
  const points = values
    .map((v, i) => `${(i * dx).toFixed(1)},${(height - ((v - min) / range) * height).toFixed(1)}`)
    .join(" ");
  const up = values[values.length - 1] >= values[0];
  const stroke = up ? "var(--color-bull, #e85a4f)" : "var(--color-bear, #7fc99a)";
  return (
    <svg width={width} height={height} aria-hidden>
      <polyline points={points} fill="none" stroke={stroke} strokeWidth="1.5" />
    </svg>
  );
}
```

- [ ] **Step 2: IntradayChart.tsx — 主圖含 VWAP + CDP toggle**

```tsx
import { useEffect, useMemo, useState } from "react";
import { api, type CdpLevels, type IntradayCandle } from "../lib/api";

interface Props {
  symbol: string;
  candles: IntradayCandle[];
  loading: boolean;
}

const CHART_W = 720;
const CHART_H = 360;
const PAD_L = 56;
const PAD_R = 12;
const PAD_T = 12;
const PAD_B = 28;

export function IntradayChart({ symbol, candles, loading }: Props) {
  const [showVwap, setShowVwap] = useState(true);
  const [showCdp, setShowCdp] = useState(false);
  const [cdp, setCdp] = useState<CdpLevels | null>(null);
  const [cdpError, setCdpError] = useState<string | null>(null);

  useEffect(() => {
    if (!showCdp) return;
    setCdpError(null);
    api.cdp(symbol).then(setCdp).catch((e) =>
      setCdpError(e instanceof Error ? e.message : String(e))
    );
  }, [symbol, showCdp]);

  const { yMin, yMax, scaleX, scaleY, polyClose, polyVwap } = useMemo(() => {
    if (candles.length === 0) {
      return { yMin: 0, yMax: 0, scaleX: () => 0, scaleY: () => 0, polyClose: "", polyVwap: "" };
    }
    const closes = candles.map((c) => c.close);
    const vwaps = candles.map((c) => c.average);
    const allY = [...closes, ...vwaps];
    if (cdp && showCdp) allY.push(cdp.ah, cdp.al);
    const yMin = Math.min(...allY) * 0.998;
    const yMax = Math.max(...allY) * 1.002;
    const xRange = CHART_W - PAD_L - PAD_R;
    const yRange = CHART_H - PAD_T - PAD_B;
    const scaleX = (i: number) => PAD_L + (i / Math.max(candles.length - 1, 1)) * xRange;
    const scaleY = (v: number) => PAD_T + (1 - (v - yMin) / (yMax - yMin || 1)) * yRange;
    const polyClose = candles.map((c, i) => `${scaleX(i)},${scaleY(c.close)}`).join(" ");
    const polyVwap = candles.map((c, i) => `${scaleX(i)},${scaleY(c.average)}`).join(" ");
    return { yMin, yMax, scaleX, scaleY, polyClose, polyVwap };
  }, [candles, cdp, showCdp]);

  const latest = candles[candles.length - 1];
  const first = candles[0];
  const change = latest && first ? latest.close - first.open : 0;
  const isUp = change > 0;
  const dirCls = isUp ? "text-bull" : change < 0 ? "text-bear" : "text-ink-muted";

  return (
    <div>
      {loading && candles.length === 0 ? (
        <div className="h-[360px] flex items-center justify-center text-ink-dim font-serif italic">
          分時資料載入中…
        </div>
      ) : (
        <svg viewBox={`0 0 ${CHART_W} ${CHART_H}`} className="w-full h-auto">
          {/* Y 軸格線 + label (簡單 5 條) */}
          {[0, 0.25, 0.5, 0.75, 1].map((p) => {
            const v = yMin + (yMax - yMin) * (1 - p);
            const y = PAD_T + p * (CHART_H - PAD_T - PAD_B);
            return (
              <g key={p}>
                <line x1={PAD_L} y1={y} x2={CHART_W - PAD_R} y2={y}
                  stroke="var(--color-line, #2e2a22)" strokeWidth="0.5" />
                <text x={PAD_L - 4} y={y + 3} textAnchor="end"
                  className="fill-ink-dim text-[10px] tabular-nums">{v.toFixed(1)}</text>
              </g>
            );
          })}

          {/* CDP 5 線 */}
          {showCdp && cdp && (
            <>
              {(["ah", "nh", "cdp", "nl", "al"] as const).map((k) => (
                <g key={k}>
                  <line x1={PAD_L} y1={scaleY(cdp[k])} x2={CHART_W - PAD_R} y2={scaleY(cdp[k])}
                    stroke="var(--color-accent, #e85a4f)" strokeWidth="0.6"
                    strokeDasharray="4 3" opacity="0.6" />
                  <text x={CHART_W - PAD_R - 2} y={scaleY(cdp[k]) - 2} textAnchor="end"
                    className="fill-accent text-[10px] uppercase">
                    {k.toUpperCase()} {cdp[k].toFixed(1)}
                  </text>
                </g>
              ))}
            </>
          )}

          {/* VWAP */}
          {showVwap && polyVwap && (
            <polyline points={polyVwap} fill="none"
              stroke="var(--color-ink-dim, #8a8273)" strokeWidth="1" strokeDasharray="3 2" />
          )}

          {/* 主價線 */}
          {polyClose && (
            <polyline points={polyClose} fill="none"
              stroke="var(--color-ink, #ede4d3)" strokeWidth="1.5" />
          )}

          {/* X 軸時間 label */}
          {[0, 0.25, 0.5, 0.75, 1].map((p) => {
            if (candles.length === 0) return null;
            const idx = Math.floor((candles.length - 1) * p);
            const x = scaleX(idx);
            const t = new Date(candles[idx].date);
            const hh = String(t.getHours()).padStart(2, "0");
            const mm = String(t.getMinutes()).padStart(2, "0");
            return (
              <text key={p} x={x} y={CHART_H - 8} textAnchor="middle"
                className="fill-ink-dim text-[10px] tabular-nums">{hh}:{mm}</text>
            );
          })}
        </svg>
      )}

      {/* 報價 + toggle */}
      {latest && (
        <div className="mt-2 flex items-baseline justify-between border-t border-line pt-2">
          <div className="flex items-baseline gap-3">
            <span className={`font-serif italic text-xl ${dirCls} tabular-nums`}>
              {latest.close.toFixed(2)}
            </span>
            <span className={`text-sm ${dirCls} tabular-nums`}>
              {isUp ? "▲" : change < 0 ? "▾" : "—"} {Math.abs(change).toFixed(2)}
            </span>
          </div>
          <div className="flex gap-2 text-xs">
            <button
              type="button"
              onClick={() => setShowVwap((v) => !v)}
              className={`px-2 py-1 border ${showVwap ? "border-accent text-accent" : "border-line text-ink-dim"}`}
            >{showVwap ? "✓" : ""} VWAP</button>
            <button
              type="button"
              onClick={() => setShowCdp((v) => !v)}
              className={`px-2 py-1 border ${showCdp ? "border-accent text-accent" : "border-line text-ink-dim"}`}
            >{showCdp ? "✓" : ""} CDP</button>
          </div>
        </div>
      )}
      {showCdp && cdpError && (
        <div className="mt-1 text-xs text-bear">CDP 無資料：{cdpError}</div>
      )}
    </div>
  );
}
```

- [ ] **Step 3: vite build 驗 type**

```powershell
cd C:\side-project\trading-king\frontend; npx vite build 2>&1 | Select-Object -Last 4
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/Sparkline.tsx frontend/src/components/IntradayChart.tsx
git commit -m "feat(phase3): IntradayChart (VWAP + CDP toggle, SVG only) + Sparkline"
```

---

### Task 9.2: SymbolSearch + Watchlist.tsx

**Files:**
- Create: `frontend/src/components/SymbolSearch.tsx`
- Create: `frontend/src/pages/Watchlist.tsx`

- [ ] **Step 1: SymbolSearch.tsx**

```tsx
import { useEffect, useState } from "react";
import { api, type SymbolSearchRow } from "../lib/api";

interface Props {
  onPick: (symbol: string) => void;
  placeholder?: string;
}

export function SymbolSearch({ onPick, placeholder = "搜尋代號或名稱..." }: Props) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<SymbolSearchRow[]>([]);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!q.trim()) { setResults([]); return; }
    const t = setTimeout(async () => {
      try {
        const r = await api.symbols(q.trim(), 10);
        setResults(r.results);
      } catch { /* ignore */ }
    }, 200);
    return () => clearTimeout(t);
  }, [q]);

  return (
    <div className="relative">
      <input
        type="text"
        value={q}
        onChange={(e) => { setQ(e.target.value); setOpen(true); }}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        onFocus={() => setOpen(true)}
        placeholder={placeholder}
        className="w-full bg-bg-deep border border-line text-ink px-3 py-2 outline-none focus:border-accent text-sm"
      />
      {open && results.length > 0 && (
        <div className="absolute z-10 left-0 right-0 mt-1 max-h-64 overflow-y-auto bg-bg-card border border-line">
          {results.map((r) => (
            <button
              key={r.symbol}
              type="button"
              onClick={() => { onPick(r.symbol); setQ(""); setResults([]); setOpen(false); }}
              className="w-full text-left px-3 py-2 hover:bg-bg-deep flex items-baseline justify-between"
            >
              <span className="text-sm">
                <span className="font-medium text-ink">{r.symbol}</span>
                <span className="ml-2 text-ink-muted">{r.name}</span>
              </span>
              <span className="text-2xs text-ink-dim">{r.market}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Watchlist.tsx**

```tsx
import { useState } from "react";
import { IntradayChart } from "../components/IntradayChart";
import { SymbolSearch } from "../components/SymbolSearch";
import { useIntradayCandles } from "../hooks/useIntradayCandles";
import { useSignalsStream } from "../hooks/useSignalsStream";
import { useWatchlist } from "../hooks/useWatchlist";

export function Watchlist() {
  const { items, loading, error, add, remove } = useWatchlist();
  const [selected, setSelected] = useState<string | null>(null);
  const { candles, loading: candlesLoading, onTick } = useIntradayCandles(selected);
  useSignalsStream({ onTick });

  return (
    <div className="mx-auto max-w-[1200px] px-12 py-12 max-md:px-6 max-md:py-6 grid grid-cols-[360px_1fr] gap-8 max-md:grid-cols-1">
      <section>
        <div className="label-small text-accent mb-2.5">壹</div>
        <h2 className="h-display text-[28px] mb-4">自選清單</h2>

        <div className="mb-4">
          <SymbolSearch onPick={(s) => add(s).catch(() => {})} />
        </div>

        {error && (
          <div className="border border-accent/40 bg-accent/10 px-3 py-2 mb-3 text-xs text-bear">
            {error}
          </div>
        )}

        {loading && items.length === 0 ? (
          <div className="text-ink-dim text-sm">載入中…</div>
        ) : items.length === 0 ? (
          <div className="border border-line p-6 text-center text-ink-dim font-serif italic">
            自選清單還是空的 — 上面搜尋加入第一檔股票
          </div>
        ) : (
          <ul className="border-t border-line">
            {items.map((it) => {
              const isSel = it.symbol === selected;
              return (
                <li key={it.symbol}
                  className={`border-b border-line ${isSel ? "bg-bg-card" : "hover:bg-bg-card/40"}`}>
                  <button type="button"
                    onClick={() => setSelected(it.symbol)}
                    className="w-full text-left px-3 py-2.5">
                    <div className="flex items-baseline justify-between">
                      <span className="font-medium text-ink">{it.symbol}</span>
                      <span className="text-2xs text-ink-dim">{it.market}</span>
                    </div>
                    <div className="mt-0.5 text-sm text-ink-muted">{it.name ?? "—"}</div>
                  </button>
                  <button type="button"
                    onClick={() => remove(it.symbol)}
                    className="absolute right-2 top-2 text-ink-dim hover:text-bear text-xs">
                    ×
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <section>
        <div className="label-small text-accent mb-2.5">貳</div>
        <h2 className="h-display text-[28px] mb-4">分時走勢</h2>

        {!selected ? (
          <div className="h-[400px] flex items-center justify-center border border-line text-ink-dim font-serif italic">
            ← 點選左邊任一檔股票看分時走勢
          </div>
        ) : (
          <div className="border border-line p-4">
            <div className="text-xs text-ink-dim mb-2">{selected}</div>
            <IntradayChart symbol={selected} candles={candles} loading={candlesLoading} />
          </div>
        )}
      </section>
    </div>
  );
}
```

- [ ] **Step 3: vite build**

```powershell
cd C:\side-project\trading-king\frontend; npx vite build 2>&1 | Select-Object -Last 4
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/SymbolSearch.tsx frontend/src/pages/Watchlist.tsx
git commit -m "feat(phase3): Watchlist 頁 + SymbolSearch + IntradayChart 整合"
```

---

## Task Group 10 — Frontend Signals + ActiveSignalEditor

### Task 10.1: ActiveSignalEditor (modal)

**Files:**
- Create: `frontend/src/components/ActiveSignalEditor.tsx`

- [ ] **Step 1: 建檔**

```tsx
import { useState } from "react";
import {
  ALL_FIELDS, api, type ActiveFilter, type ActiveSignal, type Condition,
  type ConditionField, type ConditionOperator, type Scope, type WindowCondition,
  type WindowConditionType, type WindowSeconds,
} from "../lib/api";

const FIELD_LABEL: Record<ConditionField, string> = {
  close: "收盤價", change_pct: "漲跌幅 %", volume: "成交量", amount: "成交金額",
  rsi_14: "RSI(14)", macd: "MACD", macd_signal: "MACD signal",
  kdj_k: "KDJ K", kdj_d: "KDJ D", kdj_j: "KDJ J",
  sma_5: "5 日均線", sma_20: "20 日均線", sma_60: "60 日均線",
  bbands_upper: "BB 上軌", bbands_middle: "BB 中軌", bbands_lower: "BB 下軌",
  cdp_ah: "CDP AH (最高值)", cdp_nh: "CDP NH (近高)", cdp: "CDP 中軸",
  cdp_nl: "CDP NL (近低)", cdp_al: "CDP AL (最低值)",
};

const OP_LABEL: Record<ConditionOperator, string> = {
  gt: ">", gte: "≥", lt: "<", lte: "≤", eq: "=",
};

const WINDOW_OPTIONS: { value: WindowSeconds; label: string }[] = [
  { value: 60, label: "1 分鐘" }, { value: 180, label: "3 分鐘" },
  { value: 300, label: "5 分鐘" }, { value: 600, label: "10 分鐘" },
  { value: 1800, label: "30 分鐘" },
];

const WINDOW_TYPE_LABEL: Record<WindowConditionType, string> = {
  price_change_pct: "漲跌幅 %", volume_burst: "累積成交量", trade_count: "成交筆數",
};

interface Props {
  initial?: ActiveSignal;
  onClose: () => void;
  onSaved: () => void;
}

export function ActiveSignalEditor({ initial, onClose, onSaved }: Props) {
  const [name, setName] = useState(initial?.name ?? "");
  const [filter, setFilter] = useState<ActiveFilter>(initial?.filter_json ?? {
    market: ["TWSE", "OTC"], exclude_etf: true,
    conditions: [], window_conditions: [], logic: "AND", limit: 200,
  });
  const [scope, setScope] = useState<Scope>(initial?.scope ?? { type: "watchlist" });
  const [cooldown, setCooldown] = useState(initial?.cooldown_seconds ?? 1800);
  const [ignoreAuctions, setIgnoreAuctions] = useState(initial?.ignore_auctions ?? true);
  const [enabled, setEnabled] = useState(initial?.enabled ?? true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function addWindow() {
    setFilter({
      ...filter,
      window_conditions: [
        ...(filter.window_conditions ?? []),
        { type: "price_change_pct", window_seconds: 300, operator: "gt", value: 2 },
      ],
    });
  }
  function updateWindow(i: number, w: WindowCondition) {
    const next = [...(filter.window_conditions ?? [])];
    next[i] = w;
    setFilter({ ...filter, window_conditions: next });
  }
  function removeWindow(i: number) {
    setFilter({ ...filter, window_conditions: (filter.window_conditions ?? []).filter((_, j) => j !== i) });
  }
  function addCond() {
    setFilter({
      ...filter,
      conditions: [...filter.conditions, { field: "close", operator: "gt", value: 0, days_ago: 0 }],
    });
  }
  function updateCond(i: number, c: Condition) {
    const next = [...filter.conditions];
    next[i] = c;
    setFilter({ ...filter, conditions: next });
  }
  function removeCond(i: number) {
    setFilter({ ...filter, conditions: filter.conditions.filter((_, j) => j !== i) });
  }

  async function save() {
    if (!name.trim()) { setError("請輸入名稱"); return; }
    if (filter.conditions.length === 0 && (filter.window_conditions ?? []).length === 0) {
      setError("至少要有一條條件"); return;
    }
    setSaving(true);
    setError(null);
    try {
      const payload = { name: name.trim(), filter_json: filter, scope, cooldown_seconds: cooldown, ignore_auctions: ignoreAuctions, enabled };
      if (initial) await api.activeSignals.update(initial.id, payload);
      else await api.activeSignals.create(payload);
      onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally { setSaving(false); }
  }

  return (
    <div className="fixed inset-0 z-50 bg-bg-deep/80 flex items-center justify-center p-4">
      <div className="bg-bg-card border border-line max-w-3xl w-full max-h-[90vh] overflow-y-auto p-6">
        <div className="flex items-baseline justify-between mb-4">
          <h3 className="h-display text-[24px]">{initial ? "編輯訊號規則" : "新增訊號規則"}</h3>
          <button type="button" onClick={onClose} className="text-ink-dim hover:text-ink text-xl">×</button>
        </div>

        <label className="block text-xs text-ink-dim mb-1">名稱</label>
        <input value={name} onChange={(e) => setName(e.target.value)}
          className="w-full bg-bg-deep border border-line px-3 py-2 mb-5 text-sm text-ink outline-none focus:border-accent" />

        {/* WindowCondition 區塊 */}
        <div className="border-t border-line pt-3 mb-4">
          <div className="label-tiny mb-2">即時時窗條件</div>
          {(filter.window_conditions ?? []).map((w, i) => (
            <div key={i} className="flex items-center gap-2 mb-2">
              <select value={w.type} onChange={(e) => updateWindow(i, { ...w, type: e.target.value as WindowConditionType })}
                className="bg-bg-deep border border-line text-sm px-2 py-1">
                {Object.entries(WINDOW_TYPE_LABEL).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
              <select value={w.window_seconds} onChange={(e) => updateWindow(i, { ...w, window_seconds: Number(e.target.value) as WindowSeconds })}
                className="bg-bg-deep border border-line text-sm px-2 py-1">
                {WINDOW_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
              <select value={w.operator} onChange={(e) => updateWindow(i, { ...w, operator: e.target.value as WindowCondition["operator"] })}
                className="bg-bg-deep border border-line text-sm px-2 py-1 w-12 text-center">
                {(["gt","gte","lt","lte"] as const).map(op => <option key={op} value={op}>{OP_LABEL[op]}</option>)}
              </select>
              <input type="number" step="any" value={w.value}
                onChange={(e) => updateWindow(i, { ...w, value: Number(e.target.value) })}
                className="bg-bg-deep border border-line text-sm px-2 py-1 w-24 tabular-nums" />
              <button type="button" onClick={() => removeWindow(i)} className="text-ink-dim hover:text-bear">×</button>
            </div>
          ))}
          <button type="button" onClick={addWindow} className="text-xs text-ink-dim hover:text-accent border border-dashed border-line px-3 py-1">+ 新增時窗條件</button>
        </div>

        {/* Filter.conditions 區塊 */}
        <div className="border-t border-line pt-3 mb-4">
          <div className="label-tiny mb-2">跨指標條件 (從快取)</div>
          {filter.conditions.map((c, i) => {
            const valIsField = typeof c.value === "string";
            return (
              <div key={i} className="flex items-center gap-2 mb-2">
                <select value={c.field} onChange={(e) => updateCond(i, { ...c, field: e.target.value as ConditionField })}
                  className="bg-bg-deep border border-line text-sm px-2 py-1">
                  {ALL_FIELDS.map(f => <option key={f} value={f}>{FIELD_LABEL[f]}</option>)}
                </select>
                <select value={c.operator} onChange={(e) => updateCond(i, { ...c, operator: e.target.value as ConditionOperator })}
                  className="bg-bg-deep border border-line text-sm px-2 py-1 w-12 text-center">
                  {(["gt","gte","lt","lte","eq"] as const).map(op => <option key={op} value={op}>{OP_LABEL[op]}</option>)}
                </select>
                <div className="inline-flex border border-line">
                  <button type="button"
                    onClick={() => updateCond(i, { ...c, value: 0 })}
                    className={`px-2 py-1 text-xs ${!valIsField ? "bg-accent/20 text-accent" : "text-ink-dim"}`}>常數</button>
                  <button type="button"
                    onClick={() => updateCond(i, { ...c, value: "sma_20" })}
                    className={`px-2 py-1 text-xs border-l border-line ${valIsField ? "bg-accent/20 text-accent" : "text-ink-dim"}`}>欄位</button>
                </div>
                {!valIsField ? (
                  <input type="number" step="any" value={c.value as number}
                    onChange={(e) => updateCond(i, { ...c, value: Number(e.target.value) })}
                    className="bg-bg-deep border border-line text-sm px-2 py-1 w-24 tabular-nums" />
                ) : (
                  <select value={c.value as string}
                    onChange={(e) => updateCond(i, { ...c, value: e.target.value as ConditionField })}
                    className="bg-bg-deep border border-line text-sm px-2 py-1">
                    {ALL_FIELDS.map(f => <option key={f} value={f}>{FIELD_LABEL[f]}</option>)}
                  </select>
                )}
                <button type="button" onClick={() => removeCond(i)} className="text-ink-dim hover:text-bear">×</button>
              </div>
            );
          })}
          <button type="button" onClick={addCond} className="text-xs text-ink-dim hover:text-accent border border-dashed border-line px-3 py-1">+ 新增條件</button>
        </div>

        {/* Logic / Scope / Cooldown */}
        <div className="border-t border-line pt-3 mb-4 grid grid-cols-2 gap-4">
          <div>
            <div className="label-tiny mb-1">邏輯</div>
            <label className="text-sm mr-3"><input type="radio" checked={filter.logic === "AND"} onChange={() => setFilter({ ...filter, logic: "AND" })} className="accent-accent mr-1" />AND</label>
            <label className="text-sm"><input type="radio" checked={filter.logic === "OR"} onChange={() => setFilter({ ...filter, logic: "OR" })} className="accent-accent mr-1" />OR</label>
          </div>
          <div>
            <div className="label-tiny mb-1">套用範圍</div>
            <label className="text-sm mr-3"><input type="radio" checked={scope.type === "watchlist"} onChange={() => setScope({ type: "watchlist" })} className="accent-accent mr-1" />自選清單全部</label>
            <label className="text-sm"><input type="radio" checked={scope.type === "symbols"} onChange={() => setScope({ type: "symbols", symbols: [] })} className="accent-accent mr-1" />指定股票</label>
          </div>
          <div>
            <div className="label-tiny mb-1">Cooldown 秒</div>
            <input type="number" min={60} max={86400} value={cooldown}
              onChange={(e) => setCooldown(Number(e.target.value))}
              className="bg-bg-deep border border-line text-sm px-2 py-1 w-32 tabular-nums" />
          </div>
          <div>
            <div className="label-tiny mb-1">集合競價時段忽略 volume_burst</div>
            <label className="text-sm"><input type="checkbox" checked={ignoreAuctions} onChange={(e) => setIgnoreAuctions(e.target.checked)} className="accent-accent mr-1" />開啟</label>
          </div>
        </div>

        {error && <div className="border border-accent/40 bg-accent/10 px-3 py-2 mb-3 text-xs text-bear">{error}</div>}

        <div className="border-t border-line pt-3 flex justify-end gap-3">
          <button type="button" onClick={onClose} className="text-ink-dim hover:text-ink text-sm px-4 py-2">取消</button>
          <button type="button" onClick={save} disabled={saving}
            className="border-2 border-accent text-accent px-5 py-2 text-sm uppercase tracking-[2px] hover:bg-accent/10 disabled:opacity-40">
            {saving ? "儲存中…" : (initial ? "更新並啟用" : "儲存並啟用")}
          </button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: vite build**

```powershell
cd C:\side-project\trading-king\frontend; npx vite build 2>&1 | Select-Object -Last 4
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/ActiveSignalEditor.tsx
git commit -m "feat(phase3): ActiveSignalEditor modal — Window+Filter conditions + Scope + Cooldown"
```

---

### Task 10.2: Signals.tsx + SignalCard

**Files:**
- Create: `frontend/src/components/SignalCard.tsx`
- Create: `frontend/src/pages/Signals.tsx`

- [ ] **Step 1: SignalCard.tsx**

```tsx
import type { SignalEvent } from "../lib/api";

export function SignalCard({ s }: { s: SignalEvent["data"] }) {
  const t = new Date(s.triggered_at);
  const tt = `${String(t.getHours()).padStart(2,"0")}:${String(t.getMinutes()).padStart(2,"0")}:${String(t.getSeconds()).padStart(2,"0")}`;
  return (
    <div className="border-b border-line/50 py-3 px-3 hover:bg-bg-card/40">
      <div className="flex items-baseline justify-between">
        <div className="flex items-baseline gap-3">
          <span className="text-2xs text-ink-dim font-mono tabular-nums">{tt}</span>
          <span className="font-medium text-ink">{s.symbol}</span>
        </div>
        <span className="font-serif italic text-sm text-accent">{s.active_signal_name}</span>
      </div>
      <div className="mt-1 text-sm text-ink-muted tabular-nums">
        {s.trigger_price.toFixed(2)} · vol {s.trigger_volume}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Signals.tsx**

```tsx
import { useState } from "react";
import { ActiveSignalEditor } from "../components/ActiveSignalEditor";
import { SignalCard } from "../components/SignalCard";
import { type ActiveSignal } from "../lib/api";
import { useActiveSignals } from "../hooks/useActiveSignals";
import { useSignalsStream } from "../hooks/useSignalsStream";

export function Signals() {
  const { items: actives, refresh, remove } = useActiveSignals();
  const { status, recent } = useSignalsStream({
    onSignal: () => {
      // 未來：if user toggle 開聲音 → new Audio('/notify.mp3').play()
    },
  });
  const [editing, setEditing] = useState<ActiveSignal | null>(null);
  const [creating, setCreating] = useState(false);

  return (
    <div className="mx-auto max-w-[1200px] px-12 py-12 max-md:px-6 max-md:py-6 space-y-10">
      {/* 已啟用規則 */}
      <section>
        <div className="label-small text-accent mb-2.5">壹</div>
        <div className="flex items-baseline justify-between mb-4">
          <h2 className="h-display text-[28px]">即時訊號規則 ({actives.length})</h2>
          <div className="flex gap-2">
            <button type="button"
              onClick={() => setCreating(true)}
              className="border-2 border-accent text-accent px-4 py-1.5 text-xs uppercase tracking-[2px] hover:bg-accent/10">
              + 新增
            </button>
          </div>
        </div>

        {actives.length === 0 ? (
          <div className="border border-line p-6 text-center text-ink-dim font-serif italic">
            還沒有訊號規則 — 點上方「+ 新增」設第一條
          </div>
        ) : (
          <ul className="border-t border-line">
            {actives.map((a) => (
              <li key={a.id} className="border-b border-line py-3 px-3 flex items-baseline justify-between hover:bg-bg-card/40">
                <div>
                  <div className="text-base font-medium text-ink">{a.name}</div>
                  <div className="mt-0.5 text-xs text-ink-dim">
                    {a.scope.type === "watchlist" ? "自選清單全部" : `指定 ${a.scope.symbols.length} 檔`}
                    · cooldown {a.cooldown_seconds}s
                    · {a.enabled ? <span className="text-bull">啟用中</span> : <span className="text-ink-dim">停用</span>}
                  </div>
                </div>
                <div className="flex gap-2">
                  <button type="button" onClick={() => setEditing(a)}
                    className="text-xs text-ink-dim hover:text-ink">編輯</button>
                  <button type="button" onClick={() => { if (confirm(`刪除「${a.name}」？`)) remove(a.id); }}
                    className="text-xs text-ink-dim hover:text-bear">刪除</button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* 即時訊號流 */}
      <section>
        <div className="label-small text-accent mb-2.5">貳</div>
        <div className="flex items-baseline justify-between mb-4">
          <h2 className="h-display text-[28px]">即時訊號流</h2>
          <div className="text-xs text-ink-dim">
            {status === "open" ? <span className="text-bull">● 連線中</span>
              : status === "connecting" ? <span className="text-accent">● 連線中…</span>
              : <span className="text-bear">● 已斷線</span>}
            <span className="ml-3">最近 {recent.length} 筆</span>
          </div>
        </div>

        {recent.length === 0 ? (
          <div className="border border-line p-6 text-center text-ink-dim font-serif italic">
            等待第一筆訊號…
          </div>
        ) : (
          <div className="border-t border-line">
            {recent.map((s, i) => <SignalCard key={`${s.active_signal_id}-${s.triggered_at}-${i}`} s={s} />)}
          </div>
        )}
      </section>

      {(creating || editing) && (
        <ActiveSignalEditor
          initial={editing ?? undefined}
          onClose={() => { setCreating(false); setEditing(null); }}
          onSaved={() => refresh()}
        />
      )}
    </div>
  );
}
```

- [ ] **Step 3: vite build**

```powershell
cd C:\side-project\trading-king\frontend; npx vite build 2>&1 | Select-Object -Last 4
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/SignalCard.tsx frontend/src/pages/Signals.tsx
git commit -m "feat(phase3): Signals 頁 + SignalCard + 整合 editor + WS stream"
```

---

## Task Group 11 — App.tsx nav 啟用 + Health 加 row + 端到端

### Task 11.1: App.tsx 啟用 watchlist + signals tab

**Files:**
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: 把 disabled 的 nav 改成可點 + 加 page state + 對應 page 渲染**

打開 `frontend/src/App.tsx`，把 `Page` type 加 `"watchlist"` 跟 `"signals"`，nav items 拿掉 disabled，page 渲染加兩個 import + 兩個 conditional render：

```tsx
import { useEffect, useState } from "react";
import { Health } from "./pages/Health";
import { Screener } from "./pages/Screener";
import { Signals } from "./pages/Signals";
import { Watchlist } from "./pages/Watchlist";

type Page = "health" | "screener" | "signals" | "watchlist";

export default function App() {
  const [page, setPage] = useState<Page>("health");

  useEffect(() => { document.body.classList.add("opacity-100"); }, []);

  return (
    <>
      <Masthead />
      <Nav active={page} onNavigate={setPage} />
      {page === "health" && <Health />}
      {page === "screener" && <Screener />}
      {page === "signals" && <Signals />}
      {page === "watchlist" && <Watchlist />}
    </>
  );
}

// (Masthead / Meta 不變，省略)

function Nav({ active, onNavigate }: { active: Page; onNavigate: (p: Page) => void }) {
  const items: Array<{ id: Page; label: string }> = [
    { id: "health", label: "系統狀態" },
    { id: "screener", label: "篩股" },
    { id: "signals", label: "即時訊號" },
    { id: "watchlist", label: "自選" },
  ];
  return (
    <nav className="border-y border-line bg-bg-card/40">
      <div className="mx-auto flex max-w-[1200px] gap-0 px-12 max-md:px-6">
        {items.map((it) => {
          const isActive = active === it.id;
          return (
            <button key={it.id} type="button"
              onClick={() => onNavigate(it.id)}
              className={`px-4 py-3 text-xs uppercase tracking-[2px] cursor-pointer hover:text-ink bg-transparent border-b-2 ${
                isActive ? "border-accent text-ink" : "text-ink-dim border-transparent"}`}>
              {it.label}
            </button>
          );
        })}
      </div>
    </nav>
  );
}
```

（Masthead / Meta 區塊保留現有不動。）

- [ ] **Step 2: Health.tsx 加 ws_connections + signal_engine row**

打開 `frontend/src/pages/Health.tsx`，在既有 4 個 `StatusRow` 之後再加 2 個。先在 `HealthResponse` interface 補欄位（如果 api.ts 沒加）：

```typescript
// frontend/src/lib/api.ts — 在 HealthResponse interface 加：
ws_connections?: {
  active: number;
  subscribed_symbols: number;
  max_capacity: number;
  status: string;
};
signal_engine?: {
  queue_depth: number;
  lag_ms: number;
  dropped_today: number;
  degraded: boolean;
  active_count: number;
  writer_buffer: number;
};
```

然後 `Health.tsx` 加：

```tsx
{/* 在既有 StatusRow 之後加 */}
{data?.ws_connections && (
  <StatusRow
    name="WebSocket 訂閱"
    desc={`${data.ws_connections.active} 條連線、訂閱 ${data.ws_connections.subscribed_symbols} 檔`}
    status={data.ws_connections.status === "ok" ? "ok"
      : data.ws_connections.status === "circuit_open" ? "error" : "degraded"}
    customLabel={`${data.ws_connections.subscribed_symbols} / ${data.ws_connections.max_capacity}`}
  />
)}
{data?.signal_engine && (
  <StatusRow
    name="訊號引擎"
    desc={`queue ${data.signal_engine.queue_depth}/5000 · lag ${data.signal_engine.lag_ms}ms · 今日 dropped ${data.signal_engine.dropped_today} · writer buf ${data.signal_engine.writer_buffer}`}
    status={data.signal_engine.degraded ? "error" : "ok"}
    customLabel={data.signal_engine.degraded ? "降級" : `${data.signal_engine.active_count} 規則`}
  />
)}
```

- [ ] **Step 3: vite build**

```powershell
cd C:\side-project\trading-king\frontend; npx vite build 2>&1 | Select-Object -Last 4
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/App.tsx frontend/src/pages/Health.tsx frontend/src/lib/api.ts
git commit -m "feat(phase3): App.tsx 啟用 watchlist + signals tab + Health 加 ws/engine row"
```

---

### Task 11.2: probe_e2e_signal 整合測試

**Files:**
- Create: `backend/scripts/probe_e2e_signal.py`

- [ ] **Step 1: 寫 probe**

```python
"""端到端：建一個寬鬆 active_signal → 加股票進 watchlist → 等 10 分鐘 → 看 signals_log。

需要在交易時段跑（盤中），盤後沒 tick 不會觸發。
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

from dotenv import load_dotenv

backend = Path(__file__).resolve().parent.parent
load_dotenv(backend / ".env")
sys.path.insert(0, str(backend))

from services.fubon_client import get_fubon
from services.fubon_ws import get_ws_pool
from services.signal_engine import get_signal_engine
from services.supabase_client import get_supabase
from services.supabase_writer import get_supabase_writer


async def main() -> None:
    fubon = get_fubon()
    await fubon.init()
    sb = get_supabase()
    sb.init()
    if fubon.status.value != "ok" or sb.status.value != "ok":
        print(f"FAIL fubon={fubon.status.value} sb={sb.status.value}")
        sys.exit(1)

    pool = get_ws_pool()
    await pool.start()
    writer = get_supabase_writer()
    await writer.start()
    engine = get_signal_engine()
    await engine.start()

    print("[setup] 加 2330 進 watchlist")
    sb.client.table("watchlist").upsert({"symbol": "2330"}).execute()
    await pool.subscribe("2330", owner_id="watchlist")

    print("[setup] 建一個寬鬆 active_signal: 1 分鐘漲 0.001%")
    res = sb.client.table("active_signals").insert({
        "name": "[probe-e2e] 寬鬆漲幅",
        "filter_json": {
            "schema_version": 1,
            "market": ["TWSE", "OTC"], "exclude_etf": True,
            "conditions": [],
            "window_conditions": [{
                "type": "price_change_pct", "window_seconds": 60,
                "operator": "gt", "value": 0.001,
            }],
            "logic": "AND", "limit": 200,
        },
        "scope": {"type": "watchlist"},
        "cooldown_seconds": 60, "ignore_auctions": True, "enabled": True,
    }).execute()
    asid = res.data[0]["id"]
    await pool.subscribe("2330", owner_id=asid)
    await engine.refresh_active_signals()

    print("[wait] 等 10 分鐘看 signals_log...")
    end = time.time() + 600
    seen_before = sb.client.table("signals_log").select("id", count="exact").eq("active_signal_id", asid).execute().count or 0
    while time.time() < end:
        await asyncio.sleep(30)
        cnt = sb.client.table("signals_log").select("id", count="exact").eq("active_signal_id", asid).execute().count or 0
        h = engine.health()
        print(f"  t+{int(end-time.time())}s | signals={cnt-seen_before} | queue={h['queue_depth']} | lag={h['lag_ms']}ms")
        if cnt - seen_before >= 1:
            print("  ✓ 收到至少 1 筆訊號")
            break

    print("[cleanup] 移除 probe 用的 active_signal + watchlist")
    sb.client.table("active_signals").delete().eq("id", asid).execute()
    sb.client.table("watchlist").delete().eq("symbol", "2330").execute()
    sb.client.table("signals_log").delete().eq("active_signal_id", asid).execute()

    await engine.shutdown()
    await writer.shutdown()
    await pool.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: 跑（盤中才有意義；盤後跑會等 10 分鐘無 signal 然後結束）**

```powershell
& "C:\side-project\trading-king\backend\.venv\Scripts\python.exe" "C:\side-project\trading-king\backend\scripts\probe_e2e_signal.py"
```

Expected: 盤中 — `✓ 收到至少 1 筆訊號`；盤後 — 看 queue_depth/lag 全 0 是預期的（沒 tick 進來）。

- [ ] **Step 3: Commit**

```bash
git add backend/scripts/probe_e2e_signal.py
git commit -m "test(phase3): probe_e2e_signal — 端到端寬鬆規則驗證"
```

---

### Task 11.3: Manual UAT checklist (盤中跑)

不寫 code，只列 checklist 給人在盤中手動驗。寫進 README 或 commit message。

- [ ] **Step 1: 在 spec 末尾或 README 加 UAT checklist**

```markdown
## Phase 3 UAT Checklist (盤中)

- [ ] Watchlist 頁加 2330 → 看到列表 + 即時報價更新
- [ ] 點 2330 → 右側分時走勢圖出現 (260+ 點)
- [ ] toggle CDP → 5 條水平線疊上去 (有 label AH/NH/CDP/NL/AL)
- [ ] toggle VWAP → 灰虛線消失/出現
- [ ] Signals 頁建一個「3% / 5 分鐘」watchlist 規則 → 30 分內收到至少 1 筆推播
- [ ] 雙分頁同時開 Signals → 兩邊都收到同一筆訊號
- [ ] 啟用兩條 active_signal 都監控 2330 → 取消其中一條 → 另一條仍正常觸發
- [ ] 手動 disconnect 網路 30 秒 → 自動重連 + 訂閱還原
- [ ] Health 頁 ws_connections + signal_engine 數字會動
```

- [ ] **Step 2: Memory + final commit**

```bash
git add docs/superpowers/specs/2026-05-12-phase-3-realtime-design.md  # 如有 UAT checklist 補進去
git commit -m "docs(phase3): UAT checklist + manual verification list" --allow-empty
```

也記得 update memory：

```python
# 寫到 ~/.claude/projects/C--side-project-trading-king/memory/project_phase_3_state.md
```

```markdown
---
name: trading-king Phase 3 完成
description: 2026-05-XX Phase 3 即時 WS + Watchlist 內嵌分時走勢 + CDP 完成
type: project
---
（內容對齊 phase 2a/2b state 風格，列出實作清單 + 踩雷紀錄 + UAT 結果）
```

並更新 `MEMORY.md` 加一行。

---

## Self-Review Checklist (執行前 reviewer 用)

把 spec 跟 plan 並排檢查：

- ✅ Spec §3 Architecture → Plan TG 1-7 全 cover (新建 5 services + 6 routes + migration + main.py)
- ✅ Spec §4.1 fubon_ws → Plan TG 3 (Task 3.1, 3.2, 3.3)
- ✅ Spec §4.2 ring_buffer → Plan TG 2
- ✅ Spec §4.3 signal_engine → Plan TG 5
- ✅ Spec §4.4 supabase_writer → Plan TG 6
- ✅ Spec §4.5 cdp.py → Plan TG 4
- ✅ Spec §4.6 routes → Plan TG 7 (7.1 - 7.7)
- ✅ Spec §5 Data Flows → Plan 內各 service / route 實作 cover
- ✅ Spec §6 UI → Plan TG 8-10 + 11.1
- ✅ Spec §7 Error Handling → 散落在各 service 實作（reconnect / circuit / backpressure / retry）
- ✅ Spec §8 DB Schema → Plan TG 1.1
- ✅ Spec §9 Pydantic Models → Plan TG 1.2
- ✅ Spec §10 Test Strategy → 各 service smoke + TG 11.2 (probe_e2e_signal) + TG 11.3 (manual UAT)
- ✅ Spec §13 Out of Scope → Plan 內**沒**實作 Discord (符合)

**Type 一致性**：
- `Tick` (dataclass) — ring_buffer.py 定義，fubon_ws.py / signal_engine.py / probe_*.py import
- `WSPool.subscribe(symbol, owner_id)` 簽章一致 in all callers
- `get_signal_engine()` / `get_ws_pool()` / `get_ring_buffer()` / `get_cdp_service()` / `get_supabase_writer()` / `get_broadcaster()` 命名一致
- `ActiveSignalOut` Pydantic 在 condition.py 定義，signal_engine + routes/active_signals + probe_signal_engine import
- Frontend `ActiveSignal` interface 跟 backend `ActiveSignalOut` 對齊（id, name, filter_json, scope, cooldown_seconds, ignore_auctions, enabled, created_at）

**Placeholder scan**: 全 plan grep "TBD" / "TODO" / "implement later" → 無

**Scope check**: 11 task groups × 平均 3 tasks = ~33 tasks，4.5 天工時，single subsystem (Phase 3 即時) → 範圍適中可一次 plan 處理

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-12-phase-3-realtime.md`. Two execution options:

**1. Subagent-Driven (recommended)** — 我每個 task dispatch 一個 fresh subagent，task 之間我 review，回饋快、context 不堆積

**2. Inline Execution** — 直接在這個 session 跑，每個 task group 完跟你 check 一次

哪個？

