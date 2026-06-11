"""訊號觸發歷史(append-only JSONL)。同步、不 await → 對事件迴圈原子。

檔案只在 load() 時讀一次進記憶體,append() 寫檔的同時維護記憶體 list —
query / today_rows 直接查記憶體,避免 append-only 檔無上限成長後,
每次 API 都同步全檔重讀重 parse 卡住事件迴圈(包括行情 tick 消費)。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from services.local_store.config_store import _now_iso

_TPE = ZoneInfo("Asia/Taipei")


class SignalsLog:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._next_id = 1
        self._rows: list[dict] = []

    def load(self) -> None:
        self._next_id = 1
        self._rows = []
        if not self._path.exists():
            return
        with open(self._path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(rec, dict):
                    # 合法 JSON 但非物件(手改/外部損壞)— 跳過,不讓啟動掛掉
                    continue
                self._rows.append(rec)
                try:
                    self._next_id = max(self._next_id, int(rec.get("id", 0)) + 1)
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
        self._rows.append(rec)
        return rec

    def query(self, *, symbol: str | None = None, active_signal_id: str | None = None,
              since: str | None = None, limit: int = 200) -> list[dict]:
        rows = list(self._rows)  # copy:下面的 sort 不能動到內部 list
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
        for r in self._rows:
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
