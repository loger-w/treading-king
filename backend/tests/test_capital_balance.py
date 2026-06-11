"""OnRealBalanceReport 解析。樣本依「假設表」構造 — 首測後以真實字串(去敏)替換並校準 index。"""
from services.capital_balance import BalanceCollector, parse_balance_line


def test_parse_single_line_to_position():
    p = parse_balance_line("TS,1234567,2330,1000,2000,0,3000,985.5")
    assert p is not None
    assert p.stock_no == "2330"
    assert p.qty == 3          # 3000 股 → 3 張
    assert p.avg_price == 985.5


def test_unparseable_or_short_line_skipped():
    assert parse_balance_line("##") is None                  # 結束/雜訊標記
    assert parse_balance_line("") is None
    assert parse_balance_line("TS,1234567") is None          # 欄位不足
    assert parse_balance_line("TS,x,2330,a,b,c,not_num,z") is None  # 數字欄壞 → 整筆略過


def test_zero_qty_line_skipped():
    # 已出清的標的(餘額 0)不該佔一列
    assert parse_balance_line("TS,1234567,2330,1000,0,1000,0,985.5") is None


def test_collector_flush_on_end_marker():
    got = []
    c = BalanceCollector(on_complete=got.append)
    c.feed("TS,1234567,2330,0,3000,0,3000,985.5")
    c.feed("TS,1234567,2317,1000,0,0,1000,100.0")
    assert got == []                       # 未收到結束標記不 flush
    c.feed("##")
    assert len(got) == 1
    assert [p.stock_no for p in got[0]] == ["2330", "2317"]


def test_collector_timeout_flush():
    got = []
    c = BalanceCollector(on_complete=got.append, timeout_s=0.0)
    c.feed("TS,1234567,2330,0,3000,0,3000,985.5")
    c.poll()                               # timeout=0 → 任何 elapsed 都該 flush(沒等到 ## 的保險)
    assert len(got) == 1


def test_collector_new_query_resets_staging():
    got = []
    c = BalanceCollector(on_complete=got.append)
    c.feed("TS,1234567,2330,0,3000,0,3000,985.5")
    c.reset()                              # 新一輪查詢
    c.feed("##")
    assert got == [[]]                     # staging 已清,flush 空集合(全部出清的合法狀態)
