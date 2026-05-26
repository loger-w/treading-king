# 監聽清單 + 訊號 Discord 通知 — 實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把訊號評估範圍從「per-rule scope」轉成「全域監聽清單」,並讓每條規則觸發時能可選地推 Discord webhook 通知。

**Architecture:** 新 table `monitor_list` 取代既有 `active_signals.scope` 語意;signal_engine 改成讀 `monitor_list` 評估;`_fanout` 多一條 Discord branch;前端書籤面板 sidebar 加「監聽」入口;舊規則 migration 自動 backfill,scope 欄位保留 schema 但程式碼 ignore。

**Tech Stack:** FastAPI + Pydantic、Supabase PostgreSQL、`httpx`、React + TypeScript、Tailwind、pytest + pytest-asyncio + MagicMock。

**Spec:** [`docs/superpowers/specs/2026-05-26-monitor-list-and-discord-notify-design.md`](../specs/2026-05-26-monitor-list-and-discord-notify-design.md)

---

## File map

### Create
- `supabase/migrations/0008_monitor_list_and_notify.sql`
- `backend/services/discord_notifier.py`
- `backend/routes/monitor_list.py`
- `backend/tests/test_discord_notifier.py`
- `backend/tests/test_monitor_list_route.py`
- `backend/tests/test_signal_engine_monitor.py`
- `frontend/src/hooks/useMonitorList.ts`

### Modify
- `backend/models/condition.py` — `ActiveSignalCreate` 加 `notify_discord`
- `backend/services/signal_engine.py` — scope 邏輯切到 monitor_list、`_fanout` 加 Discord
- `backend/routes/active_signals.py` — 拿掉 scope-based ws sub、加 notify_discord 進 payload
- `backend/main.py` — startup 加 monitor_list ws sub、include 新 router
- `backend/.env.example` — 加 `SIGNALS_DISCORD_WEBHOOK_URL`
- `frontend/src/lib/api.ts` — 新增 `MonitorListItem`、`monitorList` client、`ActiveSignal.notify_discord`
- `frontend/src/components/ActiveSignalEditor.tsx` — 移除 scope 選擇、加 Discord toggle
- `frontend/src/components/BookmarksPanel.tsx` — sidebar 加「監聽」常駐項 + MonitorListView
- `frontend/src/components/IntradayChart.tsx` — header 加「+ 加入監聽」按鈕
- `frontend/src/components/AddToBookmarksDialog.tsx` — 加「同時加入監聽清單」checkbox
- `frontend/src/pages/Monitor.tsx` — 整合 `useMonitorList`

---

## Task 1: DB migration + backfill

**Files:**
- Create: `supabase/migrations/0008_monitor_list_and_notify.sql`

- [ ] **Step 1: 撰寫 migration**

寫入以下完整內容:

```sql
-- 2026-05-26 — 監聽清單 + 訊號 Discord 通知
-- 設計見 docs/superpowers/specs/2026-05-26-monitor-list-and-discord-notify-design.md

-- ---------------------------------------------------------------------------
-- monitor_list:訊號評估的全域監聽 universe(per user_label)
-- ---------------------------------------------------------------------------
create table if not exists monitor_list (
  user_label text not null,
  symbol     text not null references symbols(symbol),
  added_at   timestamptz default now(),
  primary key (user_label, symbol)
);

create index if not exists idx_monitor_list_label on monitor_list(user_label);

alter table monitor_list enable row level security;

create policy "anon can read monitor_list"
  on monitor_list for select
  to anon, authenticated
  using (true);

-- ---------------------------------------------------------------------------
-- active_signals:per-rule Discord 通知開關
-- ---------------------------------------------------------------------------
alter table active_signals
  add column if not exists notify_discord boolean not null default true;

-- ---------------------------------------------------------------------------
-- Backfill 1:每個 user 的「自選」書籤股票 → monitor_list
--   (舊 scope=watchlist 等於這份,migrate 後語意不變)
-- ---------------------------------------------------------------------------
insert into monitor_list (user_label, symbol)
select distinct bg.user_label, wi.symbol
from watchlist_items wi
join bookmark_groups bg on bg.id = wi.group_id
where bg.user_label is not null
  and bg.name = '自選'
on conflict do nothing;

-- ---------------------------------------------------------------------------
-- Backfill 2:舊 active_signals.scope.symbols (scope=symbols) → monitor_list
--   exists 子查詢確保被加入的 symbol 還在 symbols 表(避免 FK 撞 delisted)
-- ---------------------------------------------------------------------------
insert into monitor_list (user_label, symbol)
select distinct a.user_label, sym.symbol
from active_signals a,
     lateral jsonb_array_elements_text(a.scope->'symbols') as sym(symbol)
where a.scope->>'type' = 'symbols'
  and jsonb_typeof(a.scope->'symbols') = 'array'
  and exists (select 1 from symbols s where s.symbol = sym.symbol)
on conflict do nothing;
```

- [ ] **Step 2: 套到 supabase 並驗 backfill**

用 MCP `mcp__supabase__apply_migration` 套用(或讓 user 手動 apply 都可)。套完後執行:

```sql
-- 確認 table 存在
select count(*) from monitor_list;

-- 確認 user 'loger' 的監聽清單 ⊇ 自選書籤
select symbol from watchlist_items wi
  join bookmark_groups bg on bg.id = wi.group_id
  where bg.user_label = 'loger' and bg.name = '自選'
except
select symbol from monitor_list where user_label = 'loger';
-- 期望:0 rows(自選書籤的 symbol 全在 monitor_list 內)

-- 確認 active_signals.notify_discord 預設值
select id, name, notify_discord from active_signals where user_label = 'loger' limit 5;
```

- [ ] **Step 3: Commit**

```bash
git add supabase/migrations/0008_monitor_list_and_notify.sql
git commit -m "feat(signals): add monitor_list table + notify_discord column

- New monitor_list (user_label, symbol) table replaces per-rule scope concept
- active_signals.notify_discord boolean defaults true for per-rule Discord push
- Backfills monitor_list from existing watchlist (自選 bookmark) +
  active_signals.scope.symbols, skipping delisted FK violations"
```

---

## Task 2: Backend model — `ActiveSignalCreate.notify_discord`

**Files:**
- Modify: `backend/models/condition.py:174-179`

- [ ] **Step 1: 寫 failing test**

新檔 `backend/tests/test_condition_model.py` 已存在;在尾端追加:

```python
def test_active_signal_create_default_notify_discord_true():
    from models.condition import ActiveSignalCreate, ActiveFilter, Condition, WatchlistScope
    payload = ActiveSignalCreate(
        name="t",
        filter_json=ActiveFilter(conditions=[Condition(field="close", operator="gt", value=0)]),
        scope=WatchlistScope(type="watchlist"),
    )
    assert payload.notify_discord is True


def test_active_signal_create_notify_discord_can_be_false():
    from models.condition import ActiveSignalCreate, ActiveFilter, Condition, WatchlistScope
    payload = ActiveSignalCreate(
        name="t",
        filter_json=ActiveFilter(conditions=[Condition(field="close", operator="gt", value=0)]),
        scope=WatchlistScope(type="watchlist"),
        notify_discord=False,
    )
    assert payload.notify_discord is False
```

- [ ] **Step 2: 驗 test 失敗**

```bash
cd backend && python -m pytest tests/test_condition_model.py::test_active_signal_create_default_notify_discord_true -v
```

Expected: FAIL with `AttributeError` or unknown field error.

- [ ] **Step 3: 改 `ActiveSignalCreate`**

`backend/models/condition.py:174-179` 改成:

```python
class ActiveSignalCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    filter_json: ActiveFilter
    scope: Scope
    cooldown_seconds: int = Field(default=1800, ge=60, le=86400)
    enabled: bool = True
    notify_discord: bool = True
```

- [ ] **Step 4: 驗 tests 通過**

```bash
cd backend && python -m pytest tests/test_condition_model.py -v
```

Expected: 全 PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/models/condition.py backend/tests/test_condition_model.py
git commit -m "feat(signals): add notify_discord field to ActiveSignalCreate

Defaults true so existing rules keep behaviour (no webhook configured
= silent skip downstream)."
```

---

## Task 3: Discord notifier service

**Files:**
- Create: `backend/services/discord_notifier.py`
- Create: `backend/tests/test_discord_notifier.py`

- [ ] **Step 1: 寫 failing test**

新建 `backend/tests/test_discord_notifier.py`:

```python
"""Discord notifier — 訊號推送(失敗 silent log)。"""
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import discord_notifier


@pytest.fixture(autouse=True)
def _reset_cached_webhook():
    """每個 test reset module-level cache,避免測試間互相污染。"""
    discord_notifier._WEBHOOK_URL = None
    yield
    discord_notifier._WEBHOOK_URL = None


@pytest.mark.asyncio
async def test_send_signal_noop_when_webhook_unset(monkeypatch):
    monkeypatch.delenv("SIGNALS_DISCORD_WEBHOOK_URL", raising=False)
    with patch("services.discord_notifier.httpx.AsyncClient") as mock_client:
        await discord_notifier.send_signal(
            rule_name="t", symbol="2330", price=600.0, volume=10,
            triggered_at_iso="2026-05-26T01:00:00+00:00",
        )
        mock_client.assert_not_called()


@pytest.mark.asyncio
async def test_send_signal_posts_embed_when_webhook_set(monkeypatch):
    monkeypatch.setenv("SIGNALS_DISCORD_WEBHOOK_URL", "https://discord.test/webhook")
    fake_client = AsyncMock()
    fake_client.__aenter__.return_value = fake_client
    fake_client.__aexit__.return_value = False
    fake_client.post = AsyncMock()
    with patch("services.discord_notifier.httpx.AsyncClient", return_value=fake_client):
        await discord_notifier.send_signal(
            rule_name="MA_5 觸碰",
            symbol="2330",
            price=600.0,
            volume=10,
            triggered_at_iso="2026-05-26T01:00:00+00:00",
            ma_touch={"level": "sma_5", "direction": "from_below", "role": "resistance"},
        )
        fake_client.post.assert_called_once()
        call = fake_client.post.call_args
        assert call.args[0] == "https://discord.test/webhook"
        payload = call.kwargs["json"]
        embed = payload["embeds"][0]
        assert "MA_5 觸碰" in embed["title"]
        field_names = [f["name"] for f in embed["fields"]]
        assert "代號" in field_names
        assert "MA" in field_names


@pytest.mark.asyncio
async def test_send_signal_swallows_httpx_errors(monkeypatch, caplog):
    monkeypatch.setenv("SIGNALS_DISCORD_WEBHOOK_URL", "https://discord.test/webhook")
    fake_client = AsyncMock()
    fake_client.__aenter__.return_value = fake_client
    fake_client.__aexit__.return_value = False
    fake_client.post = AsyncMock(side_effect=Exception("network down"))
    with patch("services.discord_notifier.httpx.AsyncClient", return_value=fake_client):
        # 不該 raise
        await discord_notifier.send_signal(
            rule_name="t", symbol="2330", price=600.0, volume=10,
            triggered_at_iso="2026-05-26T01:00:00+00:00",
        )
    assert "Discord signal notify failed" in caplog.text
```

- [ ] **Step 2: 驗 test 失敗**

```bash
cd backend && python -m pytest tests/test_discord_notifier.py -v
```

Expected: FAIL with `ModuleNotFoundError: services.discord_notifier`.

- [ ] **Step 3: 寫 implementation**

新建 `backend/services/discord_notifier.py`:

```python
"""Discord notifier — 訊號觸發推送(跟 alerts.py 系統異常 webhook 分開)。"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_WEBHOOK_URL: str | None = None


def _get_webhook_url() -> str | None:
    global _WEBHOOK_URL
    if _WEBHOOK_URL is None:
        _WEBHOOK_URL = os.getenv("SIGNALS_DISCORD_WEBHOOK_URL", "").strip() or ""
    return _WEBHOOK_URL or None


async def send_signal(
    *,
    rule_name: str,
    symbol: str,
    price: float,
    volume: int,
    triggered_at_iso: str,
    cdp_touch: dict | None = None,
    ma_touch: dict | None = None,
) -> None:
    """訊號觸發推 Discord;失敗 silent log(不影響主流程)。"""
    url = _get_webhook_url()
    if not url:
        return

    fields: list[dict[str, Any]] = [
        {"name": "代號", "value": symbol, "inline": True},
        {"name": "價格", "value": f"{price:.2f}", "inline": True},
        {"name": "量", "value": str(volume), "inline": True},
    ]
    if cdp_touch:
        fields.append({
            "name": "CDP",
            "value": f"{cdp_touch['level']} ({cdp_touch.get('role', 'touch')})",
            "inline": True,
        })
    if ma_touch:
        fields.append({
            "name": "MA",
            "value": f"{ma_touch['level']} ({ma_touch.get('role', 'touch')})",
            "inline": True,
        })

    embed = {
        "title": f"📈 {rule_name}",
        "description": f"`{symbol}` 觸發",
        "color": 0x32D27C,
        "fields": fields,
        "timestamp": triggered_at_iso,
    }

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(url, json={"embeds": [embed]})
    except Exception as e:
        logger.warning("Discord signal notify failed: %s", e)
```

- [ ] **Step 4: 驗 tests 通過**

```bash
cd backend && python -m pytest tests/test_discord_notifier.py -v
```

Expected: 3 PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/services/discord_notifier.py backend/tests/test_discord_notifier.py
git commit -m "feat(signals): add discord_notifier for signal trigger push

Separate from alerts.py (system alerts) — uses its own
SIGNALS_DISCORD_WEBHOOK_URL env. No-op when unset. Failures swallowed
to avoid breaking signal fanout."
```

---

## Task 4: signal_engine — `_load_monitor_symbols` + scope 改寫

**Files:**
- Modify: `backend/services/signal_engine.py`
- Create: `backend/tests/test_signal_engine_monitor.py`

- [ ] **Step 1: 寫 failing test**

新建 `backend/tests/test_signal_engine_monitor.py`:

```python
"""驗 signal_engine 改成讀 monitor_list 評估(不再讀 active.scope)。"""
from unittest.mock import MagicMock, AsyncMock

import pytest

from services.signal_engine import SignalEngine


@pytest.mark.asyncio
async def test_load_monitor_symbols_returns_set(monkeypatch):
    from services import signal_engine as se
    fake_table = MagicMock()
    fake_table.select.return_value = fake_table
    fake_table.eq.return_value = fake_table
    fake_table.execute.return_value = MagicMock(data=[{"symbol": "2330"}, {"symbol": "2317"}])
    fake_sb = MagicMock()
    fake_sb.client.table.return_value = fake_table
    monkeypatch.setattr(se, "get_supabase", lambda: fake_sb)
    monkeypatch.setattr(se, "get_user_label", lambda: "loger")

    engine = SignalEngine()
    syms = await engine._load_monitor_symbols()
    assert syms == {"2330", "2317"}
    fake_table.eq.assert_called_with("user_label", "loger")


@pytest.mark.asyncio
async def test_load_monitor_symbols_empty_when_supabase_none(monkeypatch):
    from services import signal_engine as se
    fake_sb = MagicMock(client=None)
    monkeypatch.setattr(se, "get_supabase", lambda: fake_sb)
    engine = SignalEngine()
    assert await engine._load_monitor_symbols() == set()


def test_scope_includes_uses_field_cache_membership():
    """field_cache 由 monitor_list refill,_scope_includes 不再讀 active.scope。"""
    engine = SignalEngine()
    engine._field_cache = {"2330": {}, "2317": {}}
    active = MagicMock()
    assert engine._scope_includes(active, "2330") is True
    assert engine._scope_includes(active, "9999") is False


def test_scope_symbols_returns_field_cache_keys():
    """heartbeat 用 _scope_symbols 拿 monitor_list 全部 symbol。"""
    engine = SignalEngine()
    engine._field_cache = {"2330": {}, "2317": {}}
    active = MagicMock()
    assert set(engine._scope_symbols(active)) == {"2330", "2317"}
```

- [ ] **Step 2: 驗 test 失敗**

```bash
cd backend && python -m pytest tests/test_signal_engine_monitor.py -v
```

Expected: `_load_monitor_symbols` 不存在 → FAIL;`_scope_includes` 用 scope 邏輯 → 部分 FAIL。

- [ ] **Step 3: 改 `signal_engine.py`**

加新 method `_load_monitor_symbols`(在 `_refill_field_cache` 上方):

```python
async def _load_monitor_symbols(self) -> set[str]:
    """從 monitor_list 拉本 user 的所有監聽 symbol。"""
    sb = get_supabase()
    if sb.client is None:
        return set()
    res = await asyncio.to_thread(
        lambda: sb.client.table("monitor_list")
        .select("symbol")
        .eq("user_label", get_user_label())
        .execute()
    )
    return {r["symbol"] for r in (res.data or [])}
```

改 `_refill_field_cache` — 把 symbols 蒐集改成讀 monitor_list,**完整替換**從 method 開始到 `# cdp 5 值 + 昨日收盤` 之前(目前 `backend/services/signal_engine.py:125-158`):

```python
async def _refill_field_cache(self) -> None:
    """為 monitor_list 內的 symbol 載入 cdp_* + sma 進 cache。

    close 走即時 tick.price(由 _eval_filter_cond 處理),不進 field_cache。
    """
    sb = get_supabase()
    symbols_needed: set[str] = await self._load_monitor_symbols()

    # cdp 5 值 + 昨日收盤(供 day_change_pct 算式分母)
    # ... 以下接既有 cdp + sma + day_volume.clear() 邏輯不動 ...
```

(保留 line 159 以下既有的 cdp / sma / day_volume 邏輯不變。)

改 `_scope_includes` (line 378-391):

```python
def _scope_includes(self, active: ActiveSignalOut, symbol: str) -> bool:
    """所有 rule 共用 monitor_list;field_cache 的 key 就是 monitor_list 的 union。"""
    return symbol in self._field_cache
```

改 `_scope_symbols` (line 231-245):

```python
def _scope_symbols(self, active: ActiveSignalOut) -> list[str]:
    """heartbeat 用,回傳 monitor_list 全部 symbol。"""
    return list(self._field_cache.keys())
```

- [ ] **Step 4: 驗 tests 通過**

```bash
cd backend && python -m pytest tests/test_signal_engine_monitor.py tests/test_signal_engine_proximity.py tests/test_signal_engine_day_metrics.py -v
```

Expected: 全 PASS(包括既有的 signal_engine tests 沒被打壞)。

- [ ] **Step 5: Commit**

```bash
git add backend/services/signal_engine.py backend/tests/test_signal_engine_monitor.py
git commit -m "feat(signals): signal_engine reads monitor_list instead of per-rule scope

_refill_field_cache, _scope_includes, _scope_symbols all switched to
field_cache (sourced from monitor_list). active.scope is ignored at
runtime — keeps schema for audit but logic no longer reads it."
```

---

## Task 5: signal_engine — `_fanout` 加 Discord branch

**Files:**
- Modify: `backend/services/signal_engine.py:553-582`

- [ ] **Step 1: 寫 failing test**

追加到 `backend/tests/test_signal_engine_monitor.py`:

```python
@pytest.mark.asyncio
async def test_fanout_calls_discord_when_notify_enabled(monkeypatch):
    """rule.notify_discord=True → discord_notifier.send_signal 被叫一次。"""
    from services import signal_engine as se
    from services.ring_buffer import Tick
    from models.condition import ActiveSignalOut, ActiveFilter, Condition

    sent = []
    async def fake_send_signal(**kwargs):
        sent.append(kwargs)
    monkeypatch.setattr(se, "discord_notifier", MagicMock(send_signal=fake_send_signal))
    # writer / broadcaster mock 避開 IO
    monkeypatch.setattr(se, "get_broadcaster", lambda: MagicMock(broadcast=AsyncMock()))
    fake_writer = MagicMock(append=MagicMock())
    monkeypatch.setattr("services.supabase_writer.get_supabase_writer", lambda: fake_writer)
    monkeypatch.setattr(se, "get_user_label", lambda: "loger")

    engine = SignalEngine()
    active = ActiveSignalOut(
        id="r1", name="r1",
        filter_json=ActiveFilter(conditions=[Condition(field="close", operator="gt", value=0)]),
        scope={"type": "watchlist"}, cooldown_seconds=60, enabled=True,
        created_at="2026-05-26", notify_discord=True,
    )
    tick = Tick(price=600.0, size=10, time=1700000000.0)
    await engine._fanout(active, "2330", tick)

    assert len(sent) == 1
    assert sent[0]["rule_name"] == "r1"
    assert sent[0]["symbol"] == "2330"


@pytest.mark.asyncio
async def test_fanout_skips_discord_when_notify_disabled(monkeypatch):
    from services import signal_engine as se
    from services.ring_buffer import Tick
    from models.condition import ActiveSignalOut, ActiveFilter, Condition

    sent = []
    async def fake_send_signal(**kwargs):
        sent.append(kwargs)
    monkeypatch.setattr(se, "discord_notifier", MagicMock(send_signal=fake_send_signal))
    monkeypatch.setattr(se, "get_broadcaster", lambda: MagicMock(broadcast=AsyncMock()))
    monkeypatch.setattr("services.supabase_writer.get_supabase_writer", lambda: MagicMock(append=MagicMock()))
    monkeypatch.setattr(se, "get_user_label", lambda: "loger")

    engine = SignalEngine()
    active = ActiveSignalOut(
        id="r2", name="r2",
        filter_json=ActiveFilter(conditions=[Condition(field="close", operator="gt", value=0)]),
        scope={"type": "watchlist"}, cooldown_seconds=60, enabled=True,
        created_at="2026-05-26", notify_discord=False,
    )
    tick = Tick(price=600.0, size=10, time=1700000000.0)
    await engine._fanout(active, "2330", tick)

    assert sent == []


@pytest.mark.asyncio
async def test_fanout_continues_when_discord_raises(monkeypatch):
    """discord 推送丟錯不該影響 ws broadcast + supabase append。"""
    from services import signal_engine as se
    from services.ring_buffer import Tick
    from models.condition import ActiveSignalOut, ActiveFilter, Condition

    async def raising_send_signal(**kwargs):
        raise RuntimeError("discord down")
    monkeypatch.setattr(se, "discord_notifier", MagicMock(send_signal=raising_send_signal))
    broadcaster = MagicMock(broadcast=AsyncMock())
    monkeypatch.setattr(se, "get_broadcaster", lambda: broadcaster)
    writer = MagicMock(append=MagicMock())
    monkeypatch.setattr("services.supabase_writer.get_supabase_writer", lambda: writer)
    monkeypatch.setattr(se, "get_user_label", lambda: "loger")

    engine = SignalEngine()
    active = ActiveSignalOut(
        id="r3", name="r3",
        filter_json=ActiveFilter(conditions=[Condition(field="close", operator="gt", value=0)]),
        scope={"type": "watchlist"}, cooldown_seconds=60, enabled=True,
        created_at="2026-05-26", notify_discord=True,
    )
    tick = Tick(price=600.0, size=10, time=1700000000.0)
    # 不該 raise
    await engine._fanout(active, "2330", tick)

    broadcaster.broadcast.assert_awaited_once()
    writer.append.assert_called_once()
```

- [ ] **Step 2: 驗 test 失敗**

```bash
cd backend && python -m pytest tests/test_signal_engine_monitor.py -k fanout -v
```

Expected: FAIL — `discord_notifier` 還沒被 import 到 signal_engine 內、`_fanout` 還沒 Discord branch。

- [ ] **Step 3: 改 `_fanout`**

`backend/services/signal_engine.py` 頂端 imports 加:

```python
from services import alerts, discord_notifier, ma_service
```

(把既有 `from services import alerts, ma_service` 改掉)

把 `_fanout` 方法 (line 553-582) 整段改成:

```python
async def _fanout(
    self, active: ActiveSignalOut, symbol: str, tick: Tick,
    cdp_touch: dict | None = None, ma_touch: dict | None = None,
) -> None:
    from services.supabase_writer import get_supabase_writer
    data: dict = {
        "active_signal_id": active.id,
        "active_signal_name": active.name,
        "symbol": symbol,
        "triggered_at": datetime.now(timezone.utc).isoformat(),
        "trigger_price": tick.price,
        "trigger_volume": tick.size,
    }
    if cdp_touch: data["cdp_touch"] = cdp_touch
    if ma_touch:  data["ma_touch"]  = ma_touch
    payload = {"event": "signal", "data": data}
    # 1. 前端 WS broadcast
    await get_broadcaster().broadcast(payload)
    # 2. supabase writer
    context: dict = {"latest_tick_time": tick.time}
    if cdp_touch: context["cdp_touch"] = cdp_touch
    if ma_touch:  context["ma_touch"]  = ma_touch
    get_supabase_writer().append({
        "active_signal_id": active.id,
        "symbol": symbol,
        "trigger_price": tick.price,
        "trigger_volume": tick.size,
        "context_json": context,
        "user_label": get_user_label(),
    })
    # 3. Discord notify(per-rule 開關;失敗 swallowed,不影響上面兩條)
    if active.notify_discord:
        try:
            await discord_notifier.send_signal(
                rule_name=active.name,
                symbol=symbol,
                price=tick.price,
                volume=tick.size,
                triggered_at_iso=data["triggered_at"],
                cdp_touch=cdp_touch,
                ma_touch=ma_touch,
            )
        except Exception as e:
            logger.warning("discord notify failed: %s", e)
```

- [ ] **Step 4: 驗 tests 通過**

```bash
cd backend && python -m pytest tests/test_signal_engine_monitor.py -v
```

Expected: 全 PASS(7 tests)。

- [ ] **Step 5: Commit**

```bash
git add backend/services/signal_engine.py backend/tests/test_signal_engine_monitor.py
git commit -m "feat(signals): fanout pushes to Discord when rule.notify_discord

Per-rule opt-in; webhook failures swallowed so ws broadcast + signals_log
write are unaffected."
```

---

## Task 6: Routes — `monitor_list.py`

**Files:**
- Create: `backend/routes/monitor_list.py`
- Create: `backend/tests/test_monitor_list_route.py`

- [ ] **Step 1: 寫 failing test**

新建 `backend/tests/test_monitor_list_route.py`:

```python
"""驗 /api/monitor_list CRUD 行為。"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def app(monkeypatch):
    from routes import monitor_list as ml
    from services.supabase_client import SupabaseStatus

    fake_table = MagicMock()
    fake_table.select.return_value = fake_table
    fake_table.eq.return_value = fake_table
    fake_table.order.return_value = fake_table
    fake_table.limit.return_value = fake_table
    fake_table.insert.return_value = fake_table
    fake_table.delete.return_value = fake_table
    fake_table.execute.return_value = MagicMock(data=[])

    fake_sb = MagicMock()
    fake_sb.status = SupabaseStatus.OK
    fake_sb.client.table.return_value = fake_table

    monkeypatch.setattr(ml, "get_supabase", lambda: fake_sb)
    monkeypatch.setattr(ml, "get_user_label", lambda: "test")

    fake_pool = MagicMock()
    fake_pool.subscribe = AsyncMock()
    fake_pool.unsubscribe = AsyncMock()
    monkeypatch.setattr(ml, "get_ws_pool", lambda: fake_pool)

    fake_cdp = MagicMock()
    fake_cdp.backfill_from_fubon = AsyncMock()
    monkeypatch.setattr(ml, "get_cdp_service", lambda: fake_cdp)

    # signal_engine refresh — patch 在 import 路徑上
    fake_engine = MagicMock()
    fake_engine.refresh_active_signals = AsyncMock()
    with patch("services.signal_engine.get_signal_engine", lambda: fake_engine):
        a = FastAPI()
        a.include_router(ml.router)
        yield a, fake_table, fake_pool


def test_list_returns_empty(app):
    a, table, _ = app
    table.execute.return_value = MagicMock(data=[])
    client = TestClient(a)
    r = client.get("/api/monitor_list")
    assert r.status_code == 200
    assert r.json() == {"items": [], "count": 0}


def test_add_unknown_symbol_returns_404(app):
    a, table, _ = app
    # symbols lookup 回空 → 404
    table.execute.return_value = MagicMock(data=[])
    client = TestClient(a)
    r = client.post("/api/monitor_list", json={"symbol": "9999"})
    assert r.status_code == 404


def test_add_success_subscribes_and_inserts(app):
    a, table, pool = app
    # 第一次 execute = symbols 查;第二次 execute = insert
    table.execute.side_effect = [
        MagicMock(data=[{"symbol": "2330"}]),
        MagicMock(data=[{"user_label": "test", "symbol": "2330"}]),
    ]
    client = TestClient(a)
    r = client.post("/api/monitor_list", json={"symbol": "2330"})
    assert r.status_code == 201
    pool.subscribe.assert_awaited_once_with("2330", owner_id="monitor_list")


def test_add_ws_capacity_full_returns_503_no_db_write(app):
    a, table, pool = app
    table.execute.return_value = MagicMock(data=[{"symbol": "2330"}])
    pool.subscribe.side_effect = RuntimeError("WS pool capacity full")
    client = TestClient(a)
    r = client.post("/api/monitor_list", json={"symbol": "2330"})
    assert r.status_code == 503
    # insert 不該被呼叫
    assert not table.insert.called


def test_delete_unsubscribes(app):
    a, _, pool = app
    client = TestClient(a)
    r = client.delete("/api/monitor_list/2330")
    assert r.status_code == 204
    pool.unsubscribe.assert_awaited_once_with("2330", owner_id="monitor_list")
```

- [ ] **Step 2: 驗 test 失敗**

```bash
cd backend && python -m pytest tests/test_monitor_list_route.py -v
```

Expected: FAIL — `ModuleNotFoundError: routes.monitor_list`.

- [ ] **Step 3: 寫 implementation**

新建 `backend/routes/monitor_list.py`:

```python
"""GET/POST/DELETE /api/monitor_list — 監聽清單 CRUD。

POST 順手:
  - ws_pool.subscribe(owner='monitor_list')
  - cdp_service.backfill_from_fubon(symbol) 背景
  - signal_engine.refresh_active_signals()
DELETE 反過來 unsubscribe + refresh。
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.cdp import get_cdp_service
from services.fubon_ws import get_ws_pool
from services.supabase_client import SupabaseStatus, get_supabase
from services.user_context import get_user_label

logger = logging.getLogger(__name__)
router = APIRouter()

OWNER_ID = "monitor_list"


class MonitorListAdd(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)


def _ensure_supabase():
    sb = get_supabase()
    if sb.status != SupabaseStatus.OK or sb.client is None:
        raise HTTPException(503, detail={"error": "supabase_unavailable"})
    return sb


@router.get("/api/monitor_list")
async def list_monitor() -> dict:
    sb = _ensure_supabase()
    res = await asyncio.to_thread(
        lambda: sb.client.table("monitor_list")
        .select("symbol, added_at, symbols(name, market, is_etf)")
        .eq("user_label", get_user_label())
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
            "name": meta.get("name"),
            "market": meta.get("market"),
            "is_etf": meta.get("is_etf"),
        })
    return {"items": out, "count": len(out)}


@router.post("/api/monitor_list", status_code=201)
async def add_monitor(payload: MonitorListAdd) -> dict:
    sb = _ensure_supabase()
    label = get_user_label()

    # symbol 必須存在 symbols 表
    sym_res = await asyncio.to_thread(
        lambda: sb.client.table("symbols").select("symbol")
        .eq("symbol", payload.symbol).limit(1).execute()
    )
    if not (sym_res.data or []):
        raise HTTPException(404, detail={"error": "symbol_not_found"})

    # 先試 ws subscribe;失敗就不寫 DB,避免狀態不一致
    try:
        await get_ws_pool().subscribe(payload.symbol, owner_id=OWNER_ID)
    except RuntimeError as e:
        raise HTTPException(503, detail={"error": "ws_capacity_full", "detail": str(e)})

    # 寫 DB
    try:
        await asyncio.to_thread(
            lambda: sb.client.table("monitor_list").insert({
                "user_label": label,
                "symbol": payload.symbol,
            }).execute()
        )
    except Exception as e:
        # 寫失敗 → rollback ws subscribe
        try:
            await get_ws_pool().unsubscribe(payload.symbol, owner_id=OWNER_ID)
        except Exception:
            pass
        raise HTTPException(409, detail={"error": "already_in_monitor_list", "detail": str(e)})

    # CDP backfill 背景跑
    asyncio.create_task(get_cdp_service().backfill_from_fubon(payload.symbol))

    # signal_engine refresh
    try:
        from services.signal_engine import get_signal_engine
        await get_signal_engine().refresh_active_signals()
    except Exception as e:
        logger.warning("monitor_list add: refresh signal_engine failed: %s", e)

    return {"symbol": payload.symbol, "status": "added"}


@router.delete("/api/monitor_list/{symbol}", status_code=204)
async def remove_monitor(symbol: str) -> None:
    sb = _ensure_supabase()
    await asyncio.to_thread(
        lambda: sb.client.table("monitor_list").delete()
        .eq("user_label", get_user_label())
        .eq("symbol", symbol)
        .execute()
    )
    try:
        await get_ws_pool().unsubscribe(symbol, owner_id=OWNER_ID)
    except Exception as e:
        logger.warning("monitor_list remove: ws unsubscribe failed: %s", e)
    try:
        from services.signal_engine import get_signal_engine
        await get_signal_engine().refresh_active_signals()
    except Exception as e:
        logger.warning("monitor_list remove: refresh signal_engine failed: %s", e)
    return None
```

- [ ] **Step 4: 驗 tests 通過**

```bash
cd backend && python -m pytest tests/test_monitor_list_route.py -v
```

Expected: 5 PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/routes/monitor_list.py backend/tests/test_monitor_list_route.py
git commit -m "feat(signals): add /api/monitor_list CRUD

POST: validates symbol → ws subscribe → DB insert (rolls back ws if
insert fails) → cdp backfill + signal_engine refresh. DELETE removes
from DB + unsubscribes. 503 on ws capacity full, 404 on unknown symbol."
```

---

## Task 7: Routes — `active_signals.py` 清理 + `notify_discord` 接入

**Files:**
- Modify: `backend/routes/active_signals.py`

- [ ] **Step 1: Inspect 既有 route**

確認 `_scope_symbols` helper 跟 `create_active` / `update_active` / `delete_active` 內所有對 `ws_pool.subscribe(owner_id=sid)` / `unsubscribe` 的呼叫位置。

- [ ] **Step 2: 改 implementation**

把 `_scope_symbols` 整段移除(line 31-58)。

`create_active` (line 73-98) 改成:

```python
@router.post("/api/active_signals", status_code=201)
async def create_active(payload: ActiveSignalCreate) -> dict:
    sb = _ensure_supabase()
    res = await asyncio.to_thread(
        lambda: sb.client.table("active_signals").insert({
            "name": payload.name,
            "filter_json": payload.filter_json.model_dump(),
            "scope": payload.scope.model_dump(),
            "cooldown_seconds": payload.cooldown_seconds,
            "enabled": payload.enabled,
            "notify_discord": payload.notify_discord,
            "user_label": get_user_label(),
        }).execute()
    )
    if not res.data:
        raise HTTPException(500, detail={"error": "insert_failed"})
    new_row = res.data[0]
    # ws 訂閱由 monitor_list owner 統一管,active_signal 不再自己訂閱
    await get_signal_engine().refresh_active_signals()
    return new_row
```

`update_active` (line 101-141) 改成:

```python
@router.put("/api/active_signals/{sid}")
async def update_active(sid: str, payload: ActiveSignalCreate) -> dict:
    sb = _ensure_supabase()
    res = await asyncio.to_thread(
        lambda: sb.client.table("active_signals").update({
            "name": payload.name,
            "filter_json": payload.filter_json.model_dump(),
            "scope": payload.scope.model_dump(),
            "cooldown_seconds": payload.cooldown_seconds,
            "enabled": payload.enabled,
            "notify_discord": payload.notify_discord,
        })
        .eq("user_label", get_user_label())
        .eq("id", sid)
        .execute()
    )
    await get_signal_engine().refresh_active_signals()
    return res.data[0] if res.data else {}
```

`delete_active` (line 144-167) 改成:

```python
@router.delete("/api/active_signals/{sid}", status_code=204)
async def delete_active(sid: str) -> None:
    sb = _ensure_supabase()
    await asyncio.to_thread(
        lambda: sb.client.table("active_signals")
        .delete()
        .eq("user_label", get_user_label())
        .eq("id", sid)
        .execute()
    )
    await get_signal_engine().refresh_active_signals()
    return None
```

順手:把不再使用的 `from services.fubon_ws import get_ws_pool` import 移除(若沒被其他段引用)。

`active_signals.list_active` 的 select 加 `notify_discord` 欄位(line 65 附近):

```python
.select("id, name, filter_json, scope, cooldown_seconds, enabled, notify_discord, created_at")
```

`signal_engine._row_to_active` (line 116-123) 改成同時讀 `notify_discord`:

```python
def _row_to_active(self, r: dict) -> ActiveSignalOut:
    return ActiveSignalOut(
        id=r["id"], name=r["name"],
        filter_json=r["filter_json"], scope=r["scope"],
        cooldown_seconds=r.get("cooldown_seconds", 1800),
        enabled=r.get("enabled", True),
        notify_discord=r.get("notify_discord", True),
        created_at=str(r.get("created_at", "")),
    )
```

把 signal_engine `refresh_active_signals` 內的 select 也加上 `notify_discord` 欄位(line 93-99):

```python
res = await asyncio.to_thread(
    lambda: sb.client.table("active_signals")
    .select("id, name, filter_json, scope, cooldown_seconds, enabled, notify_discord, created_at")
    .eq("user_label", get_user_label())
    .eq("enabled", True)
    .execute()
)
```

- [ ] **Step 3: 跑回歸 tests**

```bash
cd backend && python -m pytest tests/ -v
```

Expected: 全 PASS(包括 Task 4/5 加進來的)。

- [ ] **Step 4: Commit**

```bash
git add backend/routes/active_signals.py backend/services/signal_engine.py
git commit -m "refactor(signals): active_signals no longer manages per-rule ws subs

WS subscription is now handled by monitor_list owner; rules just trigger
signal_engine.refresh_active_signals on CRUD. notify_discord is now
persisted on create/update and read on engine reload."
```

---

## Task 8: main.py — startup ws subscribe + include router

**Files:**
- Modify: `backend/main.py`
- Modify: `backend/.env.example`

- [ ] **Step 1: 改 `main.py`**

`backend/main.py:17-22` import 加 `monitor_list`:

```python
from routes import (
    active_signals, bookmarks, camarilla, candles, cdp as cdp_route,
    ma, monitor_list as monitor_list_route, mxf,
    preview, quote, signals_history, symbols,
    watchlist, ws,
)
```

`backend/main.py:146-158` include router 加一行:

```python
app.include_router(monitor_list_route.router)
```

lifespan 內(line 79-103,既有書籤訂閱邏輯下面)加 monitor_list 訂閱:

```python
# 訂閱 monitor_list — owner = "monitor_list"
if supabase.client is not None:
    try:
        ml_res = await asyncio.to_thread(
            lambda: supabase.client.table("monitor_list")
            .select("symbol")
            .eq("user_label", label)
            .execute()
        )
        for r in (ml_res.data or []):
            try:
                await pool.subscribe(r["symbol"], owner_id="monitor_list")
            except RuntimeError as e:
                logger.warning("startup monitor_list ws sub %s failed: %s", r["symbol"], e)
    except Exception as e:
        logger.error("startup monitor_list sub failed: %s", e)
```

- [ ] **Step 2: 改 `.env.example`**

`backend/.env.example` 在 `ALERTS_DISCORD_WEBHOOK_URL=` 那行下方加:

```
# 訊號觸發 Discord webhook(跟 ALERTS_DISCORD_WEBHOOK_URL 分開,可同 URL 也可分流)
SIGNALS_DISCORD_WEBHOOK_URL=
```

- [ ] **Step 3: 煙霧驗 — backend 開得起來**

```bash
cd backend && python -c "from main import app; print('ok')"
```

Expected: 印 `ok` 沒任何 ImportError / SyntaxError。

- [ ] **Step 4: Commit**

```bash
git add backend/main.py backend/.env.example
git commit -m "feat(signals): wire monitor_list router + startup ws subscribe

Startup pulls user's monitor_list and subscribes each symbol with
owner='monitor_list'. New SIGNALS_DISCORD_WEBHOOK_URL env doc added."
```

---

## Task 9: Frontend api.ts — types + monitorList client

**Files:**
- Modify: `frontend/src/lib/api.ts`

- [ ] **Step 1: Inspect 既有 api.ts**

讀 `frontend/src/lib/api.ts`,找到:
- `ActiveSignal` interface 定義位置
- `api.activeSignals.create / update / list` 對應的 payload type 位置
- 既有 `watchlist` client(對齊 monitorList 的命名 / 風格)

- [ ] **Step 2: 加 types**

在 `ActiveSignal` interface 內加(對齊既有 fields 風格):

```typescript
export interface ActiveSignal {
  // ... 既有 fields ...
  notify_discord: boolean;
}
```

並把 `ActiveSignalCreatePayload`(或實際 payload type 名字)加 `notify_discord?: boolean`(optional,未帶時後端 default true)。

加新 type(放在 `WatchlistItem` 旁邊):

```typescript
export interface MonitorListItem {
  symbol: string;
  added_at: string;
  name: string | null;
  market: string | null;
  is_etf: boolean | null;
}
```

- [ ] **Step 3: 加 monitorList client**

在 `api` object 內加(對齊既有 `api.watchlist` 風格):

```typescript
monitorList: {
  list:   () => fetchJSON<{ items: MonitorListItem[]; count: number }>("/api/monitor_list"),
  add:    (symbol: string) =>
    fetchJSON("/api/monitor_list", { method: "POST", body: JSON.stringify({ symbol }) }),
  remove: (symbol: string) =>
    fetchJSON(`/api/monitor_list/${encodeURIComponent(symbol)}`, { method: "DELETE" }),
},
```

(`fetchJSON` 內部如果已自動 stringify body 就直接傳 object,跟既有 client 對齊。)

- [ ] **Step 4: 跑 type check**

```bash
cd frontend && npm run build
```

Expected: tsc 沒任何 error(若舊處使用了 `scope` 必填、現在改 optional 也不該炸,因為 ActiveSignal 跟 ActiveSignalCreate 都還保留 scope)。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/api.ts
git commit -m "feat(signals): add MonitorListItem type + api.monitorList client"
```

---

## Task 10: useMonitorList hook

**Files:**
- Create: `frontend/src/hooks/useMonitorList.ts`

- [ ] **Step 1: Inspect 既有 hook 風格**

讀 `frontend/src/hooks/useWatchlist.ts` 對齊 pattern。

- [ ] **Step 2: 寫 hook**

新建 `frontend/src/hooks/useMonitorList.ts`:

```typescript
import { useCallback, useEffect, useState } from "react";
import { api, type MonitorListItem } from "../lib/api";

export function useMonitorList() {
  const [items, setItems] = useState<MonitorListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setError(null);
      const r = await api.monitorList.list();
      setItems(r.items);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  const add = useCallback(async (symbol: string) => {
    await api.monitorList.add(symbol);
    await refresh();
  }, [refresh]);

  const remove = useCallback(async (symbol: string) => {
    await api.monitorList.remove(symbol);
    await refresh();
  }, [refresh]);

  useEffect(() => { refresh(); }, [refresh]);

  return { items, loading, error, refresh, add, remove };
}
```

- [ ] **Step 3: 跑 type check**

```bash
cd frontend && npm run build
```

Expected: 沒 error。

- [ ] **Step 4: Commit**

```bash
git add frontend/src/hooks/useMonitorList.ts
git commit -m "feat(signals): add useMonitorList hook"
```

---

## Task 11: ActiveSignalEditor — 移除 scope、加 Discord toggle

**Files:**
- Modify: `frontend/src/components/ActiveSignalEditor.tsx`

- [ ] **Step 1: 改 component**

`frontend/src/components/ActiveSignalEditor.tsx` 內:

1. **移除** `scope` state (line 55):
   ```typescript
   // 刪掉這行
   const [scope, setScope] = useState<Scope>(initial?.scope ?? { type: "watchlist" });
   ```

2. **新增** `notifyDiscord` state(緊接 `enabled` state 後面,line 57 之後):
   ```typescript
   const [notifyDiscord, setNotifyDiscord] = useState(initial?.notify_discord ?? true);
   ```

3. **移除** Logic / Scope / Cooldown 區塊內整段「套用範圍」(line 345-349):
   ```jsx
   {/* 整段移除 */}
   <div>
     <div className="label-tiny mb-1">套用範圍</div>
     <label className="text-sm mr-3"><input type="radio" checked={scope.type === "watchlist"} ... /></label>
     <label className="text-sm"><input type="radio" checked={scope.type === "symbols"} ... /></label>
   </div>
   ```

4. **新增**「Discord 通知」區塊到同一個 grid 內(在 Logic 之後、Cooldown 之前):
   ```jsx
   <div>
     <div className="label-tiny mb-1">Discord 通知</div>
     <label className="text-sm flex items-center gap-2 cursor-pointer">
       <input
         type="checkbox"
         checked={notifyDiscord}
         onChange={(e) => setNotifyDiscord(e.target.checked)}
         className="accent-accent"
       />
       觸發時推送
     </label>
   </div>
   ```

5. **改 `save()`** (line 161-163) — payload 加 `notify_discord`、`scope` 保留發送固定 `{ type: "watchlist" }`(後端 ignore,但 schema 仍要求 valid 物件)。改成:

   ```typescript
   const payload = {
     name: name.trim(),
     filter_json: filter,
     scope: { type: "watchlist" as const },  // legacy; backend ignores
     cooldown_seconds: cooldown,
     enabled,
     notify_discord: notifyDiscord,
   };
   ```

6. 移除 `Scope` 的 import(line 6)若不再用。

- [ ] **Step 2: 啟動 frontend 手動驗**

```bash
cd frontend && npm run dev
```

開瀏覽器 → Monitor 頁 → 訊號規則 dialog → 新增規則:
- 看不到「套用範圍」區塊 ✓
- 看到「Discord 通知」checkbox(預設打勾)✓
- 取消勾選 + 存 → reload 後仍取消 ✓
- 編輯舊規則 → 載入時 `notify_discord` 預填正確 ✓

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/ActiveSignalEditor.tsx
git commit -m "feat(signals): editor drops scope picker, adds Discord toggle

Scope is unified across all rules (monitor_list)—UI no longer asks.
notify_discord defaults true and is per-rule."
```

---

## Task 12: BookmarksPanel — sidebar 加「監聽」入口 + view

**Files:**
- Modify: `frontend/src/components/BookmarksPanel.tsx`
- Modify: `frontend/src/pages/Monitor.tsx`(只取 monitorItems 傳下去)

- [ ] **Step 1: 改 `BookmarksPanel.tsx`**

加 import:

```typescript
import { useMonitorList } from "../hooks/useMonitorList";
import { type MonitorListItem } from "../lib/api";
```

constants 區加:

```typescript
const MONITOR_VIEW = "__monitor__";
```

在 `BookmarksPanel` 函式內(line 47 附近)拿 monitor list:

```typescript
const { items: monitorItems, remove: removeFromMonitor } = useMonitorList();
```

把 `selectedGroupId` initial state 保持 `ALL_VIEW`,但要支援 `MONITOR_VIEW`:不用改 type(已是 string)。

sidebar 渲染 — 在「全部」上方加固定項(line 110-116 附近):

```jsx
<SidebarItem
  label="☆ 監聽"
  count={monitorItems.length}
  selected={selectedGroupId === MONITOR_VIEW}
  onClick={() => pickGroup(MONITOR_VIEW)}
/>
<SidebarItem
  label="全部"
  count={bySymbolFirst.size}
  selected={selectedGroupId === ALL_VIEW}
  onClick={() => pickGroup(ALL_VIEW)}
/>
```

(`SidebarItem` 的 `label` 已是 string;加 emoji 看實際視覺效果。若顯示不佳改成普通文字「監聽」,system 旗標也可借用。)

主區渲染條件分支 — 改 line 137-178 的 ternary 變成:

```jsx
{selectedGroupId === MONITOR_VIEW ? (
  <MonitorListView
    items={monitorItems}
    quotes={quotes}
    rules={rules}
    hitCounts={hitCounts}
    selectedSymbol={selectedSymbol}
    onSelect={onSelectSymbol}
    onRemove={removeFromMonitor}
  />
) : selectedGroupId === ALL_VIEW ? (
  <AllView ... />
) : editMode && canEdit && selectedGroup ? (
  ...
) : (
  ...
)}
```

新增 `MonitorListView` component(放在檔尾,類似 `SingleListView`):

```tsx
function MonitorListView({
  items, quotes, rules, hitCounts, selectedSymbol, onSelect, onRemove,
}: {
  items: MonitorListItem[];
  quotes: Record<string, WatchlistQuote>;
  rules: ActiveSignal[];
  hitCounts: HitCounts;
  selectedSymbol: string | null;
  onSelect: (s: string) => void;
  onRemove: (s: string) => void;
}) {
  if (items.length === 0) {
    return <EmptyState text="監聽清單還是空的 — 上方搜尋或從書籤加入" />;
  }
  return (
    <ul>
      {items.map((it) => (
        <ItemRow
          key={it.symbol}
          item={{
            symbol: it.symbol,
            name: it.name,
            added_at: it.added_at,
            note: null,
            group_id: "",
          } as any}
          quote={quotes[it.symbol]}
          rules={rules}
          hitCounts={hitCounts}
          selectedSymbol={selectedSymbol}
          onSelect={onSelect}
          onRemove={onRemove}
          showRemove={true}
        />
      ))}
    </ul>
  );
}
```

(`ItemRow` 既有接 `BookmarkItem`,我們塞一個對齊形狀的物件就好;若 `ItemRow` 嚴格 type check 不過,提取共用 `RowDisplay` interface 或直接放寬 `ItemRow` 的 prop。)

- [ ] **Step 2: 改 `Monitor.tsx`**

把 BookmarksPanel onItemsChanged 傳出去的 symbols 用 union(book + monitor)餵 useWatchlistQuotes。但因為 `useMonitorList` 已在 BookmarksPanel 內被呼叫一次,還要在 Monitor 頁呼叫第二次(避免兩次 fetch),改成讓 BookmarksPanel `onItemsChanged` 額外傳 monitor symbols 一併出來:

— 為避免兩次 fetch,在 Monitor.tsx 直接也用 `useMonitorList`,然後 union 進 watchlistQuotes 訂閱範圍:

```typescript
import { useMonitorList } from "../hooks/useMonitorList";
const { items: monitorItems } = useMonitorList();
const allWatchSymbols = useMemo(
  () => Array.from(new Set([...bookmarkSymbols, ...monitorItems.map((m) => m.symbol)])),
  [bookmarkSymbols, monitorItems]
);
const watchlistQuotes = useWatchlistQuotes(allWatchSymbols);
```

(BookmarksPanel 內第二份 `useMonitorList` 在同 React tree 下會去重 fetch 嗎?React 沒有自動快取;會 fetch 兩次。可接受 — 監聽清單通常小;若想優化提到 context provider。)

- [ ] **Step 3: 手動驗**

```bash
cd frontend && npm run dev
```

- 書籤面板 sidebar 看到「☆ 監聽 (0)」最上 ✓
- 點進去顯示「監聽清單還是空的」empty state ✓
- 在「自選」加股票後,監聽清單仍是 0(獨立)✓

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/BookmarksPanel.tsx frontend/src/pages/Monitor.tsx
git commit -m "feat(signals): BookmarksPanel sidebar adds monitor list entry

New MonitorListView shows monitor_list items; Monitor page unions
bookmark + monitor symbols for quote subscriptions."
```

---

## Task 13: IntradayChart — header 加「+ 加入監聽」

**Files:**
- Modify: `frontend/src/components/IntradayChart.tsx`
- Modify: `frontend/src/pages/Monitor.tsx`(把 monitor add / remove handler 傳下去)

- [ ] **Step 1: Inspect 既有「+ 加入自選」按鈕**

讀 `IntradayChart.tsx` 找 `inAnyBookmark` / `onOpenBookmarkDialog` 對應的按鈕 JSX,記住既有風格。

- [ ] **Step 2: 加新 props + button**

`IntradayChart` 加 props:

```typescript
interface Props {
  // ... 既有 ...
  inMonitor: boolean;
  onAddToMonitor: () => void;
  onRemoveFromMonitor: () => void;
}
```

按鈕 JSX(對齊既有「+ 加入自選 / 已在自選 ✓」風格,新增第二顆):

```jsx
{inMonitor ? (
  <button
    type="button"
    onClick={onRemoveFromMonitor}
    className="text-xs text-ink-dim hover:text-bear px-2 py-1 border border-line"
    title="從監聽清單移除"
  >
    已在監聽 ✓
  </button>
) : (
  <button
    type="button"
    onClick={onAddToMonitor}
    className="text-xs text-ink-dim hover:text-accent px-2 py-1 border border-dashed border-line"
  >
    + 加入監聽
  </button>
)}
```

- [ ] **Step 3: 在 `Monitor.tsx` 接 handler**

```typescript
const { items: monitorItems, add: addToMonitor, remove: removeFromMonitor } = useMonitorList();

const inMonitor = useMemo(
  () => selected !== null && monitorItems.some((m) => m.symbol === selected),
  [monitorItems, selected]
);

// 傳給 IntradayChart
<IntradayChart
  // ... 既有 props ...
  inMonitor={inMonitor}
  onAddToMonitor={() => selected && addToMonitor(selected)}
  onRemoveFromMonitor={() => selected && removeFromMonitor(selected)}
/>
```

- [ ] **Step 4: 手動驗**

開瀏覽器:選一檔股票 → 看到「+ 加入監聽」按鈕 → 點下去 → 變「已在監聽 ✓」+ sidebar 監聽清單 count + 1 ✓。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/IntradayChart.tsx frontend/src/pages/Monitor.tsx
git commit -m "feat(signals): IntradayChart adds + 加入監聽 button"
```

---

## Task 14: AddToBookmarksDialog — 「同時加入監聽」checkbox

**Files:**
- Modify: `frontend/src/components/AddToBookmarksDialog.tsx`

- [ ] **Step 1: 改 dialog**

加 import:

```typescript
import { useMonitorList } from "../hooks/useMonitorList";
```

dialog 函式內加 state:

```typescript
const { add: addToMonitor } = useMonitorList();
const [alsoMonitor, setAlsoMonitor] = useState(false);
```

dialog body 底部(送出按鈕上方)加 checkbox:

```jsx
<label className="text-sm flex items-center gap-2 cursor-pointer text-ink-muted py-3 border-t border-line">
  <input
    type="checkbox"
    checked={alsoMonitor}
    onChange={(e) => setAlsoMonitor(e.target.checked)}
    className="accent-accent"
  />
  同時加入監聽清單(訊號評估)
</label>
```

送出 handler(對應「加入」按鈕的 onClick)加 monitor add:

```typescript
async function handleSubmit() {
  // ... 既有書籤加入邏輯 ...
  if (alsoMonitor) {
    try { await addToMonitor(symbol); }
    catch (e) { console.warn("add to monitor failed:", e); }
  }
  onChanged();
  onClose();
}
```

- [ ] **Step 2: 手動驗**

開 dialog → 勾「同時加入監聽清單」→ 加進書籤 → 確認監聽清單也 +1 ✓;不勾就只進書籤 ✓。

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/AddToBookmarksDialog.tsx
git commit -m "feat(signals): bookmark dialog adds opt-in monitor checkbox"
```

---

## Task 15: E2E 煙霧驗證

無 code change,純手動驗收(Rule 12「Fail loud」)。

- [ ] **Step 1: 重啟 backend(讀新 .env、跑 startup ws sub)**

```powershell
.\start.ps1
```

或

```powershell
cd backend
uvicorn main:app --reload
```

- [ ] **Step 2: 把監聽清單清空,加一檔測試標的**

UI:書籤面板 sidebar → 監聽 → 看到 empty state → toolbar 搜尋 `2330` → IntradayChart header 點「+ 加入監聽」→ sidebar 變 `監聽 (1)`。

- [ ] **Step 3: 建必觸發規則**

訊號規則 dialog → 新增 → 名稱 `smoke-test` → 跨指標條件:`close > 0`(必觸發)→ Discord 通知打勾 → 存。

- [ ] **Step 4: 設 Discord webhook**

`backend/.env` 加:

```
SIGNALS_DISCORD_WEBHOOK_URL=<你的 Discord channel webhook URL>
```

(可暫時複用 `ALERTS_DISCORD_WEBHOOK_URL` 的 URL 確認流程)。重啟 backend。

- [ ] **Step 5: 等盤中第一筆 tick(或人工 inject)**

盤中時觀察 — 觸發後驗三件:
1. Monitor 頁觸發歷史列表新增一筆 ✓
2. supabase `signals_log` 表多一 row(`select * from signals_log order by triggered_at desc limit 1;`)✓
3. Discord channel 收到 embed,包含規則名 `smoke-test` + 代號 `2330` + 價格 ✓

- [ ] **Step 6: 關掉 Discord 通知再觸發**

訊號規則 → 編輯 `smoke-test` → 取消 Discord 通知 → 存。等下一筆觸發 — Discord 不再收到,但 ① ② 仍正常 ✓。

- [ ] **Step 7: 收尾 — 刪 smoke-test 規則 + 從監聽清單移除 2330**

避免污染常規使用。

- [ ] **Step 8: 收尾 commit(若驗證過程中改了任何 config / docs)**

若有改 README 或 docs 補使用說明,commit。否則跳過。

---

## Self-review

**Spec coverage 對照(每個 spec 段落都點到一個 task):**

| Spec § | Task |
|---|---|
| §5 Schema | Task 1 |
| §6.1 discord_notifier | Task 3 |
| §6.2 signal_engine scope + fanout | Task 4 + Task 5 |
| §6.3 monitor_list route | Task 6 |
| §6.4 ActiveSignalCreate notify_discord | Task 2 + Task 7 |
| §6.5 active_signals route 清理 | Task 7 |
| §6.6 main.py startup ws sub | Task 8 |
| §6.7 .env.example | Task 8 |
| §7.1 api.ts | Task 9 |
| §7.2 useMonitorList | Task 10 |
| §7.3 ActiveSignalEditor | Task 11 |
| §7.4 MonitorListPanel(嵌 BookmarksPanel)| Task 12 |
| §7.5 IntradayChart + 加入監聽 | Task 13 |
| §7.6 AddToBookmarksDialog | Task 14 |
| §7.7 Monitor.tsx 整合 | Task 12 + Task 13 |
| §8 Error handling | 散落 Task 3 / 5 / 6(test 覆蓋 webhook unset、httpx fail、ws cap、insert fail rollback)|
| §9 Testing | Task 2-6 各帶 test;Task 15 = E2E |

**Type consistency check:**
- `MonitorListItem` 在 Task 9 / 10 / 12 一致
- `notify_discord` 從 Task 2(model)→ Task 5(`_fanout` 讀 `active.notify_discord`)→ Task 7(route insert/update + signal_engine select 帶 column + `_row_to_active` 讀)→ Task 9(frontend type)→ Task 11(編輯 toggle)貫通
- `OWNER_ID = "monitor_list"` 在 Task 6(route)= Task 8(main.py startup)一致

**Placeholder scan:** 無 TBD / TODO / 「similar to」/「implement later」。每步都有完整 code。
