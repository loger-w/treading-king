from services.capital_models import (
    StockOrderRequest, BuySell, PriceType, TimeInForce, TradeKind,
)
from services.capital_mapping import to_stockorder_fields


def _req(**kw):
    base = dict(stock_no="2330", buy_sell=BuySell.BUY, price=590.0, qty=1)
    base.update(kw)
    return StockOrderRequest(**base)


def test_buy_limit_rod_cash_maps_to_capital_enums():
    f = to_stockorder_fields(_req(), full_account="1234567890A")
    assert f["bstrStockNo"] == "2330"
    assert f["bstrFullAccount"] == "1234567890A"
    assert f["sBuySell"] == 0           # 買=0
    assert f["nSpecialTradeType"] == 2  # 限價=2
    assert f["nTradeType"] == 0         # ROD=0
    assert f["sFlag"] == 0              # 現股=0
    assert f["bstrPrice"] == "590.00"   # 價格字串,兩位小數
    assert f["nQty"] == 1


def test_sell_market_fok_short_maps():
    f = to_stockorder_fields(
        _req(buy_sell=BuySell.SELL, price_type=PriceType.MARKET,
             time_in_force=TimeInForce.FOK, trade_kind=TradeKind.SHORT),
        full_account="1234567890A",
    )
    assert f["sBuySell"] == 1            # 賣=1
    assert f["nSpecialTradeType"] == 1   # 市價=1
    assert f["nTradeType"] == 2          # FOK=2
    assert f["sFlag"] == 2               # 融券=2


def test_margin_maps_to_one():
    f = to_stockorder_fields(_req(trade_kind=TradeKind.MARGIN), full_account="x")
    assert f["sFlag"] == 1               # 融資=1
