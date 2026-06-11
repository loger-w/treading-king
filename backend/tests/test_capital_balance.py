"""OnRealBalanceReport 解析。樣本 = 2026-06-11 正式環境真實回報(ID/帳號去敏),
欄位定義 = 官方 4-2-c(策略王COM元件使用說明_V2.13.58.docx),兩者已互相驗證。"""
from services.capital_balance import (
    BalanceCollector, dedupe_positions, parse_balance_line, parse_profit_line,
)
from services.capital_models import Position

# 今日買進 1 張現股:昨庫 0、今委買/買成 1000、即時庫存[14]=1000
RAW_T_BOUGHT = "2493,T,0,0,0,0,0,1000,0,1000,0,1000,0,0,1000,0,,A123456789,1234567890"
# 當沖軋平:買賣各 1000、即時庫存 0 → 不佔一列
RAW_T_FLAT = "3042,T,0,0,0,0,0,1000,1000,1000,1000,0,0,0,0,0,,A123456789,1234567890"
# 融資 3 張(昨日庫存):[1]=C、[2][3]=資額度(千元)、[16]=即時維持率(會跳動,不是價格!)
RAW_C_MARGIN = "3357,C,2000,1944,0,0,3000,0,0,0,0,3000,0,0,3000,0,155.63,A123456789,1234567890"
# 融券 2 張(依官方欄位表構造):qty 應為負
RAW_L_SHORT = "9105,L,0,0,2000,2000,0,0,0,0,0,2000,0,0,2000,0,130.25,A123456789,1234567890"
# 查詢結束標記
RAW_END = "##,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,"


def test_parse_cash_position():
    p = parse_balance_line(RAW_T_BOUGHT)
    assert p is not None
    assert p.stock_no == "2493"
    assert p.qty == 1              # 即時庫存[14]=1000 股 → 1 張
    assert p.kind == "cash"
    assert p.avg_price is None     # 此報告無均價欄([16] 是維持率),均價待損益試算 API


def test_parse_margin_position():
    p = parse_balance_line(RAW_C_MARGIN)
    assert p is not None
    assert p.stock_no == "3357"
    assert p.qty == 3
    assert p.kind == "margin"      # 平倉反向映射要送融資賣,不是現股賣
    assert p.avg_price is None     # [16]=155.63 是維持率,絕不可當均價/價格


def test_parse_short_position_negative_qty():
    p = parse_balance_line(RAW_L_SHORT)
    assert p is not None
    assert p.qty == -2             # 融券放空 → 負張數(close 映射靠 qty>0 判多空)
    assert p.kind == "short"


def test_daytrade_flat_skipped():
    # 當沖軋平(即時庫存 0)不佔一列
    assert parse_balance_line(RAW_T_FLAT) is None


def test_unparseable_or_short_line_skipped():
    assert parse_balance_line(RAW_END) is None               # 結束標記
    assert parse_balance_line("") is None
    assert parse_balance_line("2493,T,0,0") is None          # 欄位不足
    bad = RAW_T_BOUGHT.replace(",1000,0,,", ",x,0,,")        # [14] 數字壞 → 整筆略過
    assert parse_balance_line(bad) is None


def test_unknown_kind_skipped():
    # 未知庫存種類寧缺勿錯:平倉映射依 kind 送單,猜錯=送錯單種
    assert parse_balance_line(RAW_T_BOUGHT.replace(",T,", ",Z,")) is None


def test_dedupe_keeps_larger_position():
    # 同檔集保+融資並存:store 以 stock_no 為鍵,保留張數大者(被捨棄部分暫不可平,待分種類建模)
    cash = Position(stock_no="2330", qty=1, kind="cash")
    margin = Position(stock_no="2330", qty=3, kind="margin")
    other = Position(stock_no="2317", qty=2, kind="cash")
    out = dedupe_positions([cash, margin, other])
    assert sorted((p.stock_no, p.qty, p.kind) for p in out) == [("2317", 2, "cash"), ("2330", 3, "margin")]


def test_collector_flush_on_end_marker():
    got = []
    c = BalanceCollector(on_complete=got.append)
    c.feed(RAW_T_BOUGHT)
    c.feed(RAW_C_MARGIN)
    assert got == []                       # 未收到結束標記不 flush
    c.feed(RAW_END)
    assert len(got) == 1
    assert [p.stock_no for p in got[0]] == ["2493", "3357"]


def test_collector_timeout_flush():
    got = []
    c = BalanceCollector(on_complete=got.append, timeout_s=0.0)
    c.feed(RAW_T_BOUGHT)
    c.poll()                               # timeout=0 → 任何 elapsed 都該 flush(沒等到 ## 的保險)
    assert len(got) == 1


def test_collector_new_query_resets_staging():
    got = []
    c = BalanceCollector(on_complete=got.append)
    c.feed(RAW_T_BOUGHT)
    c.reset()                              # 新一輪查詢
    c.feed(RAW_END)
    assert got == [[]]                     # staging 已清,flush 空集合(全部出清的合法狀態)


# 未實現-彙總(4-2-p,25 欄)依官方欄位表構造;首跑後換真實去敏樣本。
# [1]=股票代號、[10]=平均買進(券賣)成本;第一筆=查詢結果(000,訊息)
RAW_PNL_ROW = "臺慶科,3357,新台幣,融資,3000,156.00,0.27,468000,464000,12345,150.55,451650,0,0,665,0,1404,135495,316155,89,,2.73,0,,Y"
RAW_PNL_STATUS = "000,查詢成功"


def test_parse_profit_line():
    assert parse_profit_line(RAW_PNL_ROW) == ("3357", 150.55)


def test_parse_profit_skips_status_end_and_junk():
    assert parse_profit_line(RAW_PNL_STATUS) is None                     # 查詢結果列
    assert parse_profit_line("##,,,,") is None                           # 結束標記
    assert parse_profit_line("") is None
    assert parse_profit_line("名,3357,新台幣,現股,1000") is None          # 欄位不足
    assert parse_profit_line(RAW_PNL_ROW.replace("150.55", "x")) is None  # 均價壞
    assert parse_profit_line(RAW_PNL_ROW.replace("150.55", "0")) is None  # 均價 0 不出垃圾


def test_collector_with_profit_parser():
    got = []
    c = BalanceCollector(on_complete=got.append, parse=parse_profit_line)
    c.feed(RAW_PNL_STATUS)
    c.feed(RAW_PNL_ROW)
    c.feed("##")
    assert got == [[("3357", 150.55)]]
