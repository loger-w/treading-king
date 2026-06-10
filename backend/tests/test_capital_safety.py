from services.capital_models import StockOrderRequest, BuySell
from services.capital_safety import SafetyConfig, check_stock_order
from services.capital_safety import check_cancel, check_correct_price, check_decrease


def _req(qty=1, price=590.0):
    return StockOrderRequest(stock_no="2330", buy_sell=BuySell.BUY, price=price, qty=qty)


def _cfg(enabled=True, max_qty=5, max_amount=2_000_000.0):
    return SafetyConfig(order_enabled=enabled, max_qty=max_qty, max_amount=max_amount)


def test_master_switch_off_blocks():
    r = check_stock_order(_req(), _cfg(enabled=False))
    assert r.allowed is False
    assert "總開關" in r.reason


def test_qty_over_limit_blocks():
    r = check_stock_order(_req(qty=6), _cfg(max_qty=5))
    assert r.allowed is False
    assert "數量" in r.reason


def test_amount_over_limit_blocks():
    # 10 張 * 590 * 1000 = 5,900,000 > 2,000,000
    r = check_stock_order(_req(qty=10), _cfg(max_qty=100, max_amount=2_000_000))
    assert r.allowed is False
    assert "金額" in r.reason


def test_zero_qty_blocks():
    assert check_stock_order(_req(qty=0), _cfg()).allowed is False


def test_valid_order_allowed():
    r = check_stock_order(_req(qty=1), _cfg())
    assert r.allowed is True
    assert r.reason is None


def _gate_cfg(enabled=True, max_amount=100000.0):
    return SafetyConfig(order_enabled=enabled, max_qty=1, max_amount=max_amount)


def test_cancel_only_needs_master_switch():
    assert check_cancel(_gate_cfg(True)).allowed is True
    g = check_cancel(_gate_cfg(False))
    assert g.allowed is False and "總開關" in g.reason


def test_correct_price_checks_amount_with_remaining():
    # 新價 × 未成交股數 超過上限要擋
    g = check_correct_price(200.0, 1000, _gate_cfg(max_amount=100000))
    assert g.allowed is False and "超過上限" in g.reason
    assert check_correct_price(90.0, 1000, _gate_cfg(max_amount=100000)).allowed is True
    assert check_correct_price(90.0, 1000, _gate_cfg(False)).allowed is False


def test_correct_price_rejects_nonpositive():
    assert check_correct_price(0, 1000, _gate_cfg()).allowed is False
    assert check_correct_price(-1, 1000, _gate_cfg()).allowed is False


def test_decrease_needs_positive_qty():
    assert check_decrease(1, _gate_cfg()).allowed is True
    assert check_decrease(0, _gate_cfg()).allowed is False
    assert check_decrease(1, _gate_cfg(False)).allowed is False
