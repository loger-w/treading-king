"""平倉反向單組裝 — 純函式。部位種類 → 回補單(spec §6.2 固定映射):
現股多→現股賣;融資多→融資賣;融券空→融券買;無券空→現股買(交易所自動沖銷)。
v1 部位資料僅現股(GetRealBalanceReport),信用 pos_kind 由呼叫端在資料就緒後傳入。"""
from __future__ import annotations

from services.capital_models import (
    BuySell, Position, PositionCloseRequest, StockOrderRequest, TradeKind,
)

# (部位種類, 是否多頭) → (回補方向, 回補交易種類)
_CLOSE_MAP: dict[tuple[str, bool], tuple[BuySell, TradeKind]] = {
    ("cash", True): (BuySell.SELL, TradeKind.CASH),
    ("margin", True): (BuySell.SELL, TradeKind.MARGIN),
    ("short", False): (BuySell.BUY, TradeKind.SHORT),
    ("daytrade_sell", False): (BuySell.BUY, TradeKind.CASH),
}


def build_close_order(pos: Position, req: PositionCloseRequest, *, pos_kind: str) -> StockOrderRequest:
    holding = abs(pos.qty)
    if holding == 0:
        raise ValueError(f"{req.stock_no} 無部位可平")
    lots = req.qty if req.qty is not None else holding
    if lots <= 0:
        raise ValueError("平倉數量必須大於 0")
    if lots > holding:
        raise ValueError(f"平倉 {lots} 張超過持有 {holding} 張")
    key = (pos_kind, pos.qty > 0)
    if key not in _CLOSE_MAP:
        raise ValueError(f"部位種類 {pos_kind} 與方向不符,無法平倉")
    side, kind = _CLOSE_MAP[key]
    if req.price is None or req.price <= 0:
        raise ValueError("缺平倉價格(市價單也需帶閘用估價)")
    return StockOrderRequest(
        stock_no=req.stock_no, buy_sell=side, price=req.price, qty=lots,
        price_type=req.price_type, trade_kind=kind, source=req.source,
    )
