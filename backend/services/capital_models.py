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
