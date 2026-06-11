"""把 StockOrderRequest 轉成群益 STOCKORDER 欄位 dict。

純函式,不碰 COM,方便測群益 enum 對應(對錯=送錯單)。
enum 值來源:官方 Python 範例 sk.STOCKORDER(spec §4.2)。
"""
from __future__ import annotations
from services.capital_models import (
    StockOrderRequest, BuySell, PriceType, TimeInForce, TradeKind,
)

_BUYSELL = {BuySell.BUY: 0, BuySell.SELL: 1}
_SPECIAL = {PriceType.MARKET: 1, PriceType.LIMIT: 2}
_TIF = {TimeInForce.ROD: 0, TimeInForce.IOC: 1, TimeInForce.FOK: 2}
_FLAG = {TradeKind.CASH: 0, TradeKind.MARGIN: 1, TradeKind.SHORT: 2, TradeKind.DAYTRADE_SELL: 3}


def to_stockorder_fields(req: StockOrderRequest, full_account: str) -> dict:
    return {
        "bstrFullAccount": full_account,
        "bstrStockNo": req.stock_no,
        "sBuySell": _BUYSELL[req.buy_sell],
        "bstrPrice": f"{req.price:.2f}",
        "nQty": req.qty,
        "nSpecialTradeType": _SPECIAL[req.price_type],
        "nTradeType": _TIF[req.time_in_force],
        "sFlag": _FLAG[req.trade_kind],
        "sPeriod": 0,  # 盤中(v1 僅盤中整股)
        "sPrime": 0,   # 上市櫃
    }
