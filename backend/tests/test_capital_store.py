# backend/tests/test_capital_store.py
from services.capital_reply import parse_onnewdata
from services.capital_models import Position
from services.capital_store import CapitalStore


def _mk(fields):
    arr = [""] * 25
    for i, v in fields.items():
        arr[i] = str(v)
    return ",".join(arr)


def test_reply_upserts_order_by_seqno():
    s = CapitalStore()
    s.apply_reply(parse_onnewdata(_mk({0: "A1", 8: "2330", 3: "0", 20: "3"})))
    s.apply_reply(parse_onnewdata(_mk({0: "A1", 8: "2330", 3: "2", 20: "3"})))  # 同序號更新狀態
    orders = s.orders()
    assert len(orders) == 1
    assert orders[0].seq_no == "A1"
    assert orders[0].status_raw == "2"   # 後到的覆蓋


def test_orders_sorted_newest_first():
    s = CapitalStore()
    s.apply_reply(parse_onnewdata(_mk({0: "A1"})))
    s.apply_reply(parse_onnewdata(_mk({0: "A2"})))
    assert [o.seq_no for o in s.orders()] == ["A2", "A1"]


def test_set_positions_replaces():
    s = CapitalStore()
    s.set_positions([Position(stock_no="2330", name="台積電", qty=5, avg_price=575.0)])
    assert len(s.positions()) == 1
    assert s.position_for("2330").qty == 5
    assert s.position_for("9999") is None
