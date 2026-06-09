# backend/services/capital_reply.py
"""解析群益 OnNewData(bstrData)逗號分隔回報。純函式。

索引依官方範例 Reply.py 註解(spec §4.5)。狀態 enum 完整值為開放項,
故保留 status_raw,_STATUS 對照表 M1 對文件後再補。
"""
from __future__ import annotations
from pydantic import BaseModel

# 委託狀態對照(暫定,M1 對 12.回報.docx 後修正;未命中回原值)
_STATUS = {
    "0": "委託成功",
    "1": "部分成交",
    "2": "全部成交",
    "4": "已刪單",
    "5": "失敗",
}


class ReplyRecord(BaseModel):
    seq_no: str | None = None
    kind: str | None = None        # 委託種類
    status_raw: str | None = None
    status_label: str | None = None
    stock_no: str | None = None
    book_no: str | None = None
    price: float | None = None
    qty: int = 0
    error: str | None = None
    raw: str = ""


def _at(arr: list[str], i: int) -> str | None:
    if -len(arr) <= i < len(arr):
        v = arr[i].strip()
        return v or None
    return None


def parse_onnewdata(bstr_data: str) -> ReplyRecord:
    arr = bstr_data.split(",")
    price_s = _at(arr, 11)
    qty_s = _at(arr, 20)
    status_raw = _at(arr, 3)
    return ReplyRecord(
        seq_no=_at(arr, 0),
        kind=_at(arr, 2),
        status_raw=status_raw,
        status_label=_STATUS.get(status_raw, status_raw) if status_raw else None,
        stock_no=_at(arr, 8),
        book_no=_at(arr, 10),
        price=float(price_s) if price_s else None,
        qty=int(qty_s) if qty_s else 0,
        error=_at(arr, -3),
        raw=bstr_data,
    )
