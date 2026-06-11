# backend/services/capital_store.py
"""群益委託/部位記憶體快取(執行緒安全)。COM 事件回呼更新它;REST 讀它。

委託聚合:key = 13 碼委託序號(KeyNo)。同標的不同單絕不合併;
合併的只有同一張單自己的 委託/成交/刪改 事件。
重啟後靠 SKReplyLib_ConnectByID 的當日 backlog 重播重建,無需持久化。

注意:聚合**非冪等** — 同一筆回報事件只能 apply 一次(目前唯一來源
ConnectByID 啟動重播 + 即時推送,天然唯一)。未來若加回報斷線重連,
重播前必須先 clear(),否則成交量會重複累計。
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

# 刪/改事件(C/U/P/B)帶 OrderErr 時,失敗的是「該次動作」,原委託仍掛在市場上;
# 標整張單終態會讓活單從面板消失(刪/改鈕跟著沒了)。N/D/S 帶 err 才是單本身的問題。
_ACTION_TYPES = {"C", "U", "P", "B"}


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
    date: str | None = None       # 委託建立日 YYYYMMDD
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

    def _refresh_fill_status(self, a: _Agg) -> None:
        """成交滿不滿由量推導;量變動(N 補量/D 成交/U/B 改量)就重算。
        order_qty 未知(=0,N 還沒到)時不得斷言「全部成交」— 終態進 _RANK 就退不回來,
        部分成交的活單會被鎖死在面板上不可刪改。"""
        if a.filled_qty <= 0:
            return
        if a.order_qty > 0 and a.filled_qty >= a.order_qty:
            self._set_status(a, "全部成交")
        else:
            self._set_status(a, "部分成交")

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
            for f in ("stock_no", "market", "buy_sell", "flag_label", "book_no", "date"):
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
                if t not in _ACTION_TYPES:
                    self._set_status(a, "失敗" if rec.order_err == "Y" else "逾時")
            elif t == "N":
                a.order_qty = rec.qty or a.order_qty
                if rec.price is not None:
                    a.price = rec.price
                self._set_status(a, "預約中" if rec.pre_order else "委託成功")
                self._refresh_fill_status(a)   # 亂序:D 先到時,N 補上量後重算滿不滿
            elif t == "D":
                # 成交無價(解析失敗)整筆不採計:量與均價分子綁定,
                # 少算成交 → remaining_shares 高估 → 改價金額閘更嚴,是安全方向。
                if rec.price is not None:
                    a.filled_qty += rec.qty
                    a.fill_value += rec.price * rec.qty
                self._refresh_fill_status(a)
            elif t == "C":
                # C 的 qty=原委託剩量,order/filled 不動
                self._set_status(a, "已刪單")
            elif t == "U":
                a.order_qty = rec.after_qty if rec.after_qty is not None else max(a.order_qty - rec.qty, 0)
                self._set_status(a, "改量")
                self._refresh_fill_status(a)   # 減到 ≤ 已成交量 = 等同全部成交
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
                self._refresh_fill_status(a)
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
            date=a.date, time=a.time, pre_order=a.pre_order, error_msg=a.error_msg,
            actionable=_RANK.get(a.status_label or "", 0) in (1, 2),
            raw=a.raw,
        )

    def orders(self) -> list[OrderRecord]:
        """日期+時間倒序(昨日預約單不浮頂、有新回報的單浮頂);同秒以到達序新者在前。"""
        with self._lock:
            arrival = {s: i for i, s in enumerate(self._order_seq)}
            aggs = sorted(self._orders.values(),
                          key=lambda a: (a.date or "", a.time or "", arrival[a.seq_no]), reverse=True)
            return [self._to_record(a) for a in aggs]

    def remaining_shares(self, seq_no: str) -> int | None:
        """改價金額閘用:未成交量(原始單位,股/口)。查無此單回 None。
        終態單(已刪/全成/失敗/逾時/退單)回 0:死單沒有未成交量可改,
        否則已刪單的 order-filled 差額會讓改價閘對死單放行、留給券商兜底。"""
        with self._lock:
            a = self._orders.get(seq_no)
            if a is None:
                return None
            if _RANK.get(a.status_label or "", 0) >= 3:
                return 0
            return max(a.order_qty - a.filled_qty, 0)

    def market_of(self, seq_no: str) -> str | None:
        """寫入鏈市場閘用:該單市場別。查無此單或缺值回 None(寬鬆放行,與顯示端同慣例)。"""
        with self._lock:
            a = self._orders.get(seq_no)
            return a.market if a else None

    def clear(self) -> None:
        """清空委託聚合(部位不動)。回報重連重播前必須呼叫,否則成交量重複累計。"""
        with self._lock:
            self._orders.clear()
            self._order_seq.clear()

    def set_positions(self, positions: list[Position]) -> None:
        with self._lock:
            old = self._positions
            for p in positions:
                prev = old.get(p.stock_no)
                # 損益查詢回來前沿用既有均價(同種類才沿用 — 資/券成本基礎不同)
                if p.avg_price is None and prev is not None and prev.kind == p.kind:
                    p.avg_price = prev.avg_price
            self._positions = {p.stock_no: p for p in positions}

    def apply_avg_prices(self, avg: dict[str, float]) -> None:
        """損益試算回填均價;查無股號忽略(部位清單以即時庫存為權威)。"""
        with self._lock:
            for stock_no, price in avg.items():
                p = self._positions.get(stock_no)
                if p is not None:
                    p.avg_price = price

    def positions(self) -> list[Position]:
        with self._lock:
            return list(self._positions.values())

    def position_for(self, stock_no: str) -> Position | None:
        with self._lock:
            return self._positions.get(stock_no)
