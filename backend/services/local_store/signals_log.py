"""訊號觸發歷史(append-only JSONL)。同步、不 await → 對事件迴圈原子。"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
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
