"""訊號歷史寫入器 — 直接 append 到本機 jsonl(無批次、即時寫穿)。

保留 start()/shutdown() 以維持 main.py 生命週期介面(本機無需背景 flush,為 no-op)。
"""
from __future__ import annotations

from typing import Any

from services.local_store import get_local_store


class SignalWriter:
    async def start(self) -> None:  # 介面相容,no-op
        return

    async def shutdown(self) -> None:
        return

    def append(self, row: dict[str, Any]) -> None:
        get_local_store().signals.append(row)


_writer: SignalWriter | None = None


def get_signal_writer() -> SignalWriter:
    global _writer
    if _writer is None:
        _writer = SignalWriter()
    return _writer
