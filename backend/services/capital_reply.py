# backend/services/capital_reply.py
"""解析群益 OnNewData(bstrData)逗號分隔回報。純函式。

欄位索引依官方 12.回報.docx(對照表見
docs/superpowers/specs/2026-06-10-capital-orders-reply-display-design.md),
已用 2026-06-10 正式環境真實回報逐欄驗證。
"""
from __future__ import annotations
from pydantic import BaseModel
from services.capital_models import SEC_MARKETS

# idx2 Type:回報事件種類
_TYPE = {
    "N": "委託",
    "C": "刪單",
    "U": "改量",
    "P": "改價",
    "D": "成交",
    "B": "改價改量",
    "S": "退單",
}

# idx6 證券 [1:3]:現股/資券別
_SEC_FLAG = {
    "00": "現股", "01": "代資", "02": "代券", "03": "融資",
    "04": "融券", "08": "無券", "20": "零股", "40": "拍賣現股",
}
# idx6 期權 [1]:倉別
_FUT_FLAG = {"Y": "當沖", "N": "新倉", "O": "平倉", "7": "代沖銷"}


class ReplyRecord(BaseModel):
    seq_no: str | None = None
    market: str | None = None        # TS/TA/TL/TP/TC/TF/TO/OF/OO/OS
    status_raw: str | None = None    # idx2 Type 原值
    status_label: str | None = None  # _TYPE 對照(委託/成交/刪單…)
    order_err: str | None = None     # idx3:Y失敗 T逾時 N正常
    buy_sell: str | None = None      # "B"/"S"
    flag_label: str | None = None    # 現股/融資/融券…(期權:當沖/新倉/平倉)
    stock_no: str | None = None
    book_no: str | None = None
    price: float | None = None
    qty: int = 0                     # 證券=股、期權=口;語意依 Type(N委託量/D成交量/U減量數/C剩量)
    after_qty: int | None = None     # idx22(證券)改量後量
    date: str | None = None          # idx23 YYYYMMDD(委託建立日;C/D 事件實測仍為原單日期)
    time: str | None = None          # idx24 HH:MM:SS
    pre_order: bool = False          # idx31 == "B"(預約單)
    error_msg: str | None = None     # idx44(OrderErr=Y 時)
    alt_seq_no: str | None = None    # idx47 尾欄 13 碼(官方欄名待對 docx;預約單與 KeyNo 不同)
    raw: str = ""


def _at(arr: list[str], i: int) -> str | None:
    if -len(arr) <= i < len(arr):
        v = arr[i].strip()
        return v or None
    return None


def _to_int(s: str | None) -> int | None:
    try:
        return int(s) if s else None
    except ValueError:
        return None


def _parse_buysell(market: str | None, bs: str | None) -> tuple[str | None, str | None]:
    """idx6 複合欄:[0]=B/S;證券 [1:3]=資券別、期權 [1]=倉別。
    側別非 B/S 時(如刪單失敗回 "0...")不解 flag,避免回傳語意矛盾的半截資料。
    """
    if not bs:
        return None, None
    side = bs[0] if bs[0] in ("B", "S") else None
    if side is None:
        return None, None
    flag = None
    if market in SEC_MARKETS and len(bs) >= 3:
        flag = _SEC_FLAG.get(bs[1:3])
    elif market and len(bs) >= 2:
        flag = _FUT_FLAG.get(bs[1])
    return side, flag


def parse_onnewdata(bstr_data: str) -> ReplyRecord:
    arr = bstr_data.split(",")
    market = _at(arr, 1)
    status_raw = _at(arr, 2)
    buy_sell, flag_label = _parse_buysell(market, _at(arr, 6))
    price_s = _at(arr, 11)
    try:
        price = float(price_s) if price_s else None
    except ValueError:
        price = None
    return ReplyRecord(
        seq_no=_at(arr, 0),
        market=market,
        status_raw=status_raw,
        status_label=_TYPE.get(status_raw, status_raw) if status_raw else None,
        order_err=_at(arr, 3),
        buy_sell=buy_sell,
        flag_label=flag_label,
        stock_no=_at(arr, 8),
        book_no=_at(arr, 10),
        price=price,
        qty=_to_int(_at(arr, 20)) or 0,
        after_qty=_to_int(_at(arr, 22)),
        date=_at(arr, 23),
        time=_at(arr, 24),
        pre_order=_at(arr, 31) == "B",
        error_msg=_at(arr, 44),
        alt_seq_no=_at(arr, 47),
        raw=bstr_data,
    )
