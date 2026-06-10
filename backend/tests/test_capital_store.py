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


def test_action_failure_does_not_kill_live_order():
    # C/P/U/B + OrderErr 是「動作被拒」(如撮合中拒刪、改價超過漲跌停),原單仍掛在市場;
    # 標整張單失敗會讓活單從面板消失(刪/改鈕不見),user 跟丟真錢委託。
    s = CapitalStore()
    s.apply_reply(_evt(typ="N", qty="4000"))
    e = _evt(typ="C", qty="4000").model_copy(update={"order_err": "Y", "error_msg": "刪單失敗"})
    s.apply_reply(e)
    o = s.orders()[0]
    assert o.status_label == "委託成功"      # 單還活著
    assert o.actionable is True              # 可以再刪一次
    assert o.error_msg == "刪單失敗"          # 動作失敗的原因要看得到
    # 之後真的成交,照常累計
    s.apply_reply(_evt(typ="D", qty="1000"))
    o = s.orders()[0]
    assert o.status_label == "部分成交" and o.filled_qty == 1


def test_partial_fill_before_order_event_stays_actionable():
    # 亂序重播:部分成交的 D 先到、N 晚到。order_qty 未知時不得斷言「全部成交」,
    # 否則 _RANK 終態鎖死,還有 3 張掛在市場的活單會從面板上不可刪改。
    s = CapitalStore()
    s.apply_reply(_evt(typ="D", qty="1000", price="83.5000"))   # D 先到(只是部分)
    assert s.orders()[0].status_label == "部分成交"
    s.apply_reply(_evt(typ="N", qty="4000"))                    # N 晚到補量
    o = s.orders()[0]
    assert o.status_label == "部分成交"
    assert o.actionable is True
    assert o.order_qty == 4 and o.filled_qty == 1
    s.apply_reply(_evt(typ="D", qty="3000", price="83.5000"))   # 補滿才升全部成交
    assert s.orders()[0].status_label == "全部成交"


def test_d_without_price_first_event_does_not_latch_terminal():
    # 不採計的成交(無價)不可在 order_qty 未知時把單鎖成「全部成交」終態
    s = CapitalStore()
    s.apply_reply(_evt(typ="D", qty="1000", price=""))
    assert s.orders()[0].status_label is None
    s.apply_reply(_evt(typ="N", qty="4000"))
    o = s.orders()[0]
    assert o.status_label == "委託成功" and o.actionable is True


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


def test_remaining_shares_zero_for_terminal_order():
    # 已刪單 order-filled 差額不是「未成交量」:不歸零的話,死單改價會過金額閘、留給券商兜底
    s = CapitalStore()
    s.apply_reply(_evt(typ="N", qty="4000"))
    s.apply_reply(_evt(typ="D", qty="1000"))
    s.apply_reply(_evt(typ="C", qty="3000"))
    assert s.remaining_shares(SEQ_A) == 0


def test_market_of():
    s = CapitalStore()
    s.apply_reply(_evt(typ="N"))
    assert s.market_of(SEQ_A) == "TS"
    assert s.market_of("nope") is None


def test_orders_sorted_by_last_event_time():
    # spec:每筆事件更新 last_time、列表照 last_time 倒序 — 有新回報的單要浮頂,
    # 盤中確認刪改/成交結果不用往下捲找
    s = CapitalStore()
    s.apply_reply(_evt(seq=SEQ_A, typ="N", time="09:00:00"))
    s.apply_reply(_evt(seq=SEQ_B, typ="N", time="09:01:00"))
    assert [o.seq_no for o in s.orders()] == [SEQ_B, SEQ_A]
    s.apply_reply(_evt(seq=SEQ_A, typ="D", qty="1000", time="09:02:00"))  # A 有新事件 → 浮頂
    assert [o.seq_no for o in s.orders()] == [SEQ_A, SEQ_B]


def test_actionable_only_for_live_orders():
    # actionable 由後端 _RANK 單一決定(前端不再自己抄狀態表):tier 1/2 活單 true、終態/未知 false
    s = CapitalStore()
    s.apply_reply(_evt(typ="N", qty="4000"))
    assert s.orders()[0].actionable is True
    s.apply_reply(_evt(typ="D", qty="1000"))
    assert s.orders()[0].actionable is True       # 部分成交仍可刪改
    s.apply_reply(_evt(typ="C", qty="3000"))
    assert s.orders()[0].actionable is False      # 已刪單
    s2 = CapitalStore()
    s2.apply_reply(_evt(typ="X"))                 # 未知事件型別,狀態 None
    assert s2.orders()[0].actionable is False


def test_d_without_price_not_counted():
    # 成交無價整筆不採計(量與均價分子綁定)→ 均價不被稀釋、remaining 高估=金額閘更嚴(安全方向)
    s = CapitalStore()
    s.apply_reply(_evt(typ="N", qty="4000"))
    s.apply_reply(_evt(typ="D", qty="1000", price="100.0000"))
    s.apply_reply(_evt(typ="D", qty="1000", price=""))   # 無價
    o = s.orders()[0]
    assert o.filled_qty == 1
    assert abs(o.avg_fill_price - 100.0) < 1e-9
    assert s.remaining_shares(SEQ_A) == 3000


def test_d_with_order_err_not_counted():
    # D 帶 err 不採計量、標失敗:少算成交=閘更嚴,維持保守方向
    s = CapitalStore()
    s.apply_reply(_evt(typ="N", qty="4000"))
    e = _evt(typ="D", qty="1000").model_copy(update={"order_err": "Y", "error_msg": "異常"})
    s.apply_reply(e)
    o = s.orders()[0]
    assert o.filled_qty == 0
    assert o.status_label == "失敗"


def test_clear_resets_orders_keeps_positions():
    from services.capital_models import Position
    s = CapitalStore()
    s.apply_reply(_evt(typ="N"))
    s.set_positions([Position(stock_no="2330", qty=1, avg_price=500.0)])
    s.clear()
    assert s.orders() == []
    assert len(s.positions()) == 1


def test_set_positions_replaces_not_merges():
    # 整批「取代」:已出清的部位不可殘留,否則面板損益顯示錯
    from services.capital_models import Position
    s = CapitalStore()
    s.set_positions([Position(stock_no="2330", qty=5, avg_price=575.0)])
    s.set_positions([Position(stock_no="2317", qty=1, avg_price=100.0)])
    assert [p.stock_no for p in s.positions()] == ["2317"]
