# backend/tests/test_capital_position.py
from services.capital_models import Position


def test_unrealized_gross_pnl():
    p = Position(stock_no="2330", name="台積電", qty=5, avg_price=575.0)
    assert p.unrealized_gross(current_price=590.0) == 75000.0   # 5*1000*(590-575)


def test_short_position_negative_qty():
    p = Position(stock_no="2317", name="鴻海", qty=-2, avg_price=100.0)
    # 放空:跌才賺。現價 95 → (95-100)*-2*1000 = +10000
    assert p.unrealized_gross(current_price=95.0) == 10000.0


def test_zero_when_no_price():
    p = Position(stock_no="2330", name="台積電", qty=5, avg_price=575.0)
    assert p.unrealized_gross(current_price=None) == 0.0
