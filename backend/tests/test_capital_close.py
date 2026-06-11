"""平倉反向映射(spec §6.2 四規則)+ close 驗量。"""
import asyncio
import pytest
from services.capital_models import (
    BuySell, Position, PositionCloseRequest, TradeKind,
)
from services.capital_close import build_close_order


def test_long_cash_closes_with_cash_sell():
    pos = Position(stock_no="2330", qty=3, avg_price=500.0)
    req = PositionCloseRequest(stock_no="2330", price=450.0)
    order = build_close_order(pos, req, pos_kind="cash")
    assert order.buy_sell == BuySell.SELL
    assert order.trade_kind == TradeKind.CASH
    assert order.qty == 3                      # 預設全部
    assert order.source == "panel"


def test_partial_qty_close():
    pos = Position(stock_no="2330", qty=5, avg_price=500.0)
    req = PositionCloseRequest(stock_no="2330", qty=2, price=450.0)
    assert build_close_order(pos, req, pos_kind="cash").qty == 2


def test_qty_over_holding_rejected():
    pos = Position(stock_no="2330", qty=2, avg_price=500.0)
    req = PositionCloseRequest(stock_no="2330", qty=3, price=450.0)
    with pytest.raises(ValueError, match="超過持有"):
        build_close_order(pos, req, pos_kind="cash")


def test_margin_long_closes_with_margin_sell():
    pos = Position(stock_no="2330", qty=1, avg_price=500.0)
    order = build_close_order(pos, PositionCloseRequest(stock_no="2330", price=450.0), pos_kind="margin")
    assert order.buy_sell == BuySell.SELL
    assert order.trade_kind == TradeKind.MARGIN


def test_short_position_closes_with_short_buy():
    pos = Position(stock_no="2330", qty=-2, avg_price=500.0)
    order = build_close_order(pos, PositionCloseRequest(stock_no="2330", price=550.0), pos_kind="short")
    assert order.buy_sell == BuySell.BUY
    assert order.trade_kind == TradeKind.SHORT
    assert order.qty == 2                      # 取絕對值


def test_daytrade_short_closes_with_cash_buy():
    pos = Position(stock_no="2330", qty=-1, avg_price=500.0)
    order = build_close_order(pos, PositionCloseRequest(stock_no="2330", price=550.0), pos_kind="daytrade_sell")
    assert order.buy_sell == BuySell.BUY
    assert order.trade_kind == TradeKind.CASH  # 無券空單回補=現股買進(交易所自動沖銷)


def test_close_no_position_blocked_and_audited(tmp_path):
    from tests.test_capital_client import FakeCom, _client
    client = _client(FakeCom(), enabled=True, audit_path=tmp_path / "a.jsonl")
    res = asyncio.run(client.close_position(PositionCloseRequest(stock_no="2330", price=100.0)))
    assert res.ok is False
    assert "無部位" in res.message
    assert (tmp_path / "a.jsonl").exists()    # 被拒也留稽核
