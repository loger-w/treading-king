from __future__ import annotations
from enum import Enum
from typing import Literal
from pydantic import BaseModel

# 群益市場別:證券類(整股 TS/TA/TP、零股 TL/TC)。期貨/期權 TF/TO/OF/OO/OS 不在此集。
# route 顯示過濾、capital_reply idx6 解碼、capital_client 寫入市場閘共用這一份。
SEC_MARKETS = frozenset({"TS", "TA", "TL", "TP", "TC"})


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
    DAYTRADE_SELL = "daytrade_sell"  # 無券賣出(現股當沖先賣;回補=現股買進自動沖銷)


class StockOrderRequest(BaseModel):
    stock_no: str
    buy_sell: BuySell
    price: float
    qty: int  # 張
    price_type: PriceType = PriceType.LIMIT
    time_in_force: TimeInForce = TimeInForce.ROD
    trade_kind: TradeKind = TradeKind.CASH
    source: Literal["panel", "flash"] = "panel"  # 稽核分流:單從哪個介面送出


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
    date: str | None = None           # 委託建立日 YYYYMMDD(排序/前端跨日顯示用)
    time: str | None = None           # 最新事件 HH:MM:SS
    pre_order: bool = False
    error_msg: str | None = None
    actionable: bool = False          # 活單可刪/改。store 由 _RANK 算,前端不要自己抄狀態表
    raw: str = ""                     # 最新事件原始字串(debug)


class Position(BaseModel):
    stock_no: str
    name: str = ""
    qty: int                        # 張(融券放空為負)
    avg_price: float | None = None  # 損益試算[10]平均買進成本(OnRealBalanceReport 無此欄)
    kind: str = "cash"              # cash(T集保)/margin(C融資)/short(L融券) — 平倉反向映射用
    pnl_base: float | None = None        # 損益試算[9]含費稅息淨損益(報告市價時點)— 前端平移基底
    pnl_base_price: float | None = None  # 損益試算[5]報告市價(平移基準)
    pnl_cost: float | None = None        # 損益試算[12]成交價金(% 分母,同報告[21]口徑)


# 負價/0量不在 pydantic 設 gt=0:刻意下放到 client 安全閘擋,
# 422 會在進 client 前短路、不留稽核;真錢寫入連「被拒」都要留帳。
class CancelOrderRequest(BaseModel):
    seq_no: str


class CorrectPriceRequest(BaseModel):
    seq_no: str
    price: float


class DecreaseQtyRequest(BaseModel):
    seq_no: str
    qty: int  # 張(與 SendStockOrder.nQty 同慣例;首次實測對群益 App 驗)


class PositionCloseRequest(BaseModel):
    stock_no: str
    qty: int | None = None                    # None=全部
    price_type: PriceType = PriceType.MARKET
    price: float | None = None                # market=閘用估價(前端帶);limit=委託價
    source: Literal["panel", "flash"] = "panel"
