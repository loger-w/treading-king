"""個人設定的本機儲存:書籤 / 訊號規則 / 監聽清單。

所有方法同步且過程中不 await → 單 process asyncio 下對事件迴圈天生原子,不需鎖。
每次變動都寫穿到 config.json(原子替換)。
"""
from __future__ import annotations

import json
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
        try:
            self._data = read_json(self._path, None) or _empty_config()
        except (json.JSONDecodeError, OSError):
            # 壞檔不讓後端起不來:備份後重建
            if self._path.exists():
                self._path.replace(self._path.with_suffix(".json.corrupt"))
            self._data = _empty_config()
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
