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
    seq_no: str
    stock_no: str | None = None
    book_no: str | None = None
    status_raw: str | None = None
    status_label: str | None = None
    price: float | None = None
    qty: int = 0
    raw: str = ""


class Position(BaseModel):
    stock_no: str
    name: str = ""
    qty: int           # 張(放空為負)
    avg_price: float

    def unrealized_gross(self, current_price: float | None) -> float:
        if current_price is None:
            return 0.0
        return self.qty * 1000 * (current_price - self.avg_price)
