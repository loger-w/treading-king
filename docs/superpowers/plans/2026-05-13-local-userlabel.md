# 本地版 + 共用 Supabase + user_label 隔離 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓 2~5 個信任朋友能在自己 Windows 電腦本機跑 trading-king，共用同一個 Supabase + 各自富邦 API key，靠 `.env` 的 `USER_LABEL` 在 4 張個人表做命名空間隔離。

**Architecture:** Backend 啟動讀 `USER_LABEL` 一次（fail-fast 驗證），所有 watchlist / strategies / active_signals / signals_log 的 query 自動 `.eq("user_label", LABEL)`。WSPool / signal_engine / broadcaster 不動（per-instance 本來就只服務本機）。Cache job 只在 `CACHE_JOB_OWNER` 相符的 instance 跑。

**Tech Stack:** FastAPI / Python 3.12 / Supabase（service_role）/ React + Vite / pytest（新加）/ PowerShell

**Spec:** `docs/superpowers/specs/2026-05-13-local-userlabel-design.md`

---

## File Structure

**Create:**
- `backend/services/user_context.py` — `get_user_label()` + `is_cache_job_owner()`
- `backend/tests/__init__.py` — empty
- `backend/tests/test_user_context.py` — pytest unit
- `backend/routes/me.py` — `GET /api/me`
- `backend/scripts/probe_label_isolation.py` — 整合煙霧（依專案慣例，非 pytest）
- `frontend/src/hooks/useMe.ts` — `useMe()` hook
- `supabase/migrations/0005_user_label.sql` — schema migration
- `install.ps1` — repo 根目錄
- `start.ps1` — repo 根目錄

**Modify:**
- `backend/pyproject.toml` — 加 `[project.optional-dependencies] dev = ["pytest>=8"]`
- `backend/main.py` — startup validation + watchlist subscribe label filter + conditional overnight_loop + include me router
- `backend/routes/watchlist.py` — 所有 query 加 label
- `backend/routes/strategies.py` — 所有 query 加 label
- `backend/routes/active_signals.py` — 所有 query 加 label + `_scope_symbols` 加 label
- `backend/routes/signals_history.py` — 兩個 GET 加 label
- `backend/routes/cache.py` — `POST /api/cache/refresh` 非 OWNER 回 403
- `backend/routes/health.py` — payload 加 `user_label`
- `backend/services/signal_engine.py` — `refresh_active_signals` 加 label + `_refill_field_cache` watchlist subquery 加 label + `_fanout` 帶 label
- `backend/services/supabase_writer.py` — payload row 注入 label
- `backend/.env.example` — 加 `USER_LABEL` / `CACHE_JOB_OWNER`
- `frontend/src/lib/api.ts` — `MeResponse` + `api.me()`
- `frontend/src/App.tsx` — Masthead 顯示 label badge
- `.gitignore` — 加 `backend/wheels/*.whl`
- `README.md` — 整篇改寫面向使用者

---

## Phase A — Foundation (TDD)

### Task 1: Add pytest dev dep + write user_context unit tests

**Files:**
- Modify: `backend/pyproject.toml`
- Create: `backend/tests/__init__.py`
- Create: `backend/tests/test_user_context.py`

- [ ] **Step 1: Add pytest as dev dep**

Replace lines 17-19 of `backend/pyproject.toml`:

```toml
[project.optional-dependencies]
dev = ["pytest>=8"]

[tool.ruff]
line-length = 100
target-version = "py312"
```

- [ ] **Step 2: Install dev dep**

Run (from `C:\side-project\trading-king\backend`):

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Expected: pytest installs successfully; `pytest --version` ≥ 8.0.

- [ ] **Step 3: Create tests package**

Create empty file `backend/tests/__init__.py` (just `touch` — pytest discovery 需要)

- [ ] **Step 4: Write failing tests**

Create `backend/tests/test_user_context.py`:

```python
"""Unit tests for backend/services/user_context.py."""
from __future__ import annotations

import importlib
import os
import sys

import pytest


def _reload_user_context():
    """Re-import user_context to reset lru_cache after env mutation."""
    if "services.user_context" in sys.modules:
        importlib.reload(sys.modules["services.user_context"])
    from services import user_context
    return user_context


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("USER_LABEL", raising=False)
    monkeypatch.delenv("CACHE_JOB_OWNER", raising=False)
    yield


def test_valid_label(monkeypatch):
    monkeypatch.setenv("USER_LABEL", "loger")
    uc = _reload_user_context()
    assert uc.get_user_label() == "loger"


def test_valid_label_with_underscore_and_digits(monkeypatch):
    monkeypatch.setenv("USER_LABEL", "alice_2")
    uc = _reload_user_context()
    assert uc.get_user_label() == "alice_2"


@pytest.mark.parametrize("bad", ["", " ", "Loger", "foo bar", "x", "a" * 21, "user@host"])
def test_invalid_label_raises(monkeypatch, bad):
    monkeypatch.setenv("USER_LABEL", bad)
    uc = _reload_user_context()
    with pytest.raises(RuntimeError, match="USER_LABEL invalid"):
        uc.get_user_label()


def test_cache_owner_match(monkeypatch):
    monkeypatch.setenv("USER_LABEL", "loger")
    monkeypatch.setenv("CACHE_JOB_OWNER", "loger")
    uc = _reload_user_context()
    assert uc.is_cache_job_owner() is True


def test_cache_owner_mismatch(monkeypatch):
    monkeypatch.setenv("USER_LABEL", "alice")
    monkeypatch.setenv("CACHE_JOB_OWNER", "loger")
    uc = _reload_user_context()
    assert uc.is_cache_job_owner() is False


def test_cache_owner_unset(monkeypatch):
    monkeypatch.setenv("USER_LABEL", "alice")
    uc = _reload_user_context()
    assert uc.is_cache_job_owner() is False
```

- [ ] **Step 5: Run tests — expect ImportError**

Run (from `backend/`):

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_user_context.py -v
```

Expected: All tests ERROR (collection failure) with `ModuleNotFoundError: No module named 'services.user_context'`. That's fine — we're TDD.

- [ ] **Step 6: Commit**

```powershell
git add backend/pyproject.toml backend/tests/__init__.py backend/tests/test_user_context.py
git commit -m "test(user_context): add failing unit tests for USER_LABEL validation"
```

---

### Task 2: Implement user_context.py

**Files:**
- Create: `backend/services/user_context.py`

- [ ] **Step 1: Implement minimal user_context.py**

Create `backend/services/user_context.py`:

```python
"""USER_LABEL / CACHE_JOB_OWNER 讀取 + 驗證。

backend 啟動時 call get_user_label() 一次，驗證失敗直接 raise → uvicorn 不會起來。
所有 route / service 透過 get_user_label() 拿 label，避免散落 os.getenv 拼字錯誤。
"""
from __future__ import annotations

import os
import re
from functools import lru_cache

_LABEL_RE = re.compile(r"^[a-z0-9_-]{2,20}$")


@lru_cache(maxsize=1)
def get_user_label() -> str:
    raw = (os.getenv("USER_LABEL") or "").strip()
    if not _LABEL_RE.match(raw):
        raise RuntimeError(
            f"USER_LABEL invalid: {raw!r}. Must match [a-z0-9_-]{{2,20}}."
        )
    return raw


def is_cache_job_owner() -> bool:
    """OWNER 跟 USER_LABEL 相符才回 True。OWNER 未設或不符都回 False。"""
    owner = (os.getenv("CACHE_JOB_OWNER") or "").strip().lower()
    if not owner:
        return False
    return owner == get_user_label()
```

- [ ] **Step 2: Run tests — expect PASS**

Run (from `backend/`):

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_user_context.py -v
```

Expected: All 13 tests PASS (1 valid + 1 underscore + 7 parametrized invalid + 3 cache_owner + 1 unset = 13).

If `test_invalid_label_raises` 任何一個 FAIL，檢查 regex / strip 邏輯。

- [ ] **Step 3: Commit**

```powershell
git add backend/services/user_context.py
git commit -m "feat(user_context): validate USER_LABEL and detect cache job owner"
```

---

### Task 3: Wire user_context into main.py

**Files:**
- Modify: `backend/main.py`

- [ ] **Step 1: Add import + startup validation + overnight gate**

Edit `backend/main.py`:

After line 28 (the existing `from services.supabase_writer import get_supabase_writer  # noqa: E402`), add:

```python
from services.user_context import get_user_label, is_cache_job_owner  # noqa: E402
```

Replace line 36-37 (inside `lifespan`):

```python
    logger.info("=" * 60)
    logger.info("treading-king BFF starting up")
    logger.info("=" * 60)
```

with:

```python
    logger.info("=" * 60)
    logger.info("treading-king BFF starting up")
    logger.info("=" * 60)

    # Fail-fast: 壞 label 不讓 backend 起來
    label = get_user_label()
    cache_owner = is_cache_job_owner()
    logger.info("USER_LABEL=%s, cache_job_owner=%s", label, cache_owner)
```

Replace lines 56-66（startup watchlist subscribe）:

```python
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
```

with:

```python
    # 訂閱 watchlist 內所有 symbols（用 watchlist owner）
    if supabase.client is not None:
        try:
            res = await asyncio.to_thread(
                lambda: supabase.client.table("watchlist")
                .select("symbol")
                .eq("user_label", label)
                .execute()
            )
            for r in (res.data or []):
                try:
                    await pool.subscribe(r["symbol"], owner_id="watchlist")
                except RuntimeError as e:
                    logger.warning("startup ws sub %s failed: %s", r["symbol"], e)
        except Exception as e:
            logger.error("startup watchlist sub failed: %s", e)
```

Replace lines 68-69（overnight task 啟動）:

```python
    # 啟動 overnight 8:25 cron
    overnight_task = asyncio.create_task(overnight_loop())
```

with:

```python
    # 啟動 overnight 8:25 cron — 只在 CACHE_JOB_OWNER == USER_LABEL 的 instance 跑
    if cache_owner:
        overnight_task = asyncio.create_task(overnight_loop())
        logger.info("overnight loop started (this instance is the cache owner)")
    else:
        overnight_task = None
        logger.info("cache job skipped (CACHE_JOB_OWNER != USER_LABEL=%s)", label)
```

Replace line 76-77（shutdown cancellation）:

```python
    logger.info("Shutting down…")
    overnight_task.cancel()
```

with:

```python
    logger.info("Shutting down…")
    if overnight_task is not None:
        overnight_task.cancel()
```

- [ ] **Step 2: Smoke test with valid label**

Run (from `backend/`):

```powershell
$env:USER_LABEL = "loger"
$env:CACHE_JOB_OWNER = "loger"
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Expected log lines:
- `USER_LABEL=loger, cache_job_owner=True`
- `overnight loop started (this instance is the cache owner)`

Ctrl+C 收掉。

- [ ] **Step 3: Smoke test with non-owner label**

Run:

```powershell
$env:USER_LABEL = "alice"
$env:CACHE_JOB_OWNER = "loger"
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Expected log lines:
- `USER_LABEL=alice, cache_job_owner=False`
- `cache job skipped (CACHE_JOB_OWNER != USER_LABEL=alice)`

Ctrl+C 收掉。

- [ ] **Step 4: Smoke test with invalid label — expect startup fail**

Run:

```powershell
$env:USER_LABEL = "Bad Label"
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Expected: uvicorn 啟動失敗，stack trace 含 `RuntimeError: USER_LABEL invalid: 'Bad Label'`.

把 `$env:USER_LABEL` 改回 `loger`。

- [ ] **Step 5: Commit**

```powershell
git add backend/main.py
git commit -m "feat(main): fail-fast on bad USER_LABEL; gate overnight_loop on CACHE_JOB_OWNER"
```

---

## Phase B — Schema Migration

### Task 4: Create migration 0005_user_label.sql

**Files:**
- Create: `supabase/migrations/0005_user_label.sql`

- [ ] **Step 1: Write migration SQL**

Create `supabase/migrations/0005_user_label.sql`:

```sql
-- 2026-05-13 — 本地版 + 共用 Supabase + user_label 隔離
-- 4 張個人表加 user_label；既有 row 全 backfill 為 'loger'；之後 drop default。
-- 共用市場資料表（symbols / indicator_cache / daily_ohlc / cache_runs）不動。

-- ---------------------------------------------------------------------------
-- watchlist: PK 從 (symbol) 改為 (user_label, symbol)
-- ---------------------------------------------------------------------------
alter table watchlist add column if not exists user_label text not null default 'loger';
alter table watchlist drop constraint if exists watchlist_pkey;
alter table watchlist add primary key (user_label, symbol);
alter table watchlist alter column user_label drop default;
create index if not exists idx_watchlist_label on watchlist(user_label);

-- ---------------------------------------------------------------------------
-- strategies (id 已是 uuid PK，不動 PK)
-- ---------------------------------------------------------------------------
alter table strategies add column if not exists user_label text not null default 'loger';
alter table strategies alter column user_label drop default;
create index if not exists idx_strategies_label on strategies(user_label, created_at desc);

-- ---------------------------------------------------------------------------
-- active_signals
-- ---------------------------------------------------------------------------
alter table active_signals add column if not exists user_label text not null default 'loger';
alter table active_signals alter column user_label drop default;
create index if not exists idx_active_signals_label_enabled
  on active_signals(user_label, enabled) where enabled;

-- ---------------------------------------------------------------------------
-- signals_log
-- ---------------------------------------------------------------------------
alter table signals_log add column if not exists user_label text not null default 'loger';
alter table signals_log alter column user_label drop default;
create index if not exists idx_signals_log_label_time on signals_log(user_label, triggered_at desc);
```

- [ ] **Step 2: Apply migration via Supabase MCP**

Use the Supabase MCP `apply_migration` tool with the SQL above (or via Supabase dashboard SQL editor / CLI — whichever the operator prefers). Confirm via `mcp__supabase__get_project_url` that the target project matches `backend/.env`'s `SUPABASE_URL`.

- [ ] **Step 3: Verify backfill via SQL**

Run via `mcp__supabase__execute_sql`:

```sql
select 'watchlist' as t,
       count(*) filter (where user_label='loger') as loger,
       count(*) filter (where user_label is null) as nulls
from watchlist
union all
select 'strategies', count(*) filter (where user_label='loger'),
       count(*) filter (where user_label is null) from strategies
union all
select 'active_signals', count(*) filter (where user_label='loger'),
       count(*) filter (where user_label is null) from active_signals
union all
select 'signals_log', count(*) filter (where user_label='loger'),
       count(*) filter (where user_label is null) from signals_log;
```

Expected: 4 rows, `nulls` 全部 = 0。`loger` 大於 0（你的既有資料）。

- [ ] **Step 4: Commit**

```powershell
git add supabase/migrations/0005_user_label.sql
git commit -m "feat(db): 0005 — add user_label to watchlist/strategies/active_signals/signals_log"
```

---

## Phase C — Route Changes

### Task 5: Watchlist routes — label filter

**Files:**
- Modify: `backend/routes/watchlist.py`

- [ ] **Step 1: Add import + label filter to all 3 endpoints**

Edit `backend/routes/watchlist.py`:

Replace line 16 import block:

```python
from services.cdp import get_cdp_service
from services.fubon_ws import get_ws_pool
from services.supabase_client import SupabaseStatus, get_supabase
```

with:

```python
from services.cdp import get_cdp_service
from services.fubon_ws import get_ws_pool
from services.supabase_client import SupabaseStatus, get_supabase
from services.user_context import get_user_label
```

Replace `list_watchlist`（lines 35-56）`.select(...).order(...)` chain:

```python
    res = await asyncio.to_thread(
        lambda: sb.client.table("watchlist")
        .select("symbol, added_at, note, symbols(name, market, is_etf)")
        .order("added_at", desc=True)
        .execute()
    )
```

with:

```python
    res = await asyncio.to_thread(
        lambda: sb.client.table("watchlist")
        .select("symbol, added_at, note, symbols(name, market, is_etf)")
        .eq("user_label", get_user_label())
        .order("added_at", desc=True)
        .execute()
    )
```

Replace `add_watchlist` INSERT payload（line 70-74）:

```python
        await asyncio.to_thread(
            lambda: sb.client.table("watchlist").insert({
                "symbol": payload.symbol, "note": payload.note,
            }).execute()
        )
```

with:

```python
        await asyncio.to_thread(
            lambda: sb.client.table("watchlist").insert({
                "symbol": payload.symbol,
                "note": payload.note,
                "user_label": get_user_label(),
            }).execute()
        )
```

Replace `remove_watchlist` DELETE（lines 102-104）:

```python
    await asyncio.to_thread(
        lambda: sb.client.table("watchlist").delete().eq("symbol", symbol).execute()
    )
```

with:

```python
    await asyncio.to_thread(
        lambda: sb.client.table("watchlist")
        .delete()
        .eq("user_label", get_user_label())
        .eq("symbol", symbol)
        .execute()
    )
```

- [ ] **Step 2: Manual verification**

Start backend with `USER_LABEL=loger`, then:

```powershell
$apiKey = "<your BFF_API_KEY>"
$h = @{ "X-API-Key" = $apiKey }
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/watchlist" -Headers $h
```

Expected: 回傳的 watchlist 應該就是你既有的（backfill 為 loger 的 row）。

加一筆測試：

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/watchlist" -Method POST `
  -Headers $h -ContentType "application/json" `
  -Body '{"symbol":"2330","note":"test"}'
```

Expected: `{"symbol":"2330","status":"added"}`。再 GET 一次應該看到 2330。

DELETE 它：

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/watchlist/2330" -Method DELETE -Headers $h
```

Expected: 204 / 無 body。再 GET 一次應該沒有 2330。

- [ ] **Step 3: Commit**

```powershell
git add backend/routes/watchlist.py
git commit -m "feat(watchlist): scope queries by user_label"
```

---

### Task 6: Strategies routes — label filter

**Files:**
- Modify: `backend/routes/strategies.py`

- [ ] **Step 1: Add import + filter all 3 endpoints**

Replace line 13 import:

```python
from services.supabase_client import SupabaseStatus, get_supabase
```

with:

```python
from services.supabase_client import SupabaseStatus, get_supabase
from services.user_context import get_user_label
```

Replace `list_strategies`（lines 35-44 body）:

```python
@router.get("/api/strategies")
async def list_strategies() -> dict:
    sb = _ensure_supabase()
    res = (
        sb.client.table("strategies")
        .select("id, name, description, filter_json, created_at")
        .order("created_at", desc=True)
        .execute()
    )
    return {"strategies": res.data or []}
```

with:

```python
@router.get("/api/strategies")
async def list_strategies() -> dict:
    sb = _ensure_supabase()
    res = (
        sb.client.table("strategies")
        .select("id, name, description, filter_json, created_at")
        .eq("user_label", get_user_label())
        .order("created_at", desc=True)
        .execute()
    )
    return {"strategies": res.data or []}
```

Replace `create_strategy` INSERT（lines 50-60）:

```python
    res = (
        sb.client.table("strategies")
        .insert(
            {
                "name": payload.name,
                "description": payload.description,
                "filter_json": payload.filter_json.model_dump(),
            }
        )
        .execute()
    )
```

with:

```python
    res = (
        sb.client.table("strategies")
        .insert(
            {
                "name": payload.name,
                "description": payload.description,
                "filter_json": payload.filter_json.model_dump(),
                "user_label": get_user_label(),
            }
        )
        .execute()
    )
```

Replace `delete_strategy`（lines 66-70）:

```python
@router.delete("/api/strategies/{strategy_id}", status_code=204)
async def delete_strategy(strategy_id: str) -> None:
    sb = _ensure_supabase()
    sb.client.table("strategies").delete().eq("id", strategy_id).execute()
    return None
```

with:

```python
@router.delete("/api/strategies/{strategy_id}", status_code=204)
async def delete_strategy(strategy_id: str) -> None:
    sb = _ensure_supabase()
    (
        sb.client.table("strategies")
        .delete()
        .eq("user_label", get_user_label())
        .eq("id", strategy_id)
        .execute()
    )
    return None
```

- [ ] **Step 2: Manual verification**

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/strategies" -Headers $h
```

Expected: 你既有的 strategies（label=loger 那批）。

- [ ] **Step 3: Commit**

```powershell
git add backend/routes/strategies.py
git commit -m "feat(strategies): scope queries by user_label"
```

---

### Task 7: Active signals routes — label filter

**Files:**
- Modify: `backend/routes/active_signals.py`

- [ ] **Step 1: Add import + filter all endpoints + helper**

Replace line 17 import block:

```python
from services.fubon_ws import get_ws_pool
from services.signal_engine import get_signal_engine
from services.supabase_client import SupabaseStatus, get_supabase
```

with:

```python
from services.fubon_ws import get_ws_pool
from services.signal_engine import get_signal_engine
from services.supabase_client import SupabaseStatus, get_supabase
from services.user_context import get_user_label
```

Replace `_scope_symbols` watchlist branch（lines 35-39）:

```python
    if scope.get("type") == "watchlist":
        res = await asyncio.to_thread(
            lambda: sb.client.table("watchlist").select("symbol").execute()
        )
        return [r["symbol"] for r in (res.data or [])]
```

with:

```python
    if scope.get("type") == "watchlist":
        res = await asyncio.to_thread(
            lambda: sb.client.table("watchlist")
            .select("symbol")
            .eq("user_label", get_user_label())
            .execute()
        )
        return [r["symbol"] for r in (res.data or [])]
```

Replace `list_active`（lines 43-51）:

```python
@router.get("/api/active_signals")
async def list_active() -> dict:
    sb = _ensure_supabase()
    res = await asyncio.to_thread(
        lambda: sb.client.table("active_signals")
        .select("id, name, filter_json, scope, cooldown_seconds, ignore_auctions, enabled, created_at")
        .order("created_at", desc=True).execute()
    )
    return {"active_signals": res.data or []}
```

with:

```python
@router.get("/api/active_signals")
async def list_active() -> dict:
    sb = _ensure_supabase()
    res = await asyncio.to_thread(
        lambda: sb.client.table("active_signals")
        .select("id, name, filter_json, scope, cooldown_seconds, ignore_auctions, enabled, created_at")
        .eq("user_label", get_user_label())
        .order("created_at", desc=True).execute()
    )
    return {"active_signals": res.data or []}
```

Replace `create_active` INSERT（lines 57-66）:

```python
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
```

with:

```python
    res = await asyncio.to_thread(
        lambda: sb.client.table("active_signals").insert({
            "name": payload.name,
            "filter_json": payload.filter_json.model_dump(),
            "scope": payload.scope.model_dump(),
            "cooldown_seconds": payload.cooldown_seconds,
            "ignore_auctions": payload.ignore_auctions,
            "enabled": payload.enabled,
            "user_label": get_user_label(),
        }).execute()
    )
```

Replace `update_active` old fetch + update（lines 86-88 + 96-105）:

```python
    old = await asyncio.to_thread(
        lambda: sb.client.table("active_signals").select("scope, enabled").eq("id", sid).single().execute()
    )
```

with:

```python
    old = await asyncio.to_thread(
        lambda: sb.client.table("active_signals")
        .select("scope, enabled")
        .eq("user_label", get_user_label())
        .eq("id", sid)
        .single()
        .execute()
    )
```

And:

```python
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
```

with:

```python
    res = await asyncio.to_thread(
        lambda: sb.client.table("active_signals").update({
            "name": payload.name,
            "filter_json": payload.filter_json.model_dump(),
            "scope": payload.scope.model_dump(),
            "cooldown_seconds": payload.cooldown_seconds,
            "ignore_auctions": payload.ignore_auctions,
            "enabled": payload.enabled,
        })
        .eq("user_label", get_user_label())
        .eq("id", sid)
        .execute()
    )
```

Replace `delete_active`（lines 120-130）兩個 query:

```python
    old = await asyncio.to_thread(
        lambda: sb.client.table("active_signals").select("scope, enabled").eq("id", sid).single().execute()
    )
    if old.data and old.data.get("enabled"):
        for sym in await _scope_symbols(old.data.get("scope", {})):
            await get_ws_pool().unsubscribe(sym, owner_id=sid)

    await asyncio.to_thread(
        lambda: sb.client.table("active_signals").delete().eq("id", sid).execute()
    )
```

with:

```python
    old = await asyncio.to_thread(
        lambda: sb.client.table("active_signals")
        .select("scope, enabled")
        .eq("user_label", get_user_label())
        .eq("id", sid)
        .single()
        .execute()
    )
    if old.data and old.data.get("enabled"):
        for sym in await _scope_symbols(old.data.get("scope", {})):
            await get_ws_pool().unsubscribe(sym, owner_id=sid)

    await asyncio.to_thread(
        lambda: sb.client.table("active_signals")
        .delete()
        .eq("user_label", get_user_label())
        .eq("id", sid)
        .execute()
    )
```

- [ ] **Step 2: Manual verification**

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/active_signals" -Headers $h
```

Expected: 你既有的 active_signals。

- [ ] **Step 3: Commit**

```powershell
git add backend/routes/active_signals.py
git commit -m "feat(active_signals): scope CRUD + scope_symbols watchlist subquery by user_label"
```

---

### Task 8: Signals history routes — label filter

**Files:**
- Modify: `backend/routes/signals_history.py`

- [ ] **Step 1: Add import + filter both endpoints**

Edit `backend/routes/signals_history.py`:

Replace line 12 import:

```python
from services.supabase_client import SupabaseStatus, get_supabase
```

with:

```python
from services.supabase_client import SupabaseStatus, get_supabase
from services.user_context import get_user_label
```

Replace `signals_history` query builder（lines 28-38）:

```python
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
```

with:

```python
    def _q():
        q = sb.client.table("signals_log").select(
            "id, active_signal_id, symbol, triggered_at, trigger_price, trigger_volume, context_json"
        ).eq("user_label", get_user_label()).order("triggered_at", desc=True).limit(limit)
        if symbol:
            q = q.eq("symbol", symbol)
        if active_signal_id:
            q = q.eq("active_signal_id", active_signal_id)
        if since:
            q = q.gte("triggered_at", since)
        return q.execute()
```

Replace `today_counts` query（lines 57-63）:

```python
    def _q():
        return (
            sb.client.table("signals_log")
            .select("symbol, active_signal_id")
            .gte("triggered_at", today_start_tw.isoformat())
            .execute()
        )
```

with:

```python
    def _q():
        return (
            sb.client.table("signals_log")
            .select("symbol, active_signal_id")
            .eq("user_label", get_user_label())
            .gte("triggered_at", today_start_tw.isoformat())
            .execute()
        )
```

- [ ] **Step 2: Manual verification**

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/signals/history?limit=5" -Headers $h
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/signals/today_counts" -Headers $h
```

Expected: 兩個都回 200，內容是你 label=loger 那批。

- [ ] **Step 3: Commit**

```powershell
git add backend/routes/signals_history.py
git commit -m "feat(signals_history): scope signals_log queries by user_label"
```

---

### Task 9: Cache refresh 403 gate + health payload field

**Files:**
- Modify: `backend/routes/cache.py`
- Modify: `backend/routes/health.py`

- [ ] **Step 1: Cache route 403 for non-owner**

Edit `backend/routes/cache.py`:

Replace line 23 import:

```python
from services.supabase_client import SupabaseStatus, get_supabase
```

with:

```python
from services.supabase_client import SupabaseStatus, get_supabase
from services.user_context import is_cache_job_owner
```

In `refresh_cache`（line 41 起），在 `if is_cache_job_running()` 之前加：

Replace lines 49-52:

```python
    if is_cache_job_running():
        raise HTTPException(
            409,
            detail={"error": "already_running", "message": "cache job already in progress"},
        )
```

with:

```python
    if not is_cache_job_owner():
        raise HTTPException(
            403,
            detail={"error": "not_cache_owner",
                    "message": "this instance is not the configured CACHE_JOB_OWNER"},
        )

    if is_cache_job_running():
        raise HTTPException(
            409,
            detail={"error": "already_running", "message": "cache job already in progress"},
        )
```

- [ ] **Step 2: Health payload add user_label**

Edit `backend/routes/health.py`:

Replace line 18 import:

```python
from services.supabase_writer import get_supabase_writer
```

with:

```python
from services.supabase_writer import get_supabase_writer
from services.user_context import get_user_label, is_cache_job_owner
```

Replace the `return {` block (lines 60-77)：

```python
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

with:

```python
    return {
        "status": overall,
        "fubon_status": fubon.status.value,
        "fubon_last_error": fubon.last_error,
        "supabase_status": supabase.status.value,
        "supabase_last_error": supabase.last_error,
        "is_trading_day": is_trading_day,
        "cache_last_success_at": cache_last_success_at,
        "cache_last_run_status": cache_last_run_status,
        "user_label": get_user_label(),
        "is_cache_owner": is_cache_job_owner(),
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

- [ ] **Step 3: Manual verification**

Restart backend with `USER_LABEL=loger, CACHE_JOB_OWNER=loger`.

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/health" -Headers $h | ConvertTo-Json -Depth 5
```

Expected: payload 含 `"user_label": "loger"`, `"is_cache_owner": true`.

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/cache/refresh?limit=1" -Method POST -Headers $h
```

Expected: 202，回 `{"status":"accepted",...}`。

改 `$env:CACHE_JOB_OWNER = "someone_else"`，重啟 backend：

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/cache/refresh?limit=1" -Method POST -Headers $h
```

Expected: 403 with `not_cache_owner`.

完事後 `$env:CACHE_JOB_OWNER = "loger"` 改回。

- [ ] **Step 4: Commit**

```powershell
git add backend/routes/cache.py backend/routes/health.py
git commit -m "feat(cache,health): gate /api/cache/refresh on owner; expose user_label in /api/health"
```

---

### Task 10: Signal engine + supabase writer — label filter + INSERT label

**Files:**
- Modify: `backend/services/signal_engine.py`
- Modify: `backend/services/supabase_writer.py`

- [ ] **Step 1: signal_engine — refresh_active_signals 加 label**

Edit `backend/services/signal_engine.py`:

After line 19 add import:

```python
from services.user_context import get_user_label
```

Replace `refresh_active_signals`（lines 72-87）`select(...).eq("enabled", True).execute()`:

```python
        res = await asyncio.to_thread(
            lambda: sb.client.table("active_signals")
            .select("id, name, filter_json, scope, cooldown_seconds, ignore_auctions, enabled, created_at")
            .eq("enabled", True)
            .execute()
        )
```

with:

```python
        res = await asyncio.to_thread(
            lambda: sb.client.table("active_signals")
            .select("id, name, filter_json, scope, cooldown_seconds, ignore_auctions, enabled, created_at")
            .eq("user_label", get_user_label())
            .eq("enabled", True)
            .execute()
        )
```

Replace `_refill_field_cache` watchlist subquery（lines 127-133）:

```python
                elif scope.get("type") == "watchlist":
                    # watchlist 全部
                    res = await asyncio.to_thread(
                        lambda: sb.client.table("watchlist").select("symbol").execute()
                    )
                    for row in (res.data or []):
                        symbols_needed.add(row["symbol"])
```

with:

```python
                elif scope.get("type") == "watchlist":
                    # watchlist 全部（限定本 instance 的 user_label）
                    res = await asyncio.to_thread(
                        lambda: sb.client.table("watchlist")
                        .select("symbol")
                        .eq("user_label", get_user_label())
                        .execute()
                    )
                    for row in (res.data or []):
                        symbols_needed.add(row["symbol"])
```

Replace `_fanout` writer append payload（lines 335-341）:

```python
        get_supabase_writer().append({
            "active_signal_id": active.id,
            "symbol": symbol,
            "trigger_price": tick.price,
            "trigger_volume": tick.size,
            "context_json": {"latest_tick_time": tick.time},
        })
```

with:

```python
        get_supabase_writer().append({
            "active_signal_id": active.id,
            "symbol": symbol,
            "trigger_price": tick.price,
            "trigger_volume": tick.size,
            "context_json": {"latest_tick_time": tick.time},
            "user_label": get_user_label(),
        })
```

Replace `_auto_disable_all`（line 364-365）:

```python
            await asyncio.to_thread(
                lambda: sb.client.table("active_signals").update({"enabled": False}).eq("enabled", True).execute()
            )
```

with:

```python
            await asyncio.to_thread(
                lambda: sb.client.table("active_signals")
                .update({"enabled": False})
                .eq("user_label", get_user_label())
                .eq("enabled", True)
                .execute()
            )
```

- [ ] **Step 2: supabase_writer — accept label-bearing payload**

Edit `backend/services/supabase_writer.py`:

No code change needed — `append(row)` 接收 dict 後直接 batch insert，已經會帶上 `user_label` field（signal_engine 在 step 1 已加進 payload）。

驗證一下沒有 column-allowlist 邏輯：confirm `_flush()` 直接 `.insert(batch).execute()` 沒有 schema filtering — 是的，第 75 行 `sb.client.table("signals_log").insert(batch).execute()`。✓

- [ ] **Step 3: Smoke test signal engine refresh**

Restart backend with `USER_LABEL=loger`. 觀察 log:

```
INFO services.signal_engine: active_signals reloaded: N enabled
```

N 應該等於你既有 enabled=true 且 user_label=loger 的 active_signal 數量。

- [ ] **Step 4: Commit**

```powershell
git add backend/services/signal_engine.py
git commit -m "feat(signal_engine): scope active_signals + watchlist subquery + signals_log insert by user_label"
```

---

## Phase D — /api/me + Frontend Badge

### Task 11: Add /api/me route

**Files:**
- Create: `backend/routes/me.py`
- Modify: `backend/main.py`

- [ ] **Step 1: Write me.py**

Create `backend/routes/me.py`:

```python
"""GET /api/me — 回傳當前 backend 的 USER_LABEL + cache owner 旗標。

前端用來顯示「You are: <label>」徽章，避免 .env 設錯卻不自知。
"""
from __future__ import annotations

from fastapi import APIRouter

from services.user_context import get_user_label, is_cache_job_owner

router = APIRouter()


@router.get("/api/me")
async def me() -> dict:
    return {
        "user_label": get_user_label(),
        "is_cache_owner": is_cache_job_owner(),
    }
```

- [ ] **Step 2: Register router in main.py**

Edit `backend/main.py`:

Replace line 17-21 import block:

```python
from routes import (
    active_signals, cache, candles, cdp as cdp_route, health, preview,
    quote, screen, signals_history, strategies, symbols,
    watchlist, ws,
)  # noqa: E402
```

with:

```python
from routes import (
    active_signals, cache, candles, cdp as cdp_route, health, me as me_route,
    preview, quote, screen, signals_history, strategies, symbols,
    watchlist, ws,
)  # noqa: E402
```

After line 113 `app.include_router(ws.router)` add (or order with others):

```python
app.include_router(me_route.router)
```

- [ ] **Step 3: Manual verification**

Restart backend.

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/me" -Headers $h
```

Expected: `{"user_label":"loger","is_cache_owner":true}`.

- [ ] **Step 4: Commit**

```powershell
git add backend/routes/me.py backend/main.py
git commit -m "feat(api): GET /api/me — expose user_label + cache owner flag"
```

---

### Task 12: Frontend `useMe` hook + Masthead badge

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Create: `frontend/src/hooks/useMe.ts`
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Add MeResponse type + api.me() in api.ts**

Edit `frontend/src/lib/api.ts`:

After the `HealthResponse` interface block (somewhere around line 61), add:

```typescript
// ---------------------------------------------------------------------------
// Me
// ---------------------------------------------------------------------------

export interface MeResponse {
  user_label: string;
  is_cache_owner: boolean;
}
```

In the `export const api = { ... }` object (line 342 起), after `health:` add:

```typescript
  me: () => fetchJSON<MeResponse>("/api/me"),
```

(放在 `health:` 下面那行即可。)

- [ ] **Step 2: Create useMe hook**

Create `frontend/src/hooks/useMe.ts`:

```typescript
import { useEffect, useState } from "react";
import { api, type MeResponse } from "../lib/api";

let cached: MeResponse | null = null;

export function useMe(): MeResponse | null {
  const [me, setMe] = useState<MeResponse | null>(cached);

  useEffect(() => {
    if (cached) return;
    let alive = true;
    api.me().then((res) => {
      cached = res;
      if (alive) setMe(res);
    }).catch(() => {
      // /api/me 失敗代表 backend 起不來，畫面上的 SystemStatus 會處理錯誤訊息，
      // 這裡靜默即可。
    });
    return () => { alive = false; };
  }, []);

  return me;
}
```

- [ ] **Step 3: Wire useMe into Masthead**

Edit `frontend/src/App.tsx`:

Add import at top (after existing imports):

```typescript
import { useMe } from "./hooks/useMe";
```

Replace the `Meta` function（lines 41-56）:

```typescript
function Meta() {
  const today = new Date().toLocaleDateString("zh-TW", {
    year: "numeric",
    month: "long",
    day: "numeric",
    weekday: "long",
  });

  return (
    <div className="flex flex-wrap items-center gap-x-3 text-xs text-ink-dim tracking-[0.3px]">
      <span>{today}</span>
      <span className="opacity-40">·</span>
      <span>盤中連續競價</span>
    </div>
  );
}
```

with:

```typescript
function Meta() {
  const me = useMe();
  const today = new Date().toLocaleDateString("zh-TW", {
    year: "numeric",
    month: "long",
    day: "numeric",
    weekday: "long",
  });

  return (
    <div className="flex flex-wrap items-center gap-x-3 text-xs text-ink-dim tracking-[0.3px]">
      {me && (
        <>
          <span className="text-ink">
            You are: <strong className="font-semibold">{me.user_label}</strong>
            {me.is_cache_owner && (
              <span className="ml-2 rounded border border-accent px-1.5 py-0.5 text-[10px] uppercase tracking-[1px] text-accent">
                cache owner
              </span>
            )}
          </span>
          <span className="opacity-40">·</span>
        </>
      )}
      <span>{today}</span>
      <span className="opacity-40">·</span>
      <span>盤中連續競價</span>
    </div>
  );
}
```

- [ ] **Step 4: Manual verification**

In a second terminal (with backend running):

```powershell
cd C:\side-project\trading-king\frontend
npm run dev
```

Open http://localhost:5173 — Masthead 右側應該顯示 `You are: loger [CACHE OWNER]`。

- [ ] **Step 5: Commit**

```powershell
git add frontend/src/lib/api.ts frontend/src/hooks/useMe.ts frontend/src/App.tsx
git commit -m "feat(ui): show USER_LABEL + cache owner badge in masthead"
```

---

## Phase E — Integration Probe + .env.example

### Task 13: probe_label_isolation.py

**Files:**
- Create: `backend/scripts/probe_label_isolation.py`

- [ ] **Step 1: Write the probe**

Create `backend/scripts/probe_label_isolation.py`:

```python
"""Probe: 兩個 label (alice / bob) 隔離煙霧。

直接打 Supabase service_role client，不走 backend HTTP — 模擬「alice 跟 bob 各自
跑 backend，連同一個 Supabase」的真實情境。

驗證：
  1. alice 寫 watchlist + strategies + active_signals + signals_log → bob 看不到
  2. bob 寫一筆 → alice 也看不到
  3. 結束清理乾淨

跑法（在 backend/ 內）：
  $env:USER_LABEL = "loger"   # 任何 valid label，本 probe 不從 env 拿 label
  .\.venv\Scripts\python.exe scripts\probe_label_isolation.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# 讓 import services.* 找得到（複製 scripts/ 慣例）
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

    # 用一檔 symbol 共用（symbols 表已有 2330）
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
    print("\n✓ probe_label_isolation passed")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the probe**

Run (from `backend/`):

```powershell
.\.venv\Scripts\python.exe scripts\probe_label_isolation.py
```

Expected output ends with `✓ probe_label_isolation passed`. 任何 FAIL line 都該 abort。

- [ ] **Step 3: Commit**

```powershell
git add backend/scripts/probe_label_isolation.py
git commit -m "test(probe): label isolation smoke for alice/bob across watchlist + strategies"
```

---

### Task 14: Update backend/.env.example

**Files:**
- Modify: `backend/.env.example`

- [ ] **Step 1: Add USER_LABEL + CACHE_JOB_OWNER**

Edit `backend/.env.example`. After line 30 (`BOUNDED_QUEUE_SIZE=5000`), append:

```
# ============================================================
# 本地版多人共用 Supabase 用（2026-05-13 加）
# ============================================================

# 你的識別 label：2~20 字，[a-z0-9_-]。私下跟群裡其他朋友協調避免撞名。
# 範例：USER_LABEL=loger
USER_LABEL=

# Cache job owner — 8:25 盤後 indicator cache 只會在 CACHE_JOB_OWNER == USER_LABEL
# 的這台 instance 跑。專案發起人請設成自己的 label，其他朋友請留空。
CACHE_JOB_OWNER=
```

- [ ] **Step 2: Commit**

```powershell
git add backend/.env.example
git commit -m "docs(env): document USER_LABEL + CACHE_JOB_OWNER"
```

**Checkpoint：PR 1 範圍結束。**

此時應該已完成：schema migration（已 apply）+ backend 全部 query 都帶 user_label + frontend 顯示 badge + 整合 probe 過了 + .env.example 文件齊全。可以在這裡開 PR 1 給自己 review，或繼續到 Phase F 分發資產。

---

## Phase F — Distribution

### Task 15: .gitignore wheel + remove from tracking

**Files:**
- Modify: `.gitignore`
- Remove tracking: `backend/wheels/fubon_neo-*.whl`

- [ ] **Step 1: Add wheel pattern to .gitignore**

Edit `.gitignore`，加一行（建議放在「Python」或檔尾）:

```
backend/wheels/*.whl
```

- [ ] **Step 2: Stop tracking the wheel**

Run (from repo root):

```powershell
git rm --cached backend/wheels/fubon_neo-2.2.8-cp37-abi3-win_amd64.whl
```

Expected: `rm 'backend/wheels/fubon_neo-2.2.8-cp37-abi3-win_amd64.whl'`. 檔案在 disk 上**保留**，只是 git 不再追蹤。

Verify:

```powershell
git status
```

應該看到 `.gitignore` modified + `backend/wheels/...whl` deleted (from index)。`Get-ChildItem backend\wheels\` 應該還看得到 wheel 在磁碟上。

- [ ] **Step 3: Commit**

```powershell
git add .gitignore
git commit -m "chore: untrack fubon wheel — users must download from fubon TradeAPI site"
```

Note: git history 仍會殘留舊 wheel commits。非敏感資料，先不處理；之後若有需要再用 BFG repo-cleaner。

---

### Task 16: install.ps1

**Files:**
- Create: `install.ps1`

- [ ] **Step 1: Write install.ps1**

Create `install.ps1` at repo root:

```powershell
# install.ps1 — 一鍵安裝 backend venv + frontend node_modules
# Run: .\install.ps1
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

Write-Host "=== treading-king 安裝 ===" -ForegroundColor Cyan

# ---------- 檢查 wheel ----------
$wheelDir = Join-Path $root "backend\wheels"
if (-not (Test-Path $wheelDir)) {
    New-Item -ItemType Directory -Path $wheelDir | Out-Null
}
$wheel = Get-ChildItem -Path $wheelDir -Filter "fubon_neo-*.whl" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($null -eq $wheel) {
    Write-Host ""
    Write-Host "ERROR: 找不到富邦 SDK wheel。" -ForegroundColor Red
    Write-Host "請至 https://www.fbs.com.tw/TradeAPI/docs/welcome 登入下載" -ForegroundColor Yellow
    Write-Host "  fubon_neo-X.Y.Z-cpNN-abi3-win_amd64.whl" -ForegroundColor Yellow
    Write-Host "放到 $wheelDir 後重跑本腳本。" -ForegroundColor Yellow
    exit 1
}
Write-Host "→ 找到 wheel: $($wheel.Name)" -ForegroundColor Green

# ---------- Backend venv ----------
Write-Host ""
Write-Host "→ 建立 backend venv" -ForegroundColor Cyan
Set-Location (Join-Path $root "backend")
if (-not (Test-Path .venv)) {
    python -m venv .venv
}
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install $wheel.FullName
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"

# ---------- Frontend deps ----------
Write-Host ""
Write-Host "→ 安裝 frontend deps" -ForegroundColor Cyan
Set-Location (Join-Path $root "frontend")
npm install

Set-Location $root
Write-Host ""
Write-Host "✓ 安裝完成。" -ForegroundColor Green
Write-Host "  下一步：" -ForegroundColor Cyan
Write-Host "  1. 編輯 backend\.env 與 frontend\.env（從 .env.example 複製）" -ForegroundColor Cyan
Write-Host "  2. 執行 .\start.ps1" -ForegroundColor Cyan
```

- [ ] **Step 2: Smoke test**

Run from repo root:

```powershell
.\install.ps1
```

Expected: 找到既有 wheel → 走完 pip install + npm install，最後 `✓ 安裝完成`。如果 venv 已存在 pip install 會直接 skip 已裝套件。

- [ ] **Step 3: Commit**

```powershell
git add install.ps1
git commit -m "feat(install): one-shot venv + npm install with fubon wheel check"
```

---

### Task 17: start.ps1

**Files:**
- Create: `start.ps1`

- [ ] **Step 1: Write start.ps1**

Create `start.ps1` at repo root:

```powershell
# start.ps1 — 開兩個 PowerShell 視窗同時起 backend + frontend
# Run: .\start.ps1
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

# ---------- 檢查 backend .env ----------
$envFile = Join-Path $root "backend\.env"
if (-not (Test-Path $envFile)) {
    Write-Host "ERROR: 找不到 backend\.env。請先從 backend\.env.example 複製並填內容。" -ForegroundColor Red
    exit 1
}
if (-not (Select-String -Path $envFile -Pattern "^USER_LABEL=.+" -Quiet)) {
    Write-Host "ERROR: backend\.env 缺 USER_LABEL（必填）。" -ForegroundColor Red
    Write-Host "請編輯 backend\.env 設一個 2~20 字 [a-z0-9_-] 的 label。" -ForegroundColor Yellow
    exit 1
}

# ---------- 檢查 frontend .env ----------
$feEnv = Join-Path $root "frontend\.env"
if (-not (Test-Path $feEnv)) {
    Write-Host "WARN: 找不到 frontend\.env。請從 frontend\.env.example 複製並填 VITE_BFF_API_KEY。" -ForegroundColor Yellow
}

# ---------- 啟動 backend ----------
Start-Process powershell -ArgumentList @(
  "-NoExit", "-Command",
  "Set-Location '$root\backend'; `$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000"
)

# ---------- 啟動 frontend ----------
Start-Process powershell -ArgumentList @(
  "-NoExit", "-Command",
  "Set-Location '$root\frontend'; npm run dev"
)

Write-Host ""
Write-Host "✓ Backend + Frontend 已開新視窗啟動。" -ForegroundColor Green
Write-Host "  瀏覽器開 http://localhost:5173" -ForegroundColor Cyan
Write-Host "  Masthead 應顯示 'You are: <你的 label>'，看不到的話檢查 backend 視窗 log。" -ForegroundColor Cyan
```

- [ ] **Step 2: Smoke test**

Run from repo root:

```powershell
.\start.ps1
```

Expected: 開出兩個新 PowerShell 視窗，backend 跑 uvicorn、frontend 跑 vite。瀏覽器 http://localhost:5173 看得到 Masthead label badge。完成後關掉兩個視窗即可。

- [ ] **Step 3: Commit**

```powershell
git add start.ps1
git commit -m "feat(start): launch backend + frontend in two PowerShell windows"
```

---

### Task 18: README rewrite (user-facing)

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Rewrite README**

Replace the entire contents of `README.md` with:

```markdown
# trading-king — 個人台股即時監控

整合富邦 Neo API 的本地版股票篩股 + 即時訊號工具。所有人在自己 Windows 電腦本機跑、共用同一個 Supabase 資料庫，靠 `.env` 的 `USER_LABEL` 隔離各自的自選清單 / 策略 / 訊號紀錄。

## 你需要先準備

- **作業系統**：Windows 10/11 x64（富邦 SDK 是 win_amd64 wheel）
- **富邦證券帳戶 + TradeAPI key**：到 https://www.fbs.com.tw/TradeAPI/docs/key-management 申請
- **Python 3.12**：到 https://www.python.org/downloads/ 下載，安裝時勾「Add Python to PATH」
- **Node.js 20+**：https://nodejs.org/
- **Git**：https://git-scm.com/download/win
- **Supabase service_role key**：私訊 `loger` 索取（**勿外流**，等同 admin 權限）
- **你自己選的 USER_LABEL**：2~20 字、`[a-z0-9_-]`。先在群裡喊一聲避免撞名（例如 `frank`、`bobo`）

## 安裝

1. clone 專案

```powershell
git clone https://github.com/<your-user>/trading-king.git C:\trading-king
cd C:\trading-king
```

2. 下載富邦 SDK wheel

到 https://www.fbs.com.tw/TradeAPI/docs/welcome 登入後找「下載 SDK」，下載最新 Windows x64 wheel（檔名類似 `fubon_neo-2.2.8-cp37-abi3-win_amd64.whl`），放到 `backend\wheels\` 目錄。

3. 設定 backend 環境變數

```powershell
Copy-Item backend\.env.example backend\.env
notepad backend\.env
```

至少要填：
- `FUBON_PERSONAL_ID` / `FUBON_API_KEY`（你的富邦帳號）
- `SUPABASE_URL` / `SUPABASE_KEY`（loger 給你的 URL + service_role key）
- `BFF_API_KEY`：隨便填一個秘密字串（前後端共用，例如 `python -c "import secrets; print(secrets.token_urlsafe(32))"` 生一個）
- `USER_LABEL`：你的 label（例如 `frank`）
- `CACHE_JOB_OWNER`：**留空**（只有 loger 會跑 cache job）

4. 設定 frontend 環境變數

```powershell
Copy-Item frontend\.env.example frontend\.env
notepad frontend\.env
```

填 `VITE_BFF_API_KEY=<跟 backend\.env 一樣那個>`。

5. 一鍵安裝

```powershell
.\install.ps1
```

預計跑 5~10 分鐘（pip install + npm install）。

## 啟動

```powershell
.\start.ps1
```

會開兩個 PowerShell 視窗（backend + frontend）。等 backend log 出現 `Startup done` + frontend 出現 `Local: http://localhost:5173`，瀏覽器打開 http://localhost:5173。

Masthead 右上角應該顯示 `You are: <你的 label>`，看到代表 `.env` 設對了。

## 常見問題

**Q. 我看得到別人的 watchlist 嗎？**
不會。watchlist / strategies / active_signals / signals_log 全部按 `USER_LABEL` 隔離。市場資料（symbols / 技術指標 / OHLC）才是共用。

**Q. 8:25 盤後的 indicator cache 是誰跑？**
只有 `CACHE_JOB_OWNER` 跟 `USER_LABEL` 相符的那台 backend 會跑——這台一律是 loger 的電腦。如果 loger 那天沒開機，當天 indicator 不會更新，最壞影響是隔天條件式篩股用的是前一交易日資料。

**Q. 我的富邦帳號會被別人用到嗎？**
不會。`.env` 只在你電腦上，富邦 SDK 在你本機 process 內跑。

**Q. 撞名怎辦？**
backend startup 會驗 label 格式，但**不**擋重複——同一個 label 兩個朋友跑會互相覆寫資料。在群組裡先講好。

**Q. 我是 Mac / Linux 怎辦？**
目前不支援。富邦只提供 Windows wheel。

**Q. service_role key 外洩會怎樣？**
拿到 key 的人可以讀寫整個 Supabase（所有人的資料）。請當作密碼保管：不要 commit、不要貼 Discord、不要存在公開雲端硬碟。

## 開發者文件

- `docs/superpowers/specs/2026-05-13-local-userlabel-design.md` — 本地版設計
- `docs/decisions/` — 重要決策紀錄
- `docs/superpowers/plans/` — 實作計劃

## 授權

Personal use only. No warranty. 富邦 SDK 屬富邦證券所有，請依其授權條款使用。
```

- [ ] **Step 2: Final smoke**

Open `README.md` in editor / on GitHub, 確認 markdown 渲染 OK（headings / code blocks）。

- [ ] **Step 3: Commit**

```powershell
git add README.md
git commit -m "docs(readme): rewrite for friends — install/start/.env steps + FAQ"
```

**Checkpoint：PR 2 範圍結束。**

可以在這裡開 PR 2 合 main，然後私下處理 PR 3（GitHub repo settings → 改 public + 私訊朋友 keys）——這步不在程式碼範圍。

---

## Self-Review Checklist

跑完所有 task 後，從 spec 對照：

- [x] §2 Schema 變動 → Task 4 ✓
- [x] §3 user_context.py → Task 1-2 ✓
- [x] §3 main.py 改動 → Task 3 + Task 11 ✓
- [x] §3 watchlist routes → Task 5 ✓
- [x] §3 strategies routes → Task 6 ✓
- [x] §3 active_signals routes → Task 7 ✓
- [x] §3 signals_history → Task 8 ✓
- [x] §3 cache.py 403 + health.py field → Task 9 ✓
- [x] §3 signal_engine + supabase_writer → Task 10 ✓
- [x] §3 /api/me → Task 11 ✓
- [x] §3 .env.example → Task 14 ✓
- [x] §4 frontend api.me + useMe + Masthead badge → Task 12 ✓
- [x] §4 install.ps1 / start.ps1 / README → Task 16-18 ✓
- [x] §4 .gitignore wheel → Task 15 ✓
- [x] §5 user_context unit tests → Task 1 ✓
- [x] §5 label_isolation integration → Task 13（probe 形式，非 pytest，依專案慣例）✓
- [x] §5 cache job 隔離手測 → Task 3 step 2-3 ✓
- [x] §5 migration smoke SQL → Task 4 step 3 ✓
- [x] Rollout PR 切分 → Phase A-E = PR 1，Phase F = PR 2，repo public = PR 3（人工）✓

如果某個 task 失敗 / 卡住，先停下來看 spec 對應段落，再決定是修 code 還是修 plan。
