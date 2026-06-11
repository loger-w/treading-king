from services.capital_models import StockOrderRequest, BuySell, TradeKind
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


def test_unset_limits_fail_closed():
    # .env.example 承諾「0=擋」:CAPITAL_MAX_QTY/CAPITAL_MAX_AMOUNT 漏設或打錯
    # 變數名(factory 落 0)必須拒單,不可變成「無上限放行」
    r = check_stock_order(_req(), _cfg(max_qty=0))
    assert r.allowed is False and "上限" in r.reason
    r = check_stock_order(_req(), _cfg(max_amount=0))
    assert r.allowed is False and "上限" in r.reason
    g = check_correct_price(90.0, 1000, _cfg(max_amount=0))
    assert g.allowed is False and "上限" in g.reason


def test_valid_order_allowed():
    r = check_stock_order(_req(qty=1), _cfg())
    assert r.allowed is True
    assert r.reason is None


def test_daytrade_sell_with_buy_rejected():
    # 無券=現股當沖先賣;「無券+買進」不是合法組合(回補=現股買進)
    req = StockOrderRequest(stock_no="2330", buy_sell=BuySell.BUY, price=100.0, qty=1,
                            trade_kind=TradeKind.DAYTRADE_SELL)
    r = check_stock_order(req, _cfg())
    assert r.allowed is False
    assert "無券" in r.reason


def test_daytrade_sell_with_sell_allowed():
    req = StockOrderRequest(stock_no="2330", buy_sell=BuySell.SELL, price=100.0, qty=1,
                            trade_kind=TradeKind.DAYTRADE_SELL)
    assert check_stock_order(req, _cfg()).allowed is True


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


def test_correct_price_rejects_no_remaining():
    # 已全成交/已刪單 remaining=0:est=0 恆過金額閘,安全層須自己擋,不留給券商兜底
    g = check_correct_price(100.0, 0, _gate_cfg())
    assert g.allowed is False and "無未成交" in g.reason


def test_gates_reject_nan_and_inf_price():
    # NaN 對任何比較都 False:NaN<=0 過、NaN>max_amount 也過 → 兩道閘全穿透,必須明確擋
    nan, inf = float("nan"), float("inf")
    assert check_correct_price(nan, 1000, _gate_cfg()).allowed is False
    assert check_correct_price(inf, 1000, _gate_cfg()).allowed is False
    assert check_stock_order(_req(price=nan), _cfg()).allowed is False


def test_stock_order_rejects_nonpositive_price():
    # 送單閘原本只看 qty 與金額上限:price=-5 → est 為負恆 < max_amount,會直送 COM
    assert check_stock_order(_req(price=-5.0), _cfg()).allowed is False
    assert check_stock_order(_req(price=0.0), _cfg()).allowed is False
