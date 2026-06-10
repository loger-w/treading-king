# backend/tests/test_capital_store.py
"""聚合:key=13碼委託序號;同標的不同單絕不合併,合併的只有同一張單的事件。"""
from services.capital_store import CapitalStore
from services.capital_reply import parse_onnewdata

SEQ_A = "2313091378319"
SEQ_B = "2313092917885"


def _evt(seq=SEQ_A, market="TS", typ="N", err="N", bs="B00R2", stock="4989",
         price="83.7000", qty="1000", after="", time="10:05:22", pre="A"):
    arr = [""] * 47
    arr[0], arr[1], arr[2], arr[3] = seq, market, typ, err
    arr[4], arr[5], arr[6], arr[7], arr[8] = "9999", "0000000", bs, "TW", stock
    arr[10], arr[11] = "X01AA", price
    arr[20], arr[22] = qty, after
    arr[23], arr[24] = "20260610", time
    arr[31] = pre
    return parse_onnewdata(",".join(arr))


def test_order_then_partial_then_full_fill_aggregates():
    s = CapitalStore()
    s.apply_reply(_evt(typ="N", qty="4000"))                      # 委託 4 張
    s.apply_reply(_evt(typ="D", qty="1000", price="83.5000"))     # 成交 1
    o = s.orders()[0]
    assert o.status_label == "部分成交"
    assert o.order_qty == 4 and o.filled_qty == 1 and o.unit == "張"
    s.apply_reply(_evt(typ="D", qty="2000", price="83.7000"))
    s.apply_reply(_evt(typ="D", qty="1000", price="83.7000"))
    o = s.orders()[0]
    assert o.status_label == "全部成交"
    assert o.filled_qty == 4
    # 量加權均價 (83.5*1000 + 83.7*2000 + 83.7*1000) / 4000
    assert abs(o.avg_fill_price - 83.65) < 1e-9


def test_same_stock_different_seq_not_merged():
    s = CapitalStore()
    s.apply_reply(_evt(seq=SEQ_A, stock="3357", qty="1000"))
    s.apply_reply(_evt(seq=SEQ_B, stock="3357", qty="1000", time="14:59:48"))
    assert len(s.orders()) == 2


def test_cancel_keeps_filled_and_order_qty():
    s = CapitalStore()
    s.apply_reply(_evt(typ="N", qty="4000"))
    s.apply_reply(_evt(typ="D", qty="1000"))
    s.apply_reply(_evt(typ="C", qty="3000"))   # C 的 qty=剩量,不覆蓋
    o = s.orders()[0]
    assert o.status_label == "已刪單"
    assert o.order_qty == 4 and o.filled_qty == 1


def test_preorder_status_and_flag():
    s = CapitalStore()
    s.apply_reply(_evt(typ="N", pre="B"))
    o = s.orders()[0]
    assert o.status_label == "預約中"
    assert o.pre_order is True


def test_replay_out_of_order_does_not_downgrade():
    s = CapitalStore()
    s.apply_reply(_evt(typ="D", qty="1000", price="83.7000"))  # 先到 D(亂序)
    s.apply_reply(_evt(typ="N", qty="1000"))                   # 晚到 N 不得降級
    o = s.orders()[0]
    assert o.status_label == "全部成交"


def test_modify_qty_and_price():
    s = CapitalStore()
    s.apply_reply(_evt(typ="N", qty="4000", price="83.0000"))
    s.apply_reply(_evt(typ="U", qty="1000", after="3000"))     # 改量:after 優先
    s.apply_reply(_evt(typ="P", price="84.0000"))              # 改價
    o = s.orders()[0]
    assert o.order_qty == 3
    assert o.price == 84.0


def test_order_err_marks_failed():
    s = CapitalStore()
    e = _evt(typ="N")
    e = e.model_copy(update={"order_err": "Y", "error_msg": "超過漲跌停"})
    s.apply_reply(e)
    o = s.orders()[0]
    assert o.status_label == "失敗"
    assert o.error_msg == "超過漲跌停"


def test_futures_unit_and_no_division():
    s = CapitalStore()
    s.apply_reply(_evt(market="TF", bs="BNR20", stock="QEF06", qty="1", price="873.0000"))
    o = s.orders()[0]
    assert o.unit == "口" and o.order_qty == 1 and o.market == "TF"


def test_no_seq_dropped():
    s = CapitalStore()
    e = _evt().model_copy(update={"seq_no": None})
    s.apply_reply(e)
    assert s.orders() == []


def test_remaining_shares():
    s = CapitalStore()
    s.apply_reply(_evt(typ="N", qty="4000"))
    s.apply_reply(_evt(typ="D", qty="1000"))
    assert s.remaining_shares(SEQ_A) == 3000
    assert s.remaining_shares("nope") is None
