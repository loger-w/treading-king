# backend/tests/test_capital_reply.py
from services.capital_reply import parse_onnewdata


def _mk(fields: dict) -> str:
    """造一個至少 25 欄的逗號字串,把指定 index 填值。"""
    arr = [""] * 25
    for i, v in fields.items():
        arr[i] = str(v)
    return ",".join(arr)


def test_parse_extracts_known_indices():
    data = _mk({0: "A0001", 2: "1", 3: "0", 8: "2330", 10: "B123", 11: "590.00", 20: "3"})
    r = parse_onnewdata(data)
    assert r.seq_no == "A0001"
    assert r.status_raw == "0"
    assert r.stock_no == "2330"
    assert r.book_no == "B123"
    assert r.price == 590.0
    assert r.qty == 3


def test_blank_price_qty_become_none_zero():
    data = _mk({0: "A1", 8: "2317"})
    r = parse_onnewdata(data)
    assert r.price is None      # 空字串 → None
    assert r.qty == 0           # 空字串 → 0
    assert r.stock_no == "2317"


def test_status_label_maps_known_and_falls_back():
    # 已知碼給標籤;未知碼回原值
    assert parse_onnewdata(_mk({3: "0"})).status_label in {"委託成功", "委託中"}
    assert parse_onnewdata(_mk({3: "ZZ"})).status_label == "ZZ"


def test_short_string_does_not_crash():
    r = parse_onnewdata("A1,foo")   # 欄位不足也不能炸
    assert r.seq_no == "A1"
    assert r.stock_no is None
