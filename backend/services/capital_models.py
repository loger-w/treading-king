from __future__ import annotations
from enum import Enum
from pydantic import BaseModel


class CapitalEnv(str, Enum):
    TEST = "test"
    PROD = "prod"


class BuySell(str, Enum):
    BUY = "buy"
    SELL = "sell"


class PriceType(str, Enum):
    LIMIT = "limit"
    MARKET = "market"


class TimeInForce(str, Enum):
    ROD = "ROD"
    IOC = "IOC"
    FOK = "FOK"


class TradeKind(str, Enum):
    CASH = "cash"      # 現股
    MARGIN = "margin"  # 融資
    SHORT = "short"    # 融券


class StockOrderRequest(BaseModel):
    stock_no: str
    buy_sell: BuySell
    price: float
    qty: int  # 張
    price_type: PriceType = PriceType.LIMIT
    time_in_force: TimeInForce = TimeInForce.ROD
    trade_kind: TradeKind = TradeKind.CASH


class OrderResult(BaseModel):
    ok: bool
    code: int
    message: str
    seq_no: str | None = None


class OrderRecord(BaseModel):
    """委託清單一列 = 一張單的聚合狀態(key=13碼委託序號)。qty 已換算顯示單位。"""
    seq_no: str
    stock_no: str | None = None
    name: str = ""                    # route enrich 填,store 不管
    market: str | None = None
    buy_sell: str | None = None       # "B"/"S"
    flag_label: str | None = None     # 現股/融資/融券…
    book_no: str | None = None
    status_raw: str | None = None     # 最新事件 Type
    status_label: str | None = None   # 預約中/委託成功/部分成交/全部成交/已刪單/失敗/逾時/退單
    price: float | None = None        # 委託價(P/B 更新)
    avg_fill_price: float | None = None
    order_qty: int = 0                # 顯示單位(張/股/口)
    filled_qty: int = 0
    unit: str = "張"
    time: str | None = None           # 最新事件 HH:MM:SS
    pre_order: bool = False
    error_msg: str | None = None
    raw: str = ""                     # 最新事件原始字串(debug)


class Position(BaseModel):
    stock_no: str
    name: str = ""
    qty: int           # 張(放空為負)
    avg_price: float

    def unrealized_gross(self, current_price: float | None) -> float:
        if current_price is None:
            return 0.0
        return self.qty * 1000 * (current_price - self.avg_price)
