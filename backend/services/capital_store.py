# backend/services/capital_store.py
"""群益委託/部位記憶體快取(執行緒安全)。COM 事件回呼更新它;REST 讀它。

委託聚合:key = 13 碼委託序號(KeyNo)。同標的不同單絕不合併;
合併的只有同一張單自己的 委託/成交/刪改 事件。
重啟後靠 SKReplyLib_ConnectByID 的當日 backlog 重播重建,無需持久化。
"""
from __future__ import annotations
import threading
from dataclasses import dataclass
from services.capital_models import OrderRecord, Position
from services.capital_reply import ReplyRecord

_SEC_LOT_MARKETS = {"TS", "TA", "TP"}        # 整股:股 → 張(÷1000)
_FUT_MARKETS = {"TF", "TO", "OF", "OO"}      # 口

# 狀態只進不退(防 backlog 重播亂序降級)
_RANK = {
    "預約中": 1, "委託成功": 1, "改價": 1, "改量": 1, "改價改量": 1,
    "部分成交": 2,
    "全部成交": 3, "已刪單": 3, "失敗": 3, "逾時": 3, "退單": 3,
}


@dataclass
class _Agg:
    seq_no: str
    stock_no: str | None = None
    market: str | None = None
    buy_sell: str | None = None
    flag_label: str | None = None
    book_no: str | None = None
    status_raw: str | None = None
    status_label: str | None = None
    price: float | None = None
    order_qty: int = 0            # 原始單位(股/口)
    filled_qty: int = 0
    fill_value: float = 0.0       # Σ(成交價×量),算均價用
    time: str | None = None
    pre_order: bool = False
    error_msg: str | None = None
    raw: str = ""


class CapitalStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._orders: dict[str, _Agg] = {}
        self._order_seq: list[str] = []           # 到達順序
        self._positions: dict[str, Position] = {}

    def _set_status(self, a: _Agg, label: str) -> None:
        if _RANK.get(label, 0) >= _RANK.get(a.status_label or "", 0):
            a.status_label = label

    def apply_reply(self, rec: ReplyRecord) -> None:
        if not rec.seq_no:
            return
        with self._lock:
            a = self._orders.get(rec.seq_no)
            if a is None:
                a = _Agg(seq_no=rec.seq_no)
                self._orders[rec.seq_no] = a
                self._order_seq.append(rec.seq_no)

            # 共通欄位:有值就更新
            for f in ("stock_no", "market", "buy_sell", "flag_label", "book_no"):
                v = getattr(rec, f)
                if v:
                    setattr(a, f, v)
            if rec.pre_order:
                a.pre_order = True
            if rec.time:
                a.time = rec.time
            a.status_raw = rec.status_raw
            a.raw = rec.raw

            t = rec.status_raw
            if rec.order_err in ("Y", "T"):
                a.error_msg = rec.error_msg or a.error_msg
                self._set_status(a, "失敗" if rec.order_err == "Y" else "逾時")
            elif t == "N":
                a.order_qty = rec.qty or a.order_qty
                if rec.price is not None:
                    a.price = rec.price
                self._set_status(a, "預約中" if rec.pre_order else "委託成功")
            elif t == "D":
                a.filled_qty += rec.qty
                if rec.price is not None:
                    a.fill_value += rec.price * rec.qty
                full = a.order_qty > 0 and a.filled_qty >= a.order_qty
                self._set_status(a, "全部成交" if (full or a.order_qty == 0) else "部分成交")
            elif t == "C":
                # C 的 qty=原委託剩量,order/filled 不動
                self._set_status(a, "已刪單")
            elif t == "U":
                a.order_qty = rec.after_qty if rec.after_qty is not None else max(a.order_qty - rec.qty, 0)
                self._set_status(a, "改量")
            elif t == "P":
                if rec.price is not None:
                    a.price = rec.price
                self._set_status(a, "改價")
            elif t == "B":
                if rec.price is not None:
                    a.price = rec.price
                if rec.after_qty is not None:
                    a.order_qty = rec.after_qty
                self._set_status(a, "改價改量")
            elif t == "S":
                self._set_status(a, "退單")

    def _to_record(self, a: _Agg) -> OrderRecord:
        if a.market in _SEC_LOT_MARKETS or a.market is None:
            div, unit = 1000, "張"
        elif a.market in _FUT_MARKETS:
            div, unit = 1, "口"
        else:                                    # TL/TC 零股
            div, unit = 1, "股"
        avg = (a.fill_value / a.filled_qty) if a.filled_qty > 0 else None
        return OrderRecord(
            seq_no=a.seq_no, stock_no=a.stock_no, market=a.market,
            buy_sell=a.buy_sell, flag_label=a.flag_label, book_no=a.book_no,
            status_raw=a.status_raw, status_label=a.status_label,
            price=a.price, avg_fill_price=round(avg, 4) if avg is not None else None,
            order_qty=a.order_qty // div, filled_qty=a.filled_qty // div, unit=unit,
            time=a.time, pre_order=a.pre_order, error_msg=a.error_msg, raw=a.raw,
        )

    def orders(self) -> list[OrderRecord]:
        with self._lock:
            return [self._to_record(self._orders[s]) for s in reversed(self._order_seq)]

    def remaining_shares(self, seq_no: str) -> int | None:
        """改價金額閘用:未成交量(原始單位,股/口)。查無此單回 None。"""
        with self._lock:
            a = self._orders.get(seq_no)
            if a is None:
                return None
            return max(a.order_qty - a.filled_qty, 0)

    def set_positions(self, positions: list[Position]) -> None:
        with self._lock:
            self._positions = {p.stock_no: p for p in positions}

    def positions(self) -> list[Position]:
        with self._lock:
            return list(self._positions.values())

    def position_for(self, stock_no: str) -> Position | None:
        with self._lock:
            return self._positions.get(stock_no)
