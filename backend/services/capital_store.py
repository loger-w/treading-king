# backend/services/capital_store.py
"""群益委託/部位記憶體快取(執行緒安全)。COM 事件回呼更新它;REST 讀它。"""
from __future__ import annotations
import threading
from services.capital_models import OrderRecord, Position
from services.capital_reply import ReplyRecord


class CapitalStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._orders: dict[str, OrderRecord] = {}
        self._order_seq: list[str] = []           # 到達順序
        self._positions: dict[str, Position] = {}

    def apply_reply(self, rec: ReplyRecord) -> None:
        if not rec.seq_no:
            return
        with self._lock:
            if rec.seq_no not in self._orders:
                self._order_seq.append(rec.seq_no)
            self._orders[rec.seq_no] = OrderRecord(
                seq_no=rec.seq_no, stock_no=rec.stock_no, book_no=rec.book_no,
                status_raw=rec.status_raw, status_label=rec.status_label,
                price=rec.price, qty=rec.qty, raw=rec.raw,
            )

    def orders(self) -> list[OrderRecord]:
        with self._lock:
            return [self._orders[s] for s in reversed(self._order_seq)]

    def set_positions(self, positions: list[Position]) -> None:
        with self._lock:
            self._positions = {p.stock_no: p for p in positions}

    def positions(self) -> list[Position]:
        with self._lock:
            return list(self._positions.values())

    def position_for(self, stock_no: str) -> Position | None:
        with self._lock:
            return self._positions.get(stock_no)
