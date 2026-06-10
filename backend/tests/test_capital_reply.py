# backend/tests/test_capital_reply.py
"""capital_reply 解碼測試。fixture = 2026-06-10 正式環境真實回報(帳號欄已匿名化)。"""
from services.capital_reply import parse_onnewdata

# 現股買 預約單(收盤後掛)→ 之後被刪;Type=N 委託
RAW_N_PREORDER = "2313091595225,TS,N,N,9999,0000000,B00R2,TW,3357,,00000,293.0000,,,,,,,,,1000,,,20260610,14:59:48,,0000000,0671,PI,20260611,1000000055420,B,3357,,,,,,,,,,,,,,,2313092917892"
# 同一張單的刪單回報;Type=C,qty=原委託剩量
RAW_C_PREORDER = "2313091595225,TS,C,N,9999,0000000,B00R2,TW,3357,,00000,293.0000,,,,,,,,,1000,,,20260610,14:59:48,,0000000,0671,PI,20260611,1000000055420,B,3357,,,,,,,,,,,,,,,2313092917892"
# 融資賣 盤中成交(Type=D,idx38 有成交序號)
RAW_D_MARGIN_SELL = "2313092627047,TS,D,N,9999,0000000,S03R2,TW,4989,,S01Q7,83.7000,,,,,,,,,1000,,,20260610,12:46:31,,0000000,0671,PI,20260610,1020000573620,A,4989,,,,,,00006702389,,,,,,,,,2313092627047"
# 期貨 新倉買(TF;qty=口)
RAW_TF_NEW = "2315596711743,TF,N,N,F020000,4528443,BNR20,TW,QEF06,,u5834,873.0000,,,,,,,,,1,,,20260610,12:16:59,,0000000,0673,PI,20260610,2110001321199,A,FIQEF,202606,,,,,,,A,20260610,,,,N,,2315596711743"


def test_parse_preorder_new():
    r = parse_onnewdata(RAW_N_PREORDER)
    assert r.seq_no == "2313091595225"
    assert r.market == "TS"
    assert r.status_raw == "N"
    assert r.status_label == "委託"
    assert r.order_err == "N"
    assert r.buy_sell == "B"
    assert r.flag_label == "現股"
    assert r.stock_no == "3357"
    assert r.price == 293.0
    assert r.qty == 1000
    assert r.time == "14:59:48"
    assert r.pre_order is True          # idx31 = B
    assert r.error_msg is None


def test_parse_cancel():
    r = parse_onnewdata(RAW_C_PREORDER)
    assert r.status_raw == "C"
    assert r.status_label == "刪單"
    assert r.seq_no == "2313091595225"


def test_parse_fill_margin_sell():
    r = parse_onnewdata(RAW_D_MARGIN_SELL)
    assert r.status_raw == "D"
    assert r.status_label == "成交"
    assert r.buy_sell == "S"
    assert r.flag_label == "融資"
    assert r.price == 83.7
    assert r.qty == 1000
    assert r.pre_order is False         # idx31 = A
    assert r.book_no == "S01Q7"


def test_parse_futures_flag():
    r = parse_onnewdata(RAW_TF_NEW)
    assert r.market == "TF"
    assert r.buy_sell == "B"
    assert r.flag_label == "新倉"       # 期權 idx6[1] = Y當沖/N新倉/O平倉
    assert r.qty == 1


def test_parse_order_err_failed():
    # OrderErr=Y + idx44 錯誤訊息(無真實樣本,依官方 spec 構造)
    arr = RAW_N_PREORDER.split(",")
    arr[3] = "Y"
    arr[44] = "委託失敗:超過漲跌停"
    r = parse_onnewdata(",".join(arr))
    assert r.order_err == "Y"
    assert r.error_msg == "委託失敗:超過漲跌停"


def test_parse_after_qty():
    # U 改量:idx22 AfterQty(無真實樣本,依官方 spec 構造)
    arr = RAW_N_PREORDER.split(",")
    arr[2] = "U"
    arr[20] = "1000"   # 減量數
    arr[22] = "2000"   # 改後量
    r = parse_onnewdata(",".join(arr))
    assert r.status_label == "改量"
    assert r.after_qty == 2000


def test_parse_garbage_does_not_crash():
    r = parse_onnewdata("xxx")
    assert r.seq_no == "xxx"
    assert r.market is None or isinstance(r.market, str)
    assert r.qty == 0
