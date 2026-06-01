# Local-First(移除 Supabase)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把後端的資料持久化從「共用雲端 Supabase」整批換成「本機 JSON/JSONL 檔」,新增匯出/匯入,並提供一次性遷移腳本 —— 對外 API / WS / 前端行為完全不變。

**Architecture:** 新增 `services/local_store/` 套件(三個聚焦元件:`ConfigStore` 個人設定、`SignalsLog` 訊號歷史、`MarketCache` 市場快取 + 一個 `LocalStore` facade)。所有 store 方法**同步且過程中不 await**,因此在單 process asyncio 下對事件迴圈天生原子,不需鎖。各 route/service 把 `get_supabase()` 換成 `get_local_store()`,**response 形狀逐欄位保留**。最後移除 `supabase` 依賴與 `USER_LABEL`。

**Tech Stack:** Python 3.12 / FastAPI / pytest(`asyncio_mode=auto`)/ httpx(symbols 爬蟲,已是依賴)/ 標準庫 `json`/`os`/`uuid`/`datetime`/`zoneinfo`。前端 React + TypeScript。

**設計鐵則(來自 spec §3):** 所有 `/api/*` 與 WS 事件契約不變;前端資料流不改(只加匯出/匯入 UI);零資料遺失;富邦相關完全不動。spec:`docs/superpowers/specs/2026-06-01-remove-supabase-local-first-design.md`。

---

## File Structure

**新增**
```
backend/services/local_store/
  __init__.py        # LocalStore facade + get_local_store()/reset_local_store()
  paths.py           # 路徑常數 + atomic_write_json() + read_json()
  config_store.py    # ConfigStore：bookmarks / active_signals / monitor_list / export / import
  signals_log.py     # SignalsLog：jsonl append / query / today_rows
  market_cache.py    # MarketCache：symbols / daily_ohlc / top_gainers(記憶體)
backend/services/signal_writer.py    # 取代 supabase_writer.py 的 JSONL appender
backend/routes/config_io.py          # GET /api/config/export、POST /api/config/import
backend/scripts/__init__.py
backend/scripts/migrate_supabase_to_local.py
backend/services/lifecycle_sync.py   # resync_from_config()(啟動與匯入共用)
backend/tests/test_config_store.py
backend/tests/test_signals_log.py
backend/tests/test_market_cache.py
backend/tests/test_config_io.py
backend/tests/test_migrate_supabase.py
frontend/src/components/ConfigIODialog.tsx   # 匯出/匯入 UI
```

**修改**
```
backend/.gitignore（或 repo 根 .gitignore）
backend/routes/{symbols,bookmarks,watchlist,monitor_list,active_signals,signals_history}.py
backend/services/{cdp,camarilla,signal_engine}.py
backend/jobs/top_gainers_scheduler.py
backend/main.py
backend/tests/conftest.py
backend/pyproject.toml
backend/.env.example
frontend/src/lib/api.ts
frontend/src/components/BookmarkManageDialog.tsx
```

**刪除(Phase 6,遷移驗證後)**
```
backend/services/supabase_client.py
backend/services/supabase_writer.py
backend/services/user_context.py
```

---

## LocalStore 公開 API(契約 —— 後續任務都引用這些名稱)

```python
store = get_local_store()          # 單例
store.init()                       # 載入三個子 store(seed/掃描/讀快取)

# --- store.config: ConfigStore ---
store.config.list_groups() -> list[dict]                 # 使用者書籤群組(不含系統)
store.config.create_group(name, sort_order=0) -> dict
store.config.update_group(group_id, *, name=None, sort_order=None) -> dict | None
store.config.delete_group(group_id) -> bool              # cascade 刪 items
store.config.list_items(group_id) -> list[dict]
store.config.item_counts() -> dict[str, int]             # group_id -> item 數
store.config.add_item(group_id, symbol, note=None) -> dict
store.config.remove_item(group_id, symbol) -> bool
store.config.list_active_signals(enabled_only=False) -> list[dict]
store.config.get_active_signal(sig_id) -> dict | None
store.config.create_active_signal(payload: dict) -> dict
store.config.update_active_signal(sig_id, patch: dict) -> dict | None
store.config.delete_active_signal(sig_id) -> bool
store.config.disable_all_active_signals() -> int         # backpressure；回停用筆數
store.config.list_monitor() -> list[dict]
store.config.add_monitor(symbol) -> dict
store.config.remove_monitor(symbol) -> bool
store.config.export_config() -> dict                     # 蓋 exported_at + schema_version
store.config.import_config(data: dict) -> None           # 驗 schema → 備份 → 取代 → persist

# --- store.signals: SignalsLog ---
store.signals.append(row: dict) -> dict                  # 配 id + triggered_at,寫 jsonl,更新今日 counter
store.signals.query(*, symbol=None, active_signal_id=None, since=None, limit=200) -> list[dict]
store.signals.today_rows() -> list[dict]                 # 今日(Asia/Taipei)所有 row

# --- store.market: MarketCache ---
store.market.search(search: str, limit: int) -> list[dict]
store.market.replace_symbols(rows: list[dict]) -> None   # 寫 symbols.json
store.market.symbols_loaded() -> bool
store.market.has_symbol(symbol: str) -> bool
store.market.get_latest_daily_ohlc(symbol: str) -> dict | None
store.market.upsert_daily_ohlc(rows: list[dict]) -> None # 寫 daily_ohlc.json
store.market.get_top_gainers() -> list[dict]
store.market.replace_top_gainers(rows: list[dict]) -> None  # 記憶體
store.market.top_gainers_count() -> int
```

---

## Phase 0 — Scaffolding

### Task 0: 路徑模組 + .gitignore

**Files:**
- Create: `backend/services/local_store/__init__.py`(本任務先留空 docstring,Task 9 補)
- Create: `backend/services/local_store/paths.py`
- Modify: 根目錄 `.gitignore`

- [ ] **Step 1: 寫 `paths.py`**

```python
"""本機儲存的路徑常數與原子寫檔工具。

資料夾可用環境變數 TK_DATA_DIR 覆寫(測試用 tmp_path);預設 backend/data。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

_BACKEND_DIR = Path(__file__).resolve().parents[2]  # .../backend


def data_dir() -> Path:
    raw = os.getenv("TK_DATA_DIR", "").strip()
    return Path(raw) if raw else _BACKEND_DIR / "data"


def config_path() -> Path:
    return data_dir() / "config.json"


def signals_log_path() -> Path:
    return data_dir() / "signals_log.jsonl"


def cache_dir() -> Path:
    return data_dir() / "cache"


def symbols_path() -> Path:
    return cache_dir() / "symbols.json"


def daily_ohlc_path() -> Path:
    return cache_dir() / "daily_ohlc.json"


def atomic_write_json(path: Path, payload: Any) -> None:
    """寫暫存檔 + os.replace,避免寫到一半壞檔(Windows 同磁區 replace 為原子)。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
```

- [ ] **Step 2: `.gitignore` 加本機資料夾**

在根 `.gitignore` 末尾加:
```
# 本機儲存(每台機器各一份,不進版控)
backend/data/
```

- [ ] **Step 3: Commit**

```bash
git add backend/services/local_store/__init__.py backend/services/local_store/paths.py .gitignore
git commit -m "feat(local-store): 路徑常數 + 原子寫檔工具 + gitignore data 夾"
```

---

## Phase 1 — local_store 套件(隔離 TDD)

### Task 1: ConfigStore — 載入 / seed / 書籤

**Files:**
- Create: `backend/services/local_store/config_store.py`
- Test: `backend/tests/test_config_store.py`

- [ ] **Step 1: 寫失敗測試**

```python
# backend/tests/test_config_store.py
from services.local_store.config_store import ConfigStore


def test_fresh_store_seeds_default_bookmark(tmp_path):
    cfg = ConfigStore(tmp_path / "config.json")
    cfg.load()
    groups = cfg.list_groups()
    assert len(groups) == 1
    assert groups[0]["name"] == "自選"
    assert groups[0]["is_system"] is False


def test_create_and_list_group_persists(tmp_path):
    path = tmp_path / "config.json"
    cfg = ConfigStore(path)
    cfg.load()
    g = cfg.create_group("強勢股", sort_order=1)
    assert g["id"]
    # 重新載入同一檔 → 仍在(證明有寫穿)
    cfg2 = ConfigStore(path)
    cfg2.load()
    assert any(x["name"] == "強勢股" for x in cfg2.list_groups())


def test_delete_group_cascades_items(tmp_path):
    # 為何重要:孤兒 item 會讓前端出現「幽靈股票」
    cfg = ConfigStore(tmp_path / "config.json")
    cfg.load()
    g = cfg.create_group("X")
    cfg.add_item(g["id"], "2330")
    assert cfg.delete_group(g["id"]) is True
    assert cfg.list_items(g["id"]) == []


def test_add_item_dedup_and_counts(tmp_path):
    cfg = ConfigStore(tmp_path / "config.json")
    cfg.load()
    g = cfg.create_group("X")
    cfg.add_item(g["id"], "2330")
    cfg.add_item(g["id"], "2330")  # 同檔不重複
    assert len(cfg.list_items(g["id"])) == 1
    assert cfg.item_counts()[g["id"]] == 1
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend; python -m pytest tests/test_config_store.py -v`
Expected: FAIL（`ModuleNotFoundError: ... config_store`）

- [ ] **Step 3: 寫 `config_store.py`(載入 + seed + 書籤)**

```python
"""個人設定的本機儲存:書籤 / 訊號規則 / 監聽清單。

所有方法同步且過程中不 await → 單 process asyncio 下對事件迴圈天生原子,不需鎖。
每次變動都寫穿到 config.json(原子替換)。
"""
from __future__ import annotations

import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services.local_store.paths import SCHEMA_VERSION, atomic_write_json, read_json


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


def _empty_config() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "exported_at": None,
        "bookmark_groups": [],
        "watchlist_items": [],
        "active_signals": [],
        "monitor_list": [],
    }


class ConfigStore:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._data: dict[str, Any] = _empty_config()

    # ---- lifecycle ----
    def load(self) -> None:
        self._data = read_json(self._path, None) or _empty_config()
        # 補欄位(向前相容)
        for k, v in _empty_config().items():
            self._data.setdefault(k, v)
        self._seed_defaults()

    def _seed_defaults(self) -> None:
        """無任何使用者書籤時建「自選」。"""
        if not any(not g.get("is_system") for g in self._data["bookmark_groups"]):
            self._data["bookmark_groups"].append({
                "id": _new_id(), "name": "自選", "sort_order": 0,
                "is_system": False, "source_type": None, "created_at": _now_iso(),
            })
            self._persist()

    def _persist(self) -> None:
        atomic_write_json(self._path, self._data)

    # ---- bookmarks: groups ----
    def list_groups(self) -> list[dict]:
        return [g for g in self._data["bookmark_groups"] if not g.get("is_system")]

    def create_group(self, name: str, sort_order: int = 0) -> dict:
        g = {"id": _new_id(), "name": name, "sort_order": sort_order,
             "is_system": False, "source_type": None, "created_at": _now_iso()}
        self._data["bookmark_groups"].append(g)
        self._persist()
        return g

    def update_group(self, group_id: str, *, name: str | None = None,
                     sort_order: int | None = None) -> dict | None:
        for g in self._data["bookmark_groups"]:
            if g["id"] == group_id:
                if name is not None:
                    g["name"] = name
                if sort_order is not None:
                    g["sort_order"] = sort_order
                self._persist()
                return g
        return None

    def delete_group(self, group_id: str) -> bool:
        before = len(self._data["bookmark_groups"])
        self._data["bookmark_groups"] = [
            g for g in self._data["bookmark_groups"] if g["id"] != group_id
        ]
        self._data["watchlist_items"] = [
            it for it in self._data["watchlist_items"] if it["group_id"] != group_id
        ]
        if len(self._data["bookmark_groups"]) != before:
            self._persist()
            return True
        return False

    # ---- bookmarks: items ----
    def list_items(self, group_id: str) -> list[dict]:
        return [it for it in self._data["watchlist_items"] if it["group_id"] == group_id]

    def item_counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for it in self._data["watchlist_items"]:
            out[it["group_id"]] = out.get(it["group_id"], 0) + 1
        return out

    def add_item(self, group_id: str, symbol: str, note: str | None = None) -> dict:
        for it in self._data["watchlist_items"]:
            if it["group_id"] == group_id and it["symbol"] == symbol:
                return it  # 同 (group, symbol) 不重複
        it = {"id": _new_id(), "group_id": group_id, "symbol": symbol,
              "added_at": _now_iso(), "note": note}
        self._data["watchlist_items"].append(it)
        self._persist()
        return it

    def remove_item(self, group_id: str, symbol: str) -> bool:
        before = len(self._data["watchlist_items"])
        self._data["watchlist_items"] = [
            it for it in self._data["watchlist_items"]
            if not (it["group_id"] == group_id and it["symbol"] == symbol)
        ]
        if len(self._data["watchlist_items"]) != before:
            self._persist()
            return True
        return False
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd backend; python -m pytest tests/test_config_store.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: Commit**

```bash
git add backend/services/local_store/config_store.py backend/tests/test_config_store.py
git commit -m "feat(local-store): ConfigStore 載入/seed/書籤 CRUD(TDD)"
```

---

### Task 2: ConfigStore — active_signals + monitor_list

**Files:**
- Modify: `backend/services/local_store/config_store.py`
- Test: `backend/tests/test_config_store.py`

- [ ] **Step 1: 加失敗測試**

```python
def test_active_signal_crud(tmp_path):
    cfg = ConfigStore(tmp_path / "config.json")
    cfg.load()
    s = cfg.create_active_signal({
        "name": "突破", "filter_json": {"op": ">"}, "scope": {"type": "watchlist"},
        "cooldown_seconds": 1800, "enabled": True, "notify_discord": True,
    })
    assert s["id"] and s["created_at"]
    assert cfg.update_active_signal(s["id"], {"enabled": False})["enabled"] is False
    assert cfg.list_active_signals(enabled_only=True) == []
    assert cfg.delete_active_signal(s["id"]) is True


def test_disable_all_active_signals_returns_count(tmp_path):
    cfg = ConfigStore(tmp_path / "config.json")
    cfg.load()
    cfg.create_active_signal({"name": "a", "filter_json": {}, "scope": {},
                              "cooldown_seconds": 60, "enabled": True, "notify_discord": True})
    cfg.create_active_signal({"name": "b", "filter_json": {}, "scope": {},
                              "cooldown_seconds": 60, "enabled": True, "notify_discord": True})
    assert cfg.disable_all_active_signals() == 2
    assert cfg.list_active_signals(enabled_only=True) == []


def test_monitor_list_add_remove_dedup(tmp_path):
    cfg = ConfigStore(tmp_path / "config.json")
    cfg.load()
    cfg.add_monitor("2330")
    cfg.add_monitor("2330")
    assert [m["symbol"] for m in cfg.list_monitor()] == ["2330"]
    assert cfg.remove_monitor("2330") is True
    assert cfg.list_monitor() == []
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend; python -m pytest tests/test_config_store.py -k "active_signal or monitor" -v`
Expected: FAIL（`AttributeError: ... create_active_signal`）

- [ ] **Step 3: 在 `ConfigStore` 內加方法**

```python
    # ---- active_signals ----
    def list_active_signals(self, enabled_only: bool = False) -> list[dict]:
        sigs = self._data["active_signals"]
        return [s for s in sigs if s["enabled"]] if enabled_only else list(sigs)

    def get_active_signal(self, sig_id: str) -> dict | None:
        return next((s for s in self._data["active_signals"] if s["id"] == sig_id), None)

    def create_active_signal(self, payload: dict) -> dict:
        s = {
            "id": _new_id(),
            "name": payload["name"],
            "filter_json": payload.get("filter_json", {}),
            "scope": payload.get("scope", {}),
            "cooldown_seconds": payload.get("cooldown_seconds", 1800),
            "enabled": payload.get("enabled", True),
            "notify_discord": payload.get("notify_discord", True),
            "created_at": _now_iso(),
        }
        self._data["active_signals"].append(s)
        self._persist()
        return s

    def update_active_signal(self, sig_id: str, patch: dict) -> dict | None:
        for s in self._data["active_signals"]:
            if s["id"] == sig_id:
                for k in ("name", "filter_json", "scope", "cooldown_seconds",
                          "enabled", "notify_discord"):
                    if k in patch:
                        s[k] = patch[k]
                self._persist()
                return s
        return None

    def delete_active_signal(self, sig_id: str) -> bool:
        before = len(self._data["active_signals"])
        self._data["active_signals"] = [
            s for s in self._data["active_signals"] if s["id"] != sig_id
        ]
        if len(self._data["active_signals"]) != before:
            self._persist()
            return True
        return False

    def disable_all_active_signals(self) -> int:
        n = 0
        for s in self._data["active_signals"]:
            if s["enabled"]:
                s["enabled"] = False
                n += 1
        if n:
            self._persist()
        return n

    # ---- monitor_list ----
    def list_monitor(self) -> list[dict]:
        return list(self._data["monitor_list"])

    def add_monitor(self, symbol: str) -> dict:
        for m in self._data["monitor_list"]:
            if m["symbol"] == symbol:
                return m
        m = {"symbol": symbol, "added_at": _now_iso()}
        self._data["monitor_list"].append(m)
        self._persist()
        return m

    def remove_monitor(self, symbol: str) -> bool:
        before = len(self._data["monitor_list"])
        self._data["monitor_list"] = [
            m for m in self._data["monitor_list"] if m["symbol"] != symbol
        ]
        if len(self._data["monitor_list"]) != before:
            self._persist()
            return True
        return False
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd backend; python -m pytest tests/test_config_store.py -v`
Expected: PASS（全部）

- [ ] **Step 5: Commit**

```bash
git add backend/services/local_store/config_store.py backend/tests/test_config_store.py
git commit -m "feat(local-store): ConfigStore active_signals + monitor_list(TDD)"
```

---

### Task 3: ConfigStore — export / import

**Files:**
- Modify: `backend/services/local_store/config_store.py`
- Test: `backend/tests/test_config_store.py`

- [ ] **Step 1: 加失敗測試**

```python
def test_export_then_import_replaces(tmp_path):
    src = ConfigStore(tmp_path / "a.json")
    src.load()
    src.create_group("帶走的")
    snapshot = src.export_config()
    assert snapshot["schema_version"] == 1
    assert snapshot["exported_at"]

    dst = ConfigStore(tmp_path / "b.json")
    dst.load()
    dst.create_group("本機舊的")
    dst.import_config(snapshot)
    names = [g["name"] for g in dst.list_groups()]
    assert "帶走的" in names
    assert "本機舊的" not in names  # 整包取代


def test_import_backs_up_old_file(tmp_path):
    # 為何重要:誤匯入可從備份救回
    path = tmp_path / "config.json"
    cfg = ConfigStore(path)
    cfg.load()
    cfg.create_group("舊")
    cfg.import_config({"schema_version": 1, "bookmark_groups": [], "watchlist_items": [],
                       "active_signals": [], "monitor_list": []})
    backups = list(tmp_path.glob("config.backup-*.json"))
    assert len(backups) == 1


def test_import_rejects_bad_schema(tmp_path):
    cfg = ConfigStore(tmp_path / "config.json")
    cfg.load()
    import pytest
    with pytest.raises(ValueError):
        cfg.import_config({"schema_version": 999})
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend; python -m pytest tests/test_config_store.py -k "export or import" -v`
Expected: FAIL（`AttributeError: export_config`）

- [ ] **Step 3: 加 export/import 方法**

```python
    # ---- export / import ----
    def export_config(self) -> dict:
        snap = {k: self._data[k] for k in
                ("schema_version", "bookmark_groups", "watchlist_items",
                 "active_signals", "monitor_list")}
        snap["schema_version"] = SCHEMA_VERSION
        snap["exported_at"] = _now_iso()
        return snap

    def import_config(self, data: dict) -> None:
        if data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version: {data.get('schema_version')}")
        # 備份現有檔
        if self._path.exists():
            n = 1
            while (bak := self._path.with_name(f"config.backup-{n}.json")).exists():
                n += 1
            shutil.copy2(self._path, bak)
        new = _empty_config()
        for k in ("bookmark_groups", "watchlist_items", "active_signals", "monitor_list"):
            new[k] = data.get(k, [])
        self._data = new
        self._seed_defaults()  # 匯入空設定也要有「自選」
        self._persist()
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd backend; python -m pytest tests/test_config_store.py -v`
Expected: PASS（全部）

- [ ] **Step 5: Commit**

```bash
git add backend/services/local_store/config_store.py backend/tests/test_config_store.py
git commit -m "feat(local-store): ConfigStore export/import(取代+備份+驗 schema)"
```

---

### Task 4: SignalsLog(JSONL)

**Files:**
- Create: `backend/services/local_store/signals_log.py`
- Test: `backend/tests/test_signals_log.py`

- [ ] **Step 1: 寫失敗測試**

```python
# backend/tests/test_signals_log.py
from datetime import datetime, timedelta, timezone

from services.local_store.signals_log import SignalsLog


def test_append_assigns_id_and_persists(tmp_path):
    log = SignalsLog(tmp_path / "signals_log.jsonl")
    log.load()
    r = log.append({"active_signal_id": "a", "symbol": "2330",
                    "trigger_price": 925.0, "trigger_volume": 10, "context_json": {}})
    assert r["id"] == 1 and r["triggered_at"]
    # 重新載入 → id 從 2 起跳
    log2 = SignalsLog(tmp_path / "signals_log.jsonl")
    log2.load()
    assert log2.append({"active_signal_id": "a", "symbol": "2330"})["id"] == 2


def test_query_filters_and_limits(tmp_path):
    log = SignalsLog(tmp_path / "signals_log.jsonl")
    log.load()
    log.append({"active_signal_id": "a", "symbol": "2330"})
    log.append({"active_signal_id": "b", "symbol": "2454"})
    assert [r["symbol"] for r in log.query(symbol="2330")] == ["2330"]
    assert len(log.query(limit=1)) == 1


def test_today_rows_excludes_yesterday(tmp_path):
    # 為何重要:今日觸發次數必須以 Asia/Taipei 日界線切
    log = SignalsLog(tmp_path / "signals_log.jsonl")
    log.load()
    old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    log.append({"active_signal_id": "a", "symbol": "2330", "triggered_at": old})
    log.append({"active_signal_id": "a", "symbol": "2454"})  # 今天
    syms = [r["symbol"] for r in log.today_rows()]
    assert "2454" in syms and "2330" not in syms
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend; python -m pytest tests/test_signals_log.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 寫 `signals_log.py`**

```python
"""訊號觸發歷史(append-only JSONL)。同步、不 await → 對事件迴圈原子。"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

_TPE = ZoneInfo("Asia/Taipei")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SignalsLog:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._next_id = 1

    def load(self) -> None:
        self._next_id = 1
        if not self._path.exists():
            return
        with open(self._path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rid = json.loads(line).get("id", 0)
                    self._next_id = max(self._next_id, int(rid) + 1)
                except (ValueError, TypeError):
                    continue

    def append(self, row: dict) -> dict:
        rec = dict(row)
        rec["id"] = self._next_id
        self._next_id += 1
        rec.setdefault("triggered_at", _now_iso())
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    def _read_all(self) -> list[dict]:
        if not self._path.exists():
            return []
        out: list[dict] = []
        with open(self._path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        continue
        return out

    def query(self, *, symbol: str | None = None, active_signal_id: str | None = None,
              since: str | None = None, limit: int = 200) -> list[dict]:
        rows = self._read_all()
        if symbol:
            rows = [r for r in rows if r.get("symbol") == symbol]
        if active_signal_id:
            rows = [r for r in rows if r.get("active_signal_id") == active_signal_id]
        if since:
            rows = [r for r in rows if (r.get("triggered_at") or "") >= since]
        rows.sort(key=lambda r: r.get("triggered_at") or "", reverse=True)
        return rows[:limit]

    def today_rows(self) -> list[dict]:
        today = datetime.now(_TPE).date()
        out: list[dict] = []
        for r in self._read_all():
            ts = r.get("triggered_at")
            if not ts:
                continue
            try:
                dt = datetime.fromisoformat(ts)
            except ValueError:
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt.astimezone(_TPE).date() == today:
                out.append(r)
        return out
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd backend; python -m pytest tests/test_signals_log.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/services/local_store/signals_log.py backend/tests/test_signals_log.py
git commit -m "feat(local-store): SignalsLog jsonl append/query/today_rows(TDD)"
```

---

### Task 5: MarketCache(symbols / daily_ohlc / top_gainers)

**Files:**
- Create: `backend/services/local_store/market_cache.py`
- Test: `backend/tests/test_market_cache.py`

- [ ] **Step 1: 寫失敗測試**

```python
# backend/tests/test_market_cache.py
from services.local_store.market_cache import MarketCache


def test_replace_and_search_symbols(tmp_path):
    mc = MarketCache(tmp_path / "symbols.json", tmp_path / "daily_ohlc.json")
    mc.load()
    assert mc.symbols_loaded() is False
    mc.replace_symbols([
        {"symbol": "2330", "name": "台積電", "market": "TWSE", "is_etf": False, "is_active": True},
        {"symbol": "2454", "name": "聯發科", "market": "TWSE", "is_etf": False, "is_active": True},
        {"symbol": "0050", "name": "元大台灣50", "market": "TWSE", "is_etf": True, "is_active": True},
    ])
    assert mc.symbols_loaded() is True
    assert mc.has_symbol("2330") is True
    # 前綴搜代碼
    assert [r["symbol"] for r in mc.search("23", 10)] == ["2330"]
    # 名稱模糊
    assert [r["symbol"] for r in mc.search("聯發", 10)] == ["2454"]
    # 重新載入仍在(寫穿)
    mc2 = MarketCache(tmp_path / "symbols.json", tmp_path / "daily_ohlc.json")
    mc2.load()
    assert mc2.has_symbol("0050")


def test_daily_ohlc_upsert_keeps_latest(tmp_path):
    mc = MarketCache(tmp_path / "symbols.json", tmp_path / "daily_ohlc.json")
    mc.load()
    mc.upsert_daily_ohlc([{"symbol": "2330", "date": "2026-05-20",
                           "high": 1.0, "low": 1.0, "close": 1.0}])
    mc.upsert_daily_ohlc([{"symbol": "2330", "date": "2026-05-21",
                           "high": 2.0, "low": 2.0, "close": 2.0}])
    assert mc.get_latest_daily_ohlc("2330")["date"] == "2026-05-21"
    assert mc.get_latest_daily_ohlc("9999") is None


def test_top_gainers_in_memory(tmp_path):
    mc = MarketCache(tmp_path / "symbols.json", tmp_path / "daily_ohlc.json")
    mc.load()
    mc.replace_top_gainers([{"symbol": "2330", "change_pct": 5.0, "rank": 1}])
    assert mc.top_gainers_count() == 1
    assert mc.get_top_gainers()[0]["symbol"] == "2330"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend; python -m pytest tests/test_market_cache.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 寫 `market_cache.py`**

```python
"""市場資料本機快取:symbols(寫檔)/ daily_ohlc(寫檔)/ top_gainers(記憶體)。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from services.local_store.paths import atomic_write_json, read_json


class MarketCache:
    def __init__(self, symbols_path: Path, daily_ohlc_path: Path) -> None:
        self._symbols_path = Path(symbols_path)
        self._daily_ohlc_path = Path(daily_ohlc_path)
        self._symbols: list[dict] = []
        self._symbol_set: set[str] = set()
        self._daily_ohlc: dict[str, dict] = {}   # symbol -> 最新一筆
        self._top_gainers: list[dict] = []

    def load(self) -> None:
        self._symbols = read_json(self._symbols_path, []) or []
        self._symbol_set = {s["symbol"] for s in self._symbols}
        self._daily_ohlc = read_json(self._daily_ohlc_path, {}) or {}

    # ---- symbols ----
    def symbols_loaded(self) -> bool:
        return len(self._symbols) > 0

    def has_symbol(self, symbol: str) -> bool:
        return symbol in self._symbol_set

    def replace_symbols(self, rows: list[dict]) -> None:
        self._symbols = rows
        self._symbol_set = {s["symbol"] for s in rows}
        atomic_write_json(self._symbols_path, rows)

    def search(self, search: str, limit: int) -> list[dict]:
        s = (search or "").strip()
        rows = [r for r in self._symbols if r.get("is_active", True)]
        if s:
            rows = [r for r in rows
                    if r["symbol"].startswith(s) or s in r.get("name", "")]
        rows.sort(key=lambda r: r["symbol"])
        return [{"symbol": r["symbol"], "name": r["name"],
                 "market": r["market"], "is_etf": r.get("is_etf", False)}
                for r in rows[:limit]]

    # ---- daily_ohlc ----
    def get_latest_daily_ohlc(self, symbol: str) -> dict | None:
        return self._daily_ohlc.get(symbol)

    def upsert_daily_ohlc(self, rows: list[dict]) -> None:
        changed = False
        for r in rows:
            sym = r["symbol"]
            cur = self._daily_ohlc.get(sym)
            if cur is None or r["date"] >= cur["date"]:
                self._daily_ohlc[sym] = {"date": r["date"], "high": r["high"],
                                         "low": r["low"], "close": r["close"]}
                changed = True
        if changed:
            atomic_write_json(self._daily_ohlc_path, self._daily_ohlc)

    # ---- top_gainers(記憶體,每分鐘重算) ----
    def get_top_gainers(self) -> list[dict]:
        return list(self._top_gainers)

    def replace_top_gainers(self, rows: list[dict]) -> None:
        self._top_gainers = list(rows)

    def top_gainers_count(self) -> int:
        return len(self._top_gainers)
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd backend; python -m pytest tests/test_market_cache.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/services/local_store/market_cache.py backend/tests/test_market_cache.py
git commit -m "feat(local-store): MarketCache symbols/daily_ohlc/top_gainers(TDD)"
```

---

### Task 6: LocalStore facade + 單例 + 測試 fixture

**Files:**
- Modify: `backend/services/local_store/__init__.py`
- Modify: `backend/tests/conftest.py`

- [ ] **Step 1: 寫 `__init__.py`**

```python
"""本機儲存 facade。

用法:
    store = get_local_store()
    store.init()
    store.config.list_groups()
    store.signals.append({...})
    store.market.search("23", 20)
"""
from __future__ import annotations

from pathlib import Path

from services.local_store.config_store import ConfigStore
from services.local_store.market_cache import MarketCache
from services.local_store.paths import (
    config_path, daily_ohlc_path, signals_log_path, symbols_path,
)
from services.local_store.signals_log import SignalsLog


class LocalStore:
    def __init__(self) -> None:
        self.config = ConfigStore(config_path())
        self.signals = SignalsLog(signals_log_path())
        self.market = MarketCache(symbols_path(), daily_ohlc_path())

    def init(self) -> None:
        self.config.load()
        self.signals.load()
        self.market.load()


_store: LocalStore | None = None


def get_local_store() -> LocalStore:
    global _store
    if _store is None:
        _store = LocalStore()
    return _store


def reset_local_store() -> LocalStore:
    """測試用:依目前 TK_DATA_DIR 重建單例。"""
    global _store
    _store = LocalStore()
    return _store
```

- [ ] **Step 2: conftest 改用 tmp data dir(autouse fixture)**

把 `backend/tests/conftest.py` 的 `os.environ.setdefault("USER_LABEL", "test")` 保留(Phase 3 才移除),並**新增**:

```python
import pytest
from services.local_store import reset_local_store


@pytest.fixture(autouse=True)
def local_store_tmp(tmp_path, monkeypatch):
    """每個測試指向獨立 tmp data dir,並重建已初始化的 LocalStore 單例。"""
    monkeypatch.setenv("TK_DATA_DIR", str(tmp_path))
    store = reset_local_store()
    store.init()
    return store
```

- [ ] **Step 3: 跑既有測試確認沒被弄壞**

Run: `cd backend; python -m pytest tests/test_config_store.py tests/test_signals_log.py tests/test_market_cache.py -v`
Expected: PASS（facade 不影響各元件直接測試;新 fixture 對它們無害）

- [ ] **Step 4: Commit**

```bash
git add backend/services/local_store/__init__.py backend/tests/conftest.py
git commit -m "feat(local-store): LocalStore facade + 單例 + 測試 tmp data dir fixture"
```

---

## Phase 2 — 把後端消費端接到 local_store(行為保持)

> 原則:每個 route/service 把 `get_supabase()` + `sb.client.table(...)` 換成 `get_local_store()` 的對應方法,**response 形狀與副作用一字不差**。逐檔換、逐檔測。

### Task 7: signal_writer.py(取代 supabase_writer)

**Files:**
- Create: `backend/services/signal_writer.py`
- Test: `backend/tests/test_signal_writer.py`

**背景**:`signal_engine` 目前呼叫 `get_supabase_writer().append(row)`(含 `user_label`);`main.py` 呼叫 `writer.start()`。本機 append 不需批次,直接寫 `store.signals`。

- [ ] **Step 1: 寫失敗測試**

```python
# backend/tests/test_signal_writer.py
from services.local_store import get_local_store
from services.signal_writer import get_signal_writer


def test_append_writes_to_signals_log(local_store_tmp):
    w = get_signal_writer()
    w.append({"active_signal_id": "a", "symbol": "2330",
              "trigger_price": 1.0, "trigger_volume": 1, "context_json": {}})
    rows = get_local_store().signals.query(symbol="2330")
    assert len(rows) == 1 and rows[0]["active_signal_id"] == "a"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend; python -m pytest tests/test_signal_writer.py -v`
Expected: FAIL（`ModuleNotFoundError: ... signal_writer`)

- [ ] **Step 3: 寫 `signal_writer.py`**

```python
"""訊號歷史寫入器 — 直接 append 到本機 jsonl(取代 supabase_writer 的批次邏輯)。

保留 start()/stop() 以維持 main.py 生命週期介面(本機無需背景 flush,為 no-op)。
"""
from __future__ import annotations

from typing import Any

from services.local_store import get_local_store


class SignalWriter:
    async def start(self) -> None:  # noqa: D401 - 介面相容,no-op
        return

    async def stop(self) -> None:
        return

    def append(self, row: dict[str, Any]) -> None:
        get_local_store().signals.append(row)


_writer: SignalWriter | None = None


def get_signal_writer() -> SignalWriter:
    global _writer
    if _writer is None:
        _writer = SignalWriter()
    return _writer
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd backend; python -m pytest tests/test_signal_writer.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/services/signal_writer.py backend/tests/test_signal_writer.py
git commit -m "feat(local-store): signal_writer 直寫 jsonl(取代 supabase_writer)"
```

---

### Task 8: symbols route → MarketCache + 啟動 bootstrap

**Files:**
- Modify: `backend/routes/symbols.py`
- Test: `backend/tests/test_symbols_route.py`(新增)

**保留**:`refresh_symbols()` 內整段 httpx 爬 ISIN/OpenAPI 的邏輯**完全不動**(那是公開來源,與富邦/Supabase 無關)。只改「終點」與「搜尋來源」。

- [ ] **Step 1: 寫失敗測試**

```python
# backend/tests/test_symbols_route.py
from fastapi.testclient import TestClient
from main import app
from services.local_store import get_local_store

client = TestClient(app)


def test_search_reads_from_market_cache(local_store_tmp):
    get_local_store().market.replace_symbols([
        {"symbol": "2330", "name": "台積電", "market": "TWSE", "is_etf": False, "is_active": True},
    ])
    r = client.get("/api/symbols", params={"search": "23", "limit": 10})
    assert r.status_code == 200
    assert r.json() == {"results": [
        {"symbol": "2330", "name": "台積電", "market": "TWSE", "is_etf": False}]}
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend; python -m pytest tests/test_symbols_route.py -v`
Expected: FAIL（目前 `search_symbols` 走 supabase,回 503）

- [ ] **Step 3: 改 `routes/symbols.py`**

把第 89 行 `from services.supabase_client import get_supabase` 改為 `from services.local_store import get_local_store`。

`search_symbols`(原 95-117 行)整段換成:
```python
@router.get("/api/symbols")
async def search_symbols(
    search: str = Query("", description="Prefix match on symbol, contains match on name"),
    limit: int = Query(20, ge=1, le=100),
) -> dict:
    """搜 symbol（給 Monitor 搜尋框 / 加入自選股用）。"""
    results = get_local_store().market.search(search, limit)
    return {"results": results}
```

`refresh_symbols`(原 120-237 行):**保留中段 httpx 爬蟲**(135-207 行 `async with httpx.AsyncClient...rows = list(by_symbol.values())` 原樣)。把頭尾的 supabase 檢查與 upsert 換掉:
- 刪除開頭 `supabase = get_supabase(); if supabase.status...`(122-127 行)。
- 結尾 upsert 區塊(215-230 行)換成:
```python
    get_local_store().market.replace_symbols(rows)
    logger.info("Replaced %d symbols into local cache", len(rows))
    return {"status": "ok", "fetched": len(rows), "upserted": len(rows), "errors": errors}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd backend; python -m pytest tests/test_symbols_route.py -v`
Expected: PASS

- [ ] **Step 5: 加啟動 bootstrap(symbols.json 不存在則背景爬)**

在 `routes/symbols.py` 末尾加可重用函式:
```python
async def bootstrap_symbols_if_missing() -> None:
    """啟動時若本機尚無 symbols 快取,背景爬一次(不阻塞)。"""
    if get_local_store().market.symbols_loaded():
        return
    try:
        await refresh_symbols()
        logger.info("symbols bootstrap done")
    except Exception as e:
        logger.warning("symbols bootstrap failed (可手動 POST /api/symbols/refresh): %s", e)
```
（在 Task 14 的 main.py 改動裡用 `asyncio.create_task(bootstrap_symbols_if_missing())` 掛起。）

- [ ] **Step 6: Commit**

```bash
git add backend/routes/symbols.py backend/tests/test_symbols_route.py
git commit -m "feat(local-store): symbols route 改讀本機快取 + 啟動 bootstrap(契約不變)"
```

---

### Task 9: cdp.py + camarilla.py → MarketCache daily_ohlc

**Files:**
- Modify: `backend/services/cdp.py`、`backend/services/camarilla.py`
- Test: `backend/tests/test_camarilla.py`(改 mock 方式)

- [ ] **Step 1: 改 `test_camarilla.py` 的 mock(先讓它失敗)**

把原本對 `fake_supabase.client.table...execute().data` 的 long-chain mock(77-79 行)換成直接寫入本機快取:
```python
def test_refresh_hits_cache(local_store_tmp):
    from services.local_store import get_local_store
    get_local_store().market.upsert_daily_ohlc([
        {"symbol": "2330", "date": "2026-05-21", "high": 110.0, "low": 90.0, "close": 100.0}])
    # ...呼叫 camarilla.refresh("2330") 後,斷言 cache 命中、8 線值不變...
```
（`test_camarilla.py` 內其餘斷言（cache 命中、線值）**保持不變** —— 這正是「行為不變」的證明。同步調整「無資料」案例 162 行用空快取。）

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend; python -m pytest tests/test_camarilla.py -v`
Expected: FAIL（refresh 仍走 supabase）

- [ ] **Step 3: 改 `camarilla.py` 的 daily_ohlc 讀寫**

`refresh()` 內「讀最近一筆 daily_ohlc」原本:
```python
sb.client.table("daily_ohlc").select("date, high, low, close").eq("symbol", symbol).order("date", desc=True).limit(1).execute()
```
換成:
```python
row = get_local_store().market.get_latest_daily_ohlc(symbol)
# row 為 None 或 {"date","high","low","close"};沿用原本「無資料就 return」邏輯
```
`backfill_from_fubon()` 內「批次 upsert daily_ohlc」原本:
```python
sb.client.table("daily_ohlc").upsert(upserts, on_conflict="symbol,date").execute()
```
換成:
```python
get_local_store().market.upsert_daily_ohlc(upserts)
```
把 `from services.supabase_client import get_supabase` 換成 `from services.local_store import get_local_store`,刪除 `sb = get_supabase()` 與其 status 檢查(本機快取永遠可用)。

- [ ] **Step 4: 對 `cdp.py` 做完全相同的兩處替換**（讀 + upsert,訊號/欄位邏輯不動）

- [ ] **Step 5: 跑測試確認通過**

Run: `cd backend; python -m pytest tests/test_camarilla.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/services/cdp.py backend/services/camarilla.py backend/tests/test_camarilla.py
git commit -m "feat(local-store): CDP/Camarilla daily_ohlc 改本機快取(線值行為不變)"
```

---

### Task 10: bookmarks + watchlist route → ConfigStore

**Files:**
- Modify: `backend/routes/bookmarks.py`、`backend/routes/watchlist.py`
- Test: `backend/tests/test_bookmarks_route.py`(新增)

**保留副作用**:POST item → `pool.subscribe(symbol, owner_id=f"bookmark:{gid}")` + CDP backfill;DELETE item → `pool.unsubscribe(...)`。系統書籤「大漲股」的 `count` 來自 `store.market.top_gainers_count()`。

- [ ] **Step 1: 寫失敗測試(驗 response 形狀 + 訂閱副作用)**

```python
# backend/tests/test_bookmarks_route.py
from unittest.mock import AsyncMock
from fastapi.testclient import TestClient
import main
from main import app
from services.local_store import get_local_store

client = TestClient(app)


def test_list_bookmarks_shape(local_store_tmp):
    r = client.get("/api/bookmarks")
    assert r.status_code == 200
    body = r.json()
    assert "groups" in body and "count" in body
    g = body["groups"][0]
    assert set(g) == {"id", "name", "sort_order", "is_system", "source_type", "count"}


def test_add_item_subscribes_ws(local_store_tmp, monkeypatch):
    fake_pool = AsyncMock()
    monkeypatch.setattr("routes.bookmarks.get_ws_pool", lambda: fake_pool)
    groups = client.get("/api/bookmarks").json()["groups"]
    gid = groups[0]["id"]
    r = client.post(f"/api/bookmarks/{gid}/items", json={"symbol": "2330"})
    assert r.status_code in (200, 201)
    fake_pool.subscribe.assert_awaited()  # owner=f"bookmark:{gid}"
    assert any(it["symbol"] == "2330" for it in get_local_store().config.list_items(gid))
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend; python -m pytest tests/test_bookmarks_route.py -v`
Expected: FAIL（走 supabase,回 503 或 KeyError）

- [ ] **Step 3: 改 `bookmarks.py`**

逐 endpoint 把 supabase 查詢換成 `store.config` 方法(對照下表),**system「大漲股」群組**在 `list_bookmarks` 回應裡硬塞一筆(維持原本行為),其 `count = store.market.top_gainers_count()`:

| endpoint | 原 supabase | 換成 |
|----------|------------|------|
| GET `/api/bookmarks` | select bookmark_groups + watchlist_items count + top_gainers count | `store.config.list_groups()` + `store.config.item_counts()`;append 系統「大漲股」群組(count = `store.market.top_gainers_count()`) |
| POST `/api/bookmarks` | insert bookmark_groups | `store.config.create_group(name, sort_order)` |
| PATCH `/api/bookmarks/{id}` | update | `store.config.update_group(id, ...)` |
| DELETE `/api/bookmarks/{id}` | delete + cascade | `store.config.delete_group(id)`(+ 對該群組所有 symbol `pool.unsubscribe(sym, f"bookmark:{id}")`) |
| GET `/api/bookmarks/{id}/items` | select watchlist_items | `store.config.list_items(id)` |
| POST `/api/bookmarks/{id}/items` | insert watchlist_items | symbol 驗證(見下)→ `store.config.add_item(id, symbol, note)` → `await pool.subscribe(symbol, f"bookmark:{id}")` + CDP backfill(原邏輯不動) |
| DELETE `/api/bookmarks/{id}/items/{symbol}` | delete | `store.config.remove_item(id, symbol)` → `await pool.unsubscribe(symbol, f"bookmark:{id}")` |
| PATCH `/api/bookmarks/items/move` | update group_id | 用 `remove_item` + `add_item` 組合(copy 時只 add) |

**symbol 驗證(spec §8.2)**:加 item 前 —— `if store.market.symbols_loaded() and not store.market.has_symbol(symbol): raise HTTPException(404, ...)`;快取未就緒(`symbols_loaded()` False)時放行。

- [ ] **Step 4: 改 `watchlist.py`(legacy「自選」)**

`watchlist.py` 的 GET/POST/DELETE 對應「自選」群組:啟動或首次取用時用 `store.config.list_groups()` 找 `name == "自選"` 的 group(seed 保證存在),其餘同 bookmarks item 操作(subscribe owner 用 `bookmark:{自選 gid}` 以與 bookmarks 一致;若原碼用 `"watchlist"` owner 則保留原字串)。

- [ ] **Step 5: 跑測試確認通過**

Run: `cd backend; python -m pytest tests/test_bookmarks_route.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/routes/bookmarks.py backend/routes/watchlist.py backend/tests/test_bookmarks_route.py
git commit -m "feat(local-store): bookmarks/watchlist route 改 ConfigStore(訂閱副作用保留)"
```

---

### Task 11: monitor_list route → ConfigStore

**Files:**
- Modify: `backend/routes/monitor_list.py`
- Test: `backend/tests/test_monitor_list_route.py`(改寫 mock)

**保留副作用**:POST → `await pool.subscribe(symbol, owner_id="monitor_list")`;DELETE → `await pool.unsubscribe(symbol, "monitor_list")`。

- [ ] **Step 1: 改 `test_monitor_list_route.py`**

刪掉 `fake_table` 鏈式 mock(14-21 行),改成用 `local_store_tmp` fixture + mock `get_ws_pool`:
```python
from unittest.mock import AsyncMock
from fastapi.testclient import TestClient
from main import app
from services.local_store import get_local_store

client = TestClient(app)


def test_add_monitor_persists_and_subscribes(local_store_tmp, monkeypatch):
    fake_pool = AsyncMock()
    monkeypatch.setattr("routes.monitor_list.get_ws_pool", lambda: fake_pool)
    r = client.post("/api/monitor_list", json={"symbol": "2330"})
    assert r.status_code in (200, 201)
    fake_pool.subscribe.assert_awaited_once_with("2330", owner_id="monitor_list")
    assert [m["symbol"] for m in get_local_store().config.list_monitor()] == ["2330"]
```

- [ ] **Step 2: 跑確認失敗** — Run: `cd backend; python -m pytest tests/test_monitor_list_route.py -v` → FAIL

- [ ] **Step 3: 改 `monitor_list.py`**:`from services.local_store import get_local_store`;GET → `store.config.list_monitor()`;POST → symbol 驗證(同 Task 10)→ `store.config.add_monitor(symbol)` → `await pool.subscribe(symbol, owner_id="monitor_list")`;DELETE → `store.config.remove_monitor(symbol)` → `await pool.unsubscribe(symbol, "monitor_list")`。response 形狀不變。

- [ ] **Step 4: 跑確認通過** — Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add backend/routes/monitor_list.py backend/tests/test_monitor_list_route.py
git commit -m "feat(local-store): monitor_list route 改 ConfigStore(訂閱副作用保留)"
```

---

### Task 12: active_signals route → ConfigStore

**Files:**
- Modify: `backend/routes/active_signals.py`
- Test: `backend/tests/test_active_signals_route.py`(新增)

**保留副作用**:POST/PUT/DELETE 後 → `await get_signal_engine().refresh_active_signals()`。

- [ ] **Step 1: 寫失敗測試**

```python
# backend/tests/test_active_signals_route.py
from unittest.mock import AsyncMock
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)
PAYLOAD = {"name": "突破", "filter_json": {}, "scope": {"type": "watchlist"},
           "cooldown_seconds": 1800, "enabled": True, "notify_discord": True}


def test_create_refreshes_engine(local_store_tmp, monkeypatch):
    fake_engine = AsyncMock()
    monkeypatch.setattr("routes.active_signals.get_signal_engine", lambda: fake_engine)
    r = client.post("/api/active_signals", json=PAYLOAD)
    assert r.status_code in (200, 201)
    assert r.json()["name"] == "突破"
    fake_engine.refresh_active_signals.assert_awaited_once()  # 為何:不重啟也要讓規則生效
```

- [ ] **Step 2: 跑確認失敗** → FAIL

- [ ] **Step 3: 改 `active_signals.py`**:GET → `store.config.list_active_signals()`;POST → `store.config.create_active_signal(payload)`;PUT → `store.config.update_active_signal(id, patch)`;DELETE → `store.config.delete_active_signal(id)`;每個寫操作後 `await get_signal_engine().refresh_active_signals()`。response 形狀不變。

- [ ] **Step 4: 跑確認通過** → PASS

- [ ] **Step 5: Commit**
```bash
git add backend/routes/active_signals.py backend/tests/test_active_signals_route.py
git commit -m "feat(local-store): active_signals route 改 ConfigStore(refresh 副作用保留)"
```

---

### Task 13: signals_history route + signal_engine → local_store

**Files:**
- Modify: `backend/routes/signals_history.py`、`backend/services/signal_engine.py`
- Test: `backend/tests/test_signals_history_route.py`(新增)

- [ ] **Step 1: 寫失敗測試**

```python
# backend/tests/test_signals_history_route.py
from fastapi.testclient import TestClient
from main import app
from services.local_store import get_local_store

client = TestClient(app)


def test_history_reads_jsonl(local_store_tmp):
    get_local_store().signals.append({"active_signal_id": "a", "symbol": "2330",
                                      "trigger_price": 1.0, "trigger_volume": 1, "context_json": {}})
    r = client.get("/api/signals/history", params={"symbol": "2330", "limit": 50})
    assert r.status_code == 200
    rows = r.json() if isinstance(r.json(), list) else r.json().get("results", r.json())
    assert any(x["symbol"] == "2330" for x in rows)
```

- [ ] **Step 2: 跑確認失敗** → FAIL

- [ ] **Step 3: 改 `signals_history.py`**:
- `GET /api/signals/history` → `store.signals.query(symbol=..., active_signal_id=..., since=..., limit=...)`,**沿用原本對 row 的轉換與外層包裝**(讀現碼,逐欄位輸出一致)。
- `GET /api/signals/today_counts` → 用 `store.signals.today_rows()` 做**與原本相同的 group-by**(讀現碼,維持回傳形狀)。

- [ ] **Step 4: 改 `signal_engine.py`**(4 處,對照 grounding):
- `refresh_active_signals()`:讀 active_signals 改 `get_local_store().config.list_active_signals(enabled_only=True)`(欄位同 select 清單)。
- `_load_monitor_symbols()`:改 `{m["symbol"] for m in get_local_store().config.list_monitor()}`。
- `_fanout()`:`get_signal_writer().append({...})`(**移除 `"user_label"` 欄**;其餘 active_signal_id/symbol/trigger_price/trigger_volume/context_json 不變)。
- backpressure 自動停用:`get_local_store().config.disable_all_active_signals()`。
- import 改:`from services.signal_writer import get_signal_writer`、`from services.local_store import get_local_store`;移除 `get_supabase`、`get_user_label`(Phase 3 全面清,但這裡先換掉用法)。

- [ ] **Step 5: 跑確認通過**

Run: `cd backend; python -m pytest tests/test_signals_history_route.py tests/ -k "signal_engine" -v`
Expected: PASS

- [ ] **Step 6: Commit**
```bash
git add backend/routes/signals_history.py backend/services/signal_engine.py backend/tests/test_signals_history_route.py
git commit -m "feat(local-store): signals_history + signal_engine 改 local_store(去 user_label)"
```

---

### Task 14: top_gainers_scheduler + main.py lifespan

**Files:**
- Modify: `backend/jobs/top_gainers_scheduler.py`、`backend/main.py`

- [ ] **Step 1: 改 `top_gainers_scheduler.py`**
- FK 驗證:`candidate ∈ symbols` 改用 `store = get_local_store(); [c for c in candidates if store.market.has_symbol(c)]`(快取空時放行全部)。
- `_replace_snapshot(...)`:改 `get_local_store().market.replace_top_gainers(rows)`(刪掉 delete+insert)。
- 既有「WS diff 訂閱/退訂」邏輯**保留**。

- [ ] **Step 2: 改 `main.py` lifespan**(對照 grounding 八步)
- 刪 `label = get_user_label()` fail-fast(45-47 行)與 `supabase = get_supabase(); supabase.init()`(51-52 行)。
- 在 `fubon.init()` 後加:`get_local_store().init()` + `asyncio.create_task(bootstrap_symbols_if_missing())`(import 自 `routes.symbols`)。
- `writer = get_supabase_writer()` → `from services.signal_writer import get_signal_writer; writer = get_signal_writer()`。
- 把「Bookmark 群組訂閱」(77-103 行)與「Monitor List 訂閱」(105-120 行)兩段,抽到新檔 `services/lifecycle_sync.py` 的 `resync_from_config()`,startup 改呼叫它(見 Task 15)。

- [ ] **Step 3: 跑全套測試**

Run: `cd backend; python -m pytest -v`
Expected: PASS(此時 supabase 仍安裝但已無人呼叫;`get_supabase` 只剩 supabase_client.py 自己)

- [ ] **Step 4: Commit**
```bash
git add backend/jobs/top_gainers_scheduler.py backend/main.py
git commit -m "feat(local-store): top_gainers 記憶體化 + main lifespan 去 supabase/user_label"
```

---

## Phase 3 — 匯入熱套用 + 移除 USER_LABEL

### Task 15: lifecycle_sync.resync_from_config()

**Files:**
- Create: `backend/services/lifecycle_sync.py`
- Test: `backend/tests/test_lifecycle_sync.py`

- [ ] **Step 1: 寫失敗測試**

```python
# backend/tests/test_lifecycle_sync.py
from unittest.mock import AsyncMock
import pytest
from services.local_store import get_local_store
from services.lifecycle_sync import resync_from_config


@pytest.mark.asyncio
async def test_resync_subscribes_bookmarks_and_monitor(local_store_tmp, monkeypatch):
    cfg = get_local_store().config
    g = cfg.list_groups()[0]
    cfg.add_item(g["id"], "2330")
    cfg.add_monitor("2454")
    fake_pool, fake_engine = AsyncMock(), AsyncMock()
    monkeypatch.setattr("services.lifecycle_sync.get_ws_pool", lambda: fake_pool)
    monkeypatch.setattr("services.lifecycle_sync.get_signal_engine", lambda: fake_engine)
    await resync_from_config(prev_owners={"bookmark:old": ["9999"]})
    fake_pool.unsubscribe.assert_any_await("9999", "bookmark:old")  # 退訂舊的
    fake_pool.subscribe.assert_any_await("2330", owner_id=f"bookmark:{g['id']}")
    fake_pool.subscribe.assert_any_await("2454", owner_id="monitor_list")
    fake_engine.refresh_active_signals.assert_awaited_once()
```

- [ ] **Step 2: 跑確認失敗** → FAIL

- [ ] **Step 3: 寫 `lifecycle_sync.py`**

```python
"""啟動與匯入共用的訂閱重建。

resync_from_config():
  1. 退訂 prev_owners 列出的舊訂閱(匯入時用,啟動時 prev_owners 為 None)
  2. 依目前 config 訂閱所有書籤股票(owner=f"bookmark:{gid}")+ 監聽清單(owner="monitor_list")
  3. signal_engine.refresh_active_signals()
"""
from __future__ import annotations

import logging

from services.fubon_ws import get_ws_pool
from services.local_store import get_local_store
from services.signal_engine import get_signal_engine

logger = logging.getLogger(__name__)


async def resync_from_config(prev_owners: dict[str, list[str]] | None = None) -> None:
    pool = get_ws_pool()
    # 1. 退訂舊 owner(匯入路徑)
    for owner, symbols in (prev_owners or {}).items():
        for sym in symbols:
            try:
                await pool.unsubscribe(sym, owner)
            except Exception as e:
                logger.warning("resync unsubscribe %s/%s failed: %s", owner, sym, e)
    # 2. 依 config 訂閱
    cfg = get_local_store().config
    for g in cfg.list_groups():
        owner = f"bookmark:{g['id']}"
        for it in cfg.list_items(g["id"]):
            try:
                await pool.subscribe(it["symbol"], owner_id=owner)
            except RuntimeError as e:
                logger.warning("resync sub %s failed: %s", it["symbol"], e)
    for m in cfg.list_monitor():
        try:
            await pool.subscribe(m["symbol"], owner_id="monitor_list")
        except RuntimeError as e:
            logger.warning("resync monitor sub %s failed: %s", m["symbol"], e)
    # 3. 重載規則
    await get_signal_engine().refresh_active_signals()


def current_owner_map() -> dict[str, list[str]]:
    """匯入前快照目前 config 的 owner→symbols,供匯入後退訂。"""
    cfg = get_local_store().config
    owners: dict[str, list[str]] = {}
    for g in cfg.list_groups():
        owners[f"bookmark:{g['id']}"] = [it["symbol"] for it in cfg.list_items(g["id"])]
    owners["monitor_list"] = [m["symbol"] for m in cfg.list_monitor()]
    return owners
```

- [ ] **Step 4: 把 `main.py` startup 改呼叫 `resync_from_config()`**（取代 Task 14 抽走的兩段訂閱迴圈）

- [ ] **Step 5: 跑確認通過 + 全套** → PASS

- [ ] **Step 6: Commit**
```bash
git add backend/services/lifecycle_sync.py backend/main.py backend/tests/test_lifecycle_sync.py
git commit -m "feat(local-store): resync_from_config 啟動/匯入共用訂閱重建(TDD)"
```

---

### Task 16: 全面移除 USER_LABEL

**Files:**
- Delete: `backend/services/user_context.py`
- Modify: 所有 `from services.user_context import get_user_label` 的檔、`backend/tests/conftest.py`、`backend/.env.example`

- [ ] **Step 1: 搜出所有用到的地方**

Run: `cd backend; python -m pytest -q` 前,先 grep:
```bash
grep -rn "user_label\|get_user_label\|USER_LABEL" backend --include=*.py
```

- [ ] **Step 2: 逐處移除**
- 刪 `services/user_context.py`。
- 各 route/service:Phase 2 已把資料查詢改 local_store(本就不帶 user_label);若還有殘留 import 或 `.eq("user_label", ...)`,刪除。
- `conftest.py`:刪 `os.environ.setdefault("USER_LABEL", "test")`。
- `.env.example`:刪 `USER_LABEL=...` 行。

- [ ] **Step 3: 確認沒有殘留**

Run: `grep -rn "user_label\|get_user_label\|USER_LABEL" backend --include=*.py`
Expected: 無輸出(或僅遷移腳本 Task 18 的 CLI 參數說明)

- [ ] **Step 4: 跑全套** → PASS

- [ ] **Step 5: Commit**
```bash
git add -A backend
git commit -m "feat(local-store): 全面移除 USER_LABEL(本機單人無需隔離)"
```

---

## Phase 4 — 匯出 / 匯入 API

### Task 17: config_io route(export / import + 熱套用)

**Files:**
- Create: `backend/routes/config_io.py`
- Modify: `backend/main.py`(掛 router)
- Test: `backend/tests/test_config_io.py`

- [ ] **Step 1: 寫失敗測試**

```python
# backend/tests/test_config_io.py
from unittest.mock import AsyncMock
from fastapi.testclient import TestClient
from main import app
from services.local_store import get_local_store

client = TestClient(app)


def test_export_then_import_roundtrip(local_store_tmp, monkeypatch):
    monkeypatch.setattr("services.lifecycle_sync.get_ws_pool", lambda: AsyncMock())
    monkeypatch.setattr("services.lifecycle_sync.get_signal_engine", lambda: AsyncMock())
    get_local_store().config.create_group("帶走的")
    snap = client.get("/api/config/export").json()
    assert snap["schema_version"] == 1 and snap["exported_at"]

    # 換個狀態再匯入
    get_local_store().config.create_group("臨時")
    r = client.post("/api/config/import", json=snap)
    assert r.status_code == 200
    names = [g["name"] for g in get_local_store().config.list_groups()]
    assert "帶走的" in names and "臨時" not in names
```

- [ ] **Step 2: 跑確認失敗** → FAIL

- [ ] **Step 3: 寫 `config_io.py`**

```python
"""GET /api/config/export、POST /api/config/import — 個人設定可攜檔。"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from services.lifecycle_sync import current_owner_map, resync_from_config
from services.local_store import get_local_store

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/config/export")
async def export_config() -> dict:
    return get_local_store().config.export_config()


@router.post("/api/config/import")
async def import_config(payload: dict) -> dict:
    prev = current_owner_map()  # 匯入前的訂閱快照
    try:
        get_local_store().config.import_config(payload)
    except ValueError as e:
        raise HTTPException(400, detail={"error": "bad_schema", "detail": str(e)})
    await resync_from_config(prev_owners=prev)  # 熱套用:退訂舊→訂新→refresh
    return {"status": "ok"}
```

在 `main.py` 掛上:`from routes import config_io; app.include_router(config_io.router)`。

- [ ] **Step 4: 跑確認通過** → PASS

- [ ] **Step 5: Commit**
```bash
git add backend/routes/config_io.py backend/main.py backend/tests/test_config_io.py
git commit -m "feat(config-io): 匯出/匯入端點 + 匯入熱套用(不重啟)"
```

---

## Phase 5 — 一次性遷移腳本

### Task 18: migrate_supabase_to_local.py

**Files:**
- Create: `backend/scripts/__init__.py`(空)、`backend/scripts/migrate_supabase_to_local.py`
- Test: `backend/tests/test_migrate_supabase.py`

- [ ] **Step 1: 寫失敗測試(mock supabase client 餵 fixture)**

```python
# backend/tests/test_migrate_supabase.py
from unittest.mock import MagicMock
from scripts.migrate_supabase_to_local import migrate


def _fake_sb(tables: dict):
    sb = MagicMock()
    def table(name):
        t = MagicMock()
        chain = t.select.return_value
        chain.eq.return_value = chain
        chain.execute.return_value = MagicMock(data=tables.get(name, []))
        return t
    sb.table.side_effect = table
    return sb


def test_migrate_writes_config_without_user_label(local_store_tmp):
    sb = _fake_sb({
        "bookmark_groups": [{"id": "g1", "user_label": "loger", "name": "自選",
                             "sort_order": 0, "is_system": False, "source_type": None,
                             "created_at": "2026-05-01T00:00:00Z"}],
        "watchlist_items": [{"id": "i1", "group_id": "g1", "symbol": "2330",
                             "added_at": "2026-05-01T00:00:00Z", "note": None}],
        "active_signals": [], "monitor_list": [], "watchlist": [],
        "signals_log": [{"id": 1, "active_signal_id": None, "symbol": "2330",
                         "triggered_at": "2026-05-01T01:00:00Z", "trigger_price": 900.0,
                         "trigger_volume": 5, "context_json": {}, "user_label": "loger"}],
    })
    summary = migrate(sb, user_label="loger")
    from services.local_store import get_local_store
    st = get_local_store(); st.init()
    grp = st.config.list_groups()
    assert any(g["name"] == "自選" for g in grp)
    assert "user_label" not in grp[0]            # 去掉 user_label
    assert st.signals.query(symbol="2330")       # 歷史也搬
    assert summary["bookmark_groups"] == 1
```

- [ ] **Step 2: 跑確認失敗** → FAIL

- [ ] **Step 3: 寫 `migrate_supabase_to_local.py`**

```python
"""一次性:把共用 Supabase 裡某 user_label 的個人資料 + 訊號歷史拉到本機。

用法:
    cd backend
    python -m scripts.migrate_supabase_to_local --user-label loger

讀 .env 的 SUPABASE_URL / SUPABASE_KEY;--user-label 只用來決定拉哪個人的舊資料,
不寫入本機、不成為執行期概念。symbols / daily_ohlc 不遷(本機自行重建)。
"""
from __future__ import annotations

import argparse
import os
from typing import Any

from services.local_store import get_local_store


def _strip_label(row: dict) -> dict:
    return {k: v for k, v in row.items() if k != "user_label"}


def migrate(sb: Any, user_label: str) -> dict:
    store = get_local_store()
    store.init()
    cfg = store.config

    def pull(table: str) -> list[dict]:
        res = sb.table(table).select("*").eq("user_label", user_label).execute()
        return res.data or []

    groups = [_strip_label(g) for g in pull("bookmark_groups") if not g.get("is_system")]
    items = [_strip_label(i) for i in
             (sb.table("watchlist_items").select("*").execute().data or [])
             if any(i["group_id"] == g["id"] for g in groups)]
    signals = [_strip_label(s) for s in pull("active_signals")]
    monitor = [_strip_label(m) for m in pull("monitor_list")]

    cfg.import_config({
        "schema_version": 1,
        "bookmark_groups": groups,
        "watchlist_items": items,
        "active_signals": signals,
        "monitor_list": monitor,
    })

    log_rows = [_strip_label(r) for r in pull("signals_log")]
    for r in log_rows:
        store.signals.append(r)

    summary = {"bookmark_groups": len(groups), "watchlist_items": len(items),
               "active_signals": len(signals), "monitor_list": len(monitor),
               "signals_log": len(log_rows)}
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user-label", required=True)
    args = ap.parse_args()
    from dotenv import load_dotenv
    load_dotenv()
    from supabase import create_client
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    summary = migrate(sb, args.user_label)
    print("遷移完成:", summary)
    print("驗證無誤後,即可從 .env 移除 SUPABASE_*、解除安裝 supabase 依賴(見 Task 19)。")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 跑確認通過** → PASS

- [ ] **Step 5: Commit**
```bash
git add backend/scripts/__init__.py backend/scripts/migrate_supabase_to_local.py backend/tests/test_migrate_supabase.py
git commit -m "feat(local-store): 一次性 Supabase→本機 遷移腳本(TDD,去 user_label)"
```

- [ ] **Step 6: 【人工】實際跑一次遷移**(此時 supabase 仍安裝、.env 仍有 SUPABASE_*)

```powershell
cd backend
python -m scripts.migrate_supabase_to_local --user-label loger
```
核對印出的摘要筆數 ≈ Supabase 各表現有 row 數(spec §3 筆數核對)。

---

## Phase 6 — 移除 Supabase(遷移驗證後)

### Task 19: 拔除 supabase 依賴與殘檔

**Files:**
- Delete: `backend/services/supabase_client.py`、`backend/services/supabase_writer.py`
- Modify: `backend/pyproject.toml`、`backend/.env.example`、根 `.env`(人工)

- [ ] **Step 1: 確認沒有 production code 還 import supabase**

```bash
grep -rn "supabase_client\|supabase_writer\|from supabase\|import supabase\|get_supabase\b" backend --include=*.py
```
Expected: 僅剩 `scripts/migrate_supabase_to_local.py`(它在 `main()` 內 import,允許保留作為一次性工具)。

- [ ] **Step 2: 刪檔 + 改依賴**
- 刪 `services/supabase_client.py`、`services/supabase_writer.py`。
- `pyproject.toml` 移除 `"supabase>=2.4"`。
- `.env.example` 移除 `SUPABASE_URL` / `SUPABASE_KEY`。

- [ ] **Step 3: 重裝依賴 + 全套測試**

```powershell
cd backend
pip install -e .
python -m pytest -v
```
Expected: 全綠。(遷移腳本測試用 MagicMock 假 client,不需真的裝 supabase;若 CI 介意,可在該測試頂部 `pytest.importorskip` 跳過 `main()`,`migrate()` 本身不 import supabase。)

- [ ] **Step 4: Commit**
```bash
git add -A backend
git commit -m "feat(local-store): 移除 supabase 依賴 + supabase_client/writer(零雲端 DB)"
```

---

## Phase 7 — 前端匯出/匯入 UI

### Task 20: api.ts + ConfigIODialog

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Create: `frontend/src/components/ConfigIODialog.tsx`
- Modify: `frontend/src/components/BookmarkManageDialog.tsx`(底部加入口)

- [ ] **Step 1: `api.ts` 加 exportConfig / importConfig**

```typescript
export const config = {
  async export(): Promise<Blob> {
    const headers = new Headers();
    if (BFF_API_KEY) headers.set("X-API-Key", BFF_API_KEY);
    const res = await fetch("/api/config/export", { headers });
    if (!res.ok) throw new ApiError(res.status, await res.text());
    return res.blob();
  },
  async import(data: unknown): Promise<{ status: string }> {
    return fetchJSON("/api/config/import", { method: "POST", body: JSON.stringify(data) });
  },
};
```

- [ ] **Step 2: 寫 `ConfigIODialog.tsx`**(下載 + 上傳 + 取代前確認)

```tsx
import { useRef, useState } from "react";
import { config } from "../lib/api";

export function ConfigIODialog({ onClose }: { onClose: () => void }) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  async function handleExport() {
    setBusy(true);
    try {
      const blob = await config.export();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      const date = new Date().toISOString().slice(0, 10);
      a.href = url;
      a.download = `trading-king-config-${date}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } finally {
      setBusy(false);
    }
  }

  async function handleImportFile(file: File) {
    if (!window.confirm("匯入會「整包取代」本機目前的書籤 / 訊號規則 / 監聽清單(會先備份舊檔)。確定?")) return;
    setBusy(true);
    setMsg(null);
    try {
      const data = JSON.parse(await file.text());
      await config.import(data);
      setMsg("匯入完成,設定已即時套用。");
    } catch (e) {
      setMsg(`匯入失敗:${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <button disabled={busy} onClick={handleExport} className="px-4 py-2 text-sm bg-accent text-bg">
        匯出設定(下載 JSON)
      </button>
      <button disabled={busy} onClick={() => fileRef.current?.click()} className="px-4 py-2 text-sm border border-accent">
        匯入設定(整包取代)
      </button>
      <input ref={fileRef} type="file" accept="application/json" hidden
        onChange={(e) => { const f = e.target.files?.[0]; if (f) handleImportFile(f); e.target.value = ""; }} />
      {msg && <p className="text-xs text-ink-dim">{msg}</p>}
      <button onClick={onClose} className="text-xs text-ink-dim hover:text-accent">關閉</button>
    </div>
  );
}
```

- [ ] **Step 3: 在 `BookmarkManageDialog.tsx` 底部控制區(原 140-145 行)加入口**

於「完成」按鈕旁加一顆「⤓ 匯出/匯入」,點開後 render `<ConfigIODialog onClose=... />`(用既有 modal 樣式)。

- [ ] **Step 4: 手動驗證(前端跑起來)**

```powershell
.\start.ps1
```
在書籤管理 modal → 匯出 → 得到 JSON 檔;改幾個書籤 → 匯入剛才的檔 → 畫面恢復、且不需重啟。

- [ ] **Step 5: Commit**
```bash
git add frontend/src/lib/api.ts frontend/src/components/ConfigIODialog.tsx frontend/src/components/BookmarkManageDialog.tsx
git commit -m "feat(config-io): 前端匯出/匯入 UI(書籤管理 modal 入口)"
```

---

## Phase 8 — 驗證收尾

### Task 21: 全套行為 parity 驗證

- [ ] **Step 1: 後端全套測試**

Run: `cd backend; python -m pytest -v`
Expected: 全綠。

- [ ] **Step 2: 端點 parity 清單(人工逐條打)** — 確認 response 形狀與重構前一致:
  - `GET /api/symbols?search=23` → `{results:[{symbol,name,market,is_etf}]}`
  - `GET /api/bookmarks` → `{groups:[{id,name,sort_order,is_system,source_type,count}],count}`(含系統「大漲股」)
  - `GET /api/bookmarks/{id}/items`、`POST/DELETE items`(WS 有訂閱/退訂 log)
  - `GET/POST/DELETE /api/monitor_list`(WS owner=monitor_list)
  - `GET/POST/PUT/DELETE /api/active_signals`(寫後 engine refresh log)
  - `GET /api/signals/history`、`/today_counts`
  - `GET /api/cdp/{symbol}`、`/api/camarilla/{symbol}`(線值與改前一致)
  - 純富邦代理 `/api/quote`、`/api/candles`、`/api/ma`、`/api/mxf/*`、`/api/preview` → 完全不受影響
  - `WS /ws/realtime` → tick / signal / mxf_* 事件正常

- [ ] **Step 3: 開機冷啟驗證** — 把 `backend/data/` 暫時移走,`.\start.ps1`:確認 symbols 背景 bootstrap 完成、搜尋可用、加股票 → 訂閱 → 訊號評估正常。

- [ ] **Step 4: 收尾 commit(若 parity 過程有微調)**
```bash
git add -A
git commit -m "test(local-store): 端點 parity 驗證收尾"
```

---

## 對照 spec 的覆蓋自查

- spec §3 行為不變 → Phase 2 逐檔 + Task 21 parity。
- spec §5 資料分層 → Phase 1 三個 store。
- spec §6 symbols 獲取 → Task 8(爬蟲保留 + 本機快取 + bootstrap)。
- spec §7 檔案布局 / config.json / signals_log.jsonl → Task 0–4。
- spec §8.1 local_store → Phase 1;§8.2 symbol 驗證 → Task 10/11;§8.3 匯出匯入 → Task 17;§8.4 熱套用 → Task 15/17;§8.5 遷移 → Task 18;§8.6 移除 USER_LABEL → Task 16;§8.7 JSONL appender → Task 7。
- spec §9 受影響檔案 → File Structure。
- spec §11 測試 → 各 Task TDD + Task 21。
