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
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
