"""OnRealBalanceReport 解析。樣本 = 2026-06-11 正式環境真實回報(ID/帳號去敏),
欄位定義 = 官方 4-2-c(策略王COM元件使用說明_V2.13.58.docx),兩者已互相驗證。"""
from services.capital_balance import (
    BalanceCollector, ProfitRow, dedupe_positions, parse_balance_line, parse_profit_line,
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


def test_parse_short_negative_shares_defensive():
    """融券列真實符號未實測:若 [14] 回的是負股數,floor division 會把幅度多算一張
    再負負得正 — 防禦寫法兩種符號都要對。"""
    raw = RAW_L_SHORT.replace(",2000,0,130.25,", ",-2000,0,130.25,")
    p = parse_balance_line(raw)
    assert p is not None
    assert p.qty == -2 and p.kind == "short"


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


def test_collector_timeout_flush_closes_round():
    # timeout 保險先 flush 後,殘餘事件與遲到的 ## 不可再 flush —
    # on_complete 是全量取代語意,二次 flush 會把部位整批換成尾段幾檔或空集合
    got = []
    c = BalanceCollector(on_complete=got.append, timeout_s=0.0)
    c.feed(RAW_T_BOUGHT)
    c.poll()                               # timeout flush(部分清單)
    assert len(got) == 1
    c.feed(RAW_C_MARGIN)                   # 同一輪殘餘事件 → 丟棄
    c.feed(RAW_END)                        # 遲到的結束標記 → 不得 flush 空/尾段集合
    c.poll()
    assert len(got) == 1
    c.reset()                              # 下一輪查詢重新開張
    c.feed(RAW_C_MARGIN)
    c.feed(RAW_END)
    assert len(got) == 2
    assert [p.stock_no for p in got[1]] == ["3357"]


def test_collector_new_query_resets_staging():
    got = []
    c = BalanceCollector(on_complete=got.append)
    c.feed(RAW_T_BOUGHT)
    c.reset()                              # 新一輪查詢
    c.feed(RAW_END)
    assert got == [[]]                     # staging 已清,flush 空集合(全部出清的合法狀態)


# 未實現-彙總(4-2-p)= 2026-06-11 正式環境真實回報(ID/帳號去敏)。
# 實際 30 欄(比文件 25 欄多尾端含費均價等);[1]=股票代號、[10]=平均買進(券賣)成本。
# 第一筆=查詢結果(000,訊息可空);總計列股號為空。
RAW_PNL_ROW = "揚博,2493,新台幣,現股,1000,180.00,-0.50,180000.00,179414.00,1368.00,178.05,178046.00,178000.00,46.00,46.00,0.00,540.00,0,0,0,0.00,0.77,0,,Y,1,0,178.628000,A123456789,1234567890"
RAW_PNL_MARGIN = "臺慶科,3357,新台幣,融資,3000,288.00,-7.50,864000.00,301364.00,-74636.00,311.75,376240.00,935000.00,240.00,221.00,0.00,2592.00,376000,559000,583,0.00,-7.98,0,,Y,2,3,312.950000,A123456789,1234567890"
RAW_PNL_TOTAL = ",,新台幣,9999,0,0.00,0.00,1599000.00,940891.00,-91721.00,0.00,1032932.00,1684500.00,432.00,409.00,0.00,4797.00,595000,652000,583,0.00,0.00,0,,N,3,0,0.000000,A123456789,1234567890"
RAW_PNL_STATUS = "000,"


def test_parse_profit_line():
    # 均價之外還要 [9]損益(含費稅息)/[5]報告市價/[12]成交價金 —— 前端「券商基底+即時平移」口徑用;
    # [3]交易種類也要解析:同檔多種庫存並存時每種類一列,回填只認同種類(成本基礎不可混用)
    assert parse_profit_line(RAW_PNL_ROW) == ProfitRow("2493", 178.05, 1368.0, 180.0, 178000.0, "cash")
    assert parse_profit_line(RAW_PNL_MARGIN) == ProfitRow("3357", 311.75, -74636.0, 288.0, 935000.0, "margin")


def test_parse_profit_line_pnl_fields_optional():
    # 損益欄壞掉只丟那幾欄,均價仍要保住(均價是主要產出)
    bad = RAW_PNL_ROW.replace(",1368.00,", ",x,")
    assert parse_profit_line(bad) == ProfitRow("2493", 178.05, None, 180.0, 178000.0, "cash")


def test_parse_profit_line_unknown_kind_is_none():
    # 未知交易種類標籤 → kind=None:回填端視為不符、略過(寧缺均價,不可套錯成本基礎)
    row = parse_profit_line(RAW_PNL_ROW.replace(",現股,", ",信用,"))
    assert row is not None and row.kind is None


def test_parse_profit_skips_status_total_end_and_junk():
    assert parse_profit_line(RAW_PNL_STATUS) is None                     # 查詢結果列(訊息可空)
    assert parse_profit_line(RAW_PNL_TOTAL) is None                      # 總計列(股號空)不出垃圾
    assert parse_profit_line("##,,,,") is None                           # 結束標記
    assert parse_profit_line("") is None
    assert parse_profit_line("名,3357,新台幣,現股,1000") is None          # 欄位不足
    assert parse_profit_line(RAW_PNL_ROW.replace("178.05", "x")) is None  # 均價壞
    assert parse_profit_line(RAW_PNL_ROW.replace("178.05", "0")) is None  # 均價 0 不出垃圾


def test_collector_with_profit_parser():
    got = []
    c = BalanceCollector(on_complete=got.append, parse=parse_profit_line)
    c.feed(RAW_PNL_STATUS)
    c.feed(RAW_PNL_ROW)
    c.feed(RAW_PNL_TOTAL)
    c.feed("##")
    assert got == [[ProfitRow("2493", 178.05, 1368.0, 180.0, 178000.0, "cash")]]
