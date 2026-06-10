# 群益委託清單:回報解碼+聚合+成交均價+刪改減單 — 實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 群益面板委託分頁變成「每張單一列、聚合狀態」(解碼買賣/資券/時間/預約、累計成交+均價、張股口換算、補名稱、只顯證券),活單可刪單/改價/減量。

**Architecture:** 方案 A — 後端解碼+聚合(`capital_reply` 純函式解碼 → `capital_store` 按 13 碼委託序號聚合 → route 過濾證券+`get_symbol` 補名稱),前端笨渲染(`lib/capital-orders.ts` 純函式 view-model + `OrdersList.tsx` 元件)。刪改減走與送單相同的 COM 佇列+安全閘+稽核,結果靠 OnNewData 回報自然刷新(單一資料流)。

**Tech Stack:** FastAPI + comtypes(SKCOM)+ pydantic;React + TypeScript + vitest。Spec:`docs/superpowers/specs/2026-06-10-capital-orders-reply-display-design.md`(欄位對照表在 spec,實作前先讀)。

**慣例提醒:**
- 後端測試:`cd backend && ./.venv/Scripts/python.exe -m pytest tests/ -q`
- 前端:`cd frontend && npx vitest run`、`npx tsc --noEmit`;**前端只測 `lib/` 純函式,不測 component**(repo 慣例)
- route 測試用 `monkeypatch.setattr(factory, "get_capital", ...)`(module-ref,見 `tests/test_capital_route.py`)
- 真實 fixture 的分公司/帳號欄(idx4/5)已匿名化為 `9999/0000000`,不影響解碼(那兩欄不參與)

---

### Task 1: `capital_reply` 解碼擴充

**Files:**
- Modify: `backend/services/capital_reply.py`(整檔重寫)
- Test: `backend/tests/test_capital_reply.py`(整檔重寫)

- [ ] **Step 1: 重寫測試(真實 raw fixture + 逐欄斷言)**

`backend/tests/test_capital_reply.py` 整檔替換為:

```python
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
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_capital_reply.py -q`
Expected: FAIL(`ReplyRecord` 無 `market`/`flag_label` 等欄位、`status_label` 是舊數字對照)

- [ ] **Step 3: 重寫 `capital_reply.py`**

整檔替換為:

```python
# backend/services/capital_reply.py
"""解析群益 OnNewData(bstrData)逗號分隔回報。純函式。

欄位索引依官方 12.回報.docx(對照表見
docs/superpowers/specs/2026-06-10-capital-orders-reply-display-design.md),
已用 2026-06-10 正式環境真實回報逐欄驗證。
"""
from __future__ import annotations
from pydantic import BaseModel

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

_SEC_MARKETS = {"TS", "TA", "TL", "TP", "TC"}


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
    time: str | None = None          # idx24 HH:MM:SS
    pre_order: bool = False          # idx31 == "B"(預約單)
    error_msg: str | None = None     # idx44(OrderErr=Y 時)
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
    """idx6 複合欄:[0]=B/S;證券 [1:3]=資券別、期權 [1]=倉別。"""
    if not bs:
        return None, None
    side = bs[0] if bs[0] in ("B", "S") else None
    flag = None
    if market in _SEC_MARKETS and len(bs) >= 3:
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
        time=_at(arr, 24),
        pre_order=_at(arr, 31) == "B",
        error_msg=_at(arr, 44),
        raw=bstr_data,
    )
```

- [ ] **Step 4: 跑測試確認通過**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_capital_reply.py -q`
Expected: 7 passed

- [ ] **Step 5: 跑全套看連鎖破壞**

Run: `./.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: `test_capital_store.py` / `test_capital_client_reply.py` 可能 FAIL(依賴舊 `_STATUS` 數字對照,例如 fixture 用 `"...,0,..."` 期待 `委託成功`)— **先記下失敗清單,Task 2 一起修**;其餘必須綠。

- [ ] **Step 6: Commit**

```bash
git add backend/services/capital_reply.py backend/tests/test_capital_reply.py
git commit -m "feat(capital): 回報解碼對齊官方 spec(Type/OrderErr/買賣/資券/時間/預約)"
```

---

### Task 2: `capital_store` 聚合 + `OrderRecord` 聚合形狀

**Files:**
- Modify: `backend/services/capital_models.py:50-58`(OrderRecord 重定義)
- Modify: `backend/services/capital_store.py`(聚合邏輯)
- Test: `backend/tests/test_capital_store.py`(整檔重寫)
- Modify(若 Task 1 Step 5 有壞): `backend/tests/test_capital_client_reply.py`

- [ ] **Step 1: 重寫 store 測試**

`backend/tests/test_capital_store.py` 整檔替換為:

```python
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
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_capital_store.py -q`
Expected: FAIL(OrderRecord 無 `order_qty`/`unit` 等;store 是覆蓋不是聚合)

- [ ] **Step 3: 重定義 `OrderRecord`(capital_models.py)**

把現有 `class OrderRecord`(`capital_models.py:50-58`)整段替換為:

```python
class OrderRecord(BaseModel):
    """委託清單一列 = 一張單的聚合狀態(key=13碼委託序號)。qty 已換算顯示單位。"""
    seq_no: str
    stock_no: str | None = None
    name: str = ""                    # route enrich 填,store 不管
    market: str | None = None
    buy_sell: str | None = None       # "B"/"S"
    flag_label: str | None = None     # 現股/融資/融券…
    book_no: str | None = None
    status_raw: str | None = None     # 最新事件 Type
    status_label: str | None = None   # 預約中/委託成功/部分成交/全部成交/已刪單/失敗/逾時/退單
    price: float | None = None        # 委託價(P/B 更新)
    avg_fill_price: float | None = None
    order_qty: int = 0                # 顯示單位(張/股/口)
    filled_qty: int = 0
    unit: str = "張"
    time: str | None = None           # 最新事件 HH:MM:SS
    pre_order: bool = False
    error_msg: str | None = None
    raw: str = ""                     # 最新事件原始字串(debug)
```

- [ ] **Step 4: 重寫 `capital_store.py` 聚合**

整檔替換為:

```python
# backend/services/capital_store.py
"""群益委託/部位記憶體快取(執行緒安全)。COM 事件回呼更新它;REST 讀它。

委託聚合:key = 13 碼委託序號(KeyNo)。同標的不同單絕不合併;
合併的只有同一張單自己的 委託/成交/刪改 事件。
重啟後靠 SKReplyLib_ConnectByID 的當日 backlog 重播重建,無需持久化。
"""
from __future__ import annotations
import threading
from dataclasses import dataclass, field
from services.capital_models import OrderRecord, Position
from services.capital_reply import ReplyRecord

_SEC_LOT_MARKETS = {"TS", "TA", "TP"}        # 整股:股 → 張(÷1000)
_FUT_MARKETS = {"TF", "TO", "OF", "OO"}      # 口

# 狀態只進不退(防 backlog 重播亂序降級)
_RANK = {
    "預約中": 1, "委託成功": 1, "改價": 1, "改量": 1, "改價改量": 1,
    "部分成交": 2,
    "全部成交": 3, "已刪單": 3, "失敗": 3, "逾時": 3, "退單": 3,
}


@dataclass
class _Agg:
    seq_no: str
    stock_no: str | None = None
    market: str | None = None
    buy_sell: str | None = None
    flag_label: str | None = None
    book_no: str | None = None
    status_raw: str | None = None
    status_label: str | None = None
    price: float | None = None
    order_qty: int = 0            # 原始單位(股/口)
    filled_qty: int = 0
    fill_value: float = 0.0       # Σ(成交價×量),算均價用
    time: str | None = None
    pre_order: bool = False
    error_msg: str | None = None
    raw: str = ""


class CapitalStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._orders: dict[str, _Agg] = {}
        self._order_seq: list[str] = []           # 到達順序
        self._positions: dict[str, Position] = {}

    def _set_status(self, a: _Agg, label: str) -> None:
        if _RANK.get(label, 0) >= _RANK.get(a.status_label or "", 0):
            a.status_label = label

    def apply_reply(self, rec: ReplyRecord) -> None:
        if not rec.seq_no:
            return
        with self._lock:
            a = self._orders.get(rec.seq_no)
            if a is None:
                a = _Agg(seq_no=rec.seq_no)
                self._orders[rec.seq_no] = a
                self._order_seq.append(rec.seq_no)

            # 共通欄位:有值就更新
            for f in ("stock_no", "market", "buy_sell", "flag_label", "book_no"):
                v = getattr(rec, f)
                if v:
                    setattr(a, f, v)
            if rec.pre_order:
                a.pre_order = True
            if rec.time:
                a.time = rec.time
            a.status_raw = rec.status_raw
            a.raw = rec.raw

            t = rec.status_raw
            if rec.order_err in ("Y", "T"):
                a.error_msg = rec.error_msg or a.error_msg
                self._set_status(a, "失敗" if rec.order_err == "Y" else "逾時")
            elif t == "N":
                a.order_qty = rec.qty or a.order_qty
                if rec.price is not None:
                    a.price = rec.price
                self._set_status(a, "預約中" if rec.pre_order else "委託成功")
            elif t == "D":
                a.filled_qty += rec.qty
                if rec.price is not None:
                    a.fill_value += rec.price * rec.qty
                full = a.order_qty > 0 and a.filled_qty >= a.order_qty
                self._set_status(a, "全部成交" if (full or a.order_qty == 0) else "部分成交")
            elif t == "C":
                # C 的 qty=原委託剩量,order/filled 不動
                self._set_status(a, "已刪單")
            elif t == "U":
                a.order_qty = rec.after_qty if rec.after_qty is not None else max(a.order_qty - rec.qty, 0)
                self._set_status(a, "改量")
            elif t == "P":
                if rec.price is not None:
                    a.price = rec.price
                self._set_status(a, "改價")
            elif t == "B":
                if rec.price is not None:
                    a.price = rec.price
                if rec.after_qty is not None:
                    a.order_qty = rec.after_qty
                self._set_status(a, "改價改量")
            elif t == "S":
                self._set_status(a, "退單")

    def _to_record(self, a: _Agg) -> OrderRecord:
        if a.market in _SEC_LOT_MARKETS or a.market is None:
            div, unit = 1000, "張"
        elif a.market in _FUT_MARKETS:
            div, unit = 1, "口"
        else:                                    # TL/TC 零股
            div, unit = 1, "股"
        avg = (a.fill_value / a.filled_qty) if a.filled_qty > 0 else None
        return OrderRecord(
            seq_no=a.seq_no, stock_no=a.stock_no, market=a.market,
            buy_sell=a.buy_sell, flag_label=a.flag_label, book_no=a.book_no,
            status_raw=a.status_raw, status_label=a.status_label,
            price=a.price, avg_fill_price=round(avg, 4) if avg is not None else None,
            order_qty=a.order_qty // div, filled_qty=a.filled_qty // div, unit=unit,
            time=a.time, pre_order=a.pre_order, error_msg=a.error_msg, raw=a.raw,
        )

    def orders(self) -> list[OrderRecord]:
        with self._lock:
            return [self._to_record(self._orders[s]) for s in reversed(self._order_seq)]

    def remaining_shares(self, seq_no: str) -> int | None:
        """改價金額閘用:未成交量(原始單位,股/口)。查無此單回 None。"""
        with self._lock:
            a = self._orders.get(seq_no)
            if a is None:
                return None
            return max(a.order_qty - a.filled_qty, 0)

    def set_positions(self, positions: list[Position]) -> None:
        with self._lock:
            self._positions = {p.stock_no: p for p in positions}

    def positions(self) -> list[Position]:
        with self._lock:
            return list(self._positions.values())

    def position_for(self, stock_no: str) -> Position | None:
        with self._lock:
            return self._positions.get(stock_no)
```

- [ ] **Step 5: 跑 store 測試**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_capital_store.py -q`
Expected: 10 passed

- [ ] **Step 6: 跑全套、修連鎖**

Run: `./.venv/Scripts/python.exe -m pytest tests/ -q`

預期連鎖與修法:
- `tests/test_capital_client_reply.py`:若 fixture 用舊數字狀態碼或斷言 `OrderRecord.qty` → 改用本計畫 Task 2 Step 1 的 `_evt()` 風格構造 + 斷言 `order_qty`/`filled_qty`。**保留該檔驗證的行為**(OnNewData → store → broadcast 串接),只改資料形狀。
- `tests/test_capital_route.py` 的 `FakeClient._Store.orders()` 回 `OrderRecord(seq_no="A1", stock_no="2330", status_label="委託成功")` → 仍合法(其餘欄位有 default),`assert ...["seq_no"] == "A1"` 不壞;若有斷言 `qty` 改 `order_qty`。
- 不允許刪測試了事;改完必須全綠。

- [ ] **Step 7: Commit**

```bash
git add backend/services/capital_models.py backend/services/capital_store.py backend/tests/
git commit -m "feat(capital): store 按委託序號聚合(成交累計+量加權均價+張股口換算)"
```

---

### Task 3: orders route 過濾證券 + 名稱 enrich

**Files:**
- Modify: `backend/routes/capital.py:23-28`(capital_orders)
- Test: `backend/tests/test_capital_route.py`(加測試)

- [ ] **Step 1: 加失敗測試**

在 `backend/tests/test_capital_route.py` 末尾加:

```python
def test_orders_filters_futures_and_enriches_name(monkeypatch):
    from services.capital_models import OrderRecord

    class _Store:
        def orders(self):
            return [
                OrderRecord(seq_no="S1", stock_no="3357", market="TS", status_label="已刪單"),
                OrderRecord(seq_no="F1", stock_no="QEF06", market="TF", status_label="委託成功"),
            ]
        def positions(self):
            return []

    fake = FakeClient()
    fake.store = _Store()
    c = _client(monkeypatch, fake)

    import routes.capital as capital_route
    monkeypatch.setattr(capital_route, "_symbol_name", lambda code: "臺慶科" if code == "3357" else "")

    orders = c.get("/api/capital/orders").json()["orders"]
    assert len(orders) == 1                      # TF 被過濾
    assert orders[0]["seq_no"] == "S1"
    assert orders[0]["name"] == "臺慶科"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_capital_route.py -q`
Expected: 新測試 FAIL(無 `_symbol_name`、TF 沒被過濾)

- [ ] **Step 3: 改 `routes/capital.py`**

把 `capital_orders` 函式替換,並在 import 區後加 helper:

```python
_SEC_MARKETS = {"TS", "TA", "TL", "TP", "TC"}


def _symbol_name(stock_no: str | None) -> str:
    """代號→名稱;查無(期貨代號/未爬)回空字串。獨立函式方便測試 monkeypatch。"""
    if not stock_no:
        return ""
    from services.local_store import get_local_store
    row = get_local_store().market.get_symbol(stock_no)
    return row["name"] if row else ""


@router.get("/api/capital/orders")
async def capital_orders() -> dict:
    c = capital_factory.get_capital()
    if c is None:
        return {"orders": []}
    out = []
    for o in c.store.orders():
        # v1 委託清單只顯證券;期權回報照存不顯(未來期貨面板用)。market 缺值寬鬆放行。
        if o.market is not None and o.market not in _SEC_MARKETS:
            continue
        o.name = _symbol_name(o.stock_no)
        out.append(o.model_dump(mode="json"))
    return {"orders": out}
```

- [ ] **Step 4: 跑測試確認通過 + 全套綠**

Run: `./.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: 全綠

- [ ] **Step 5: Commit**

```bash
git add backend/routes/capital.py backend/tests/test_capital_route.py
git commit -m "feat(capital): 委託清單只顯證券+補股票名稱"
```

---

### Task 4: 安全閘三 gate + 稽核 action 欄

**Files:**
- Modify: `backend/services/capital_safety.py`
- Modify: `backend/services/capital_audit.py`
- Modify: `backend/services/capital_models.py`(三個請求 model)
- Test: `backend/tests/test_capital_safety.py`、`backend/tests/test_capital_audit.py`(加測試)

- [ ] **Step 1: 加失敗測試(safety)**

在 `backend/tests/test_capital_safety.py` 末尾加:

```python
from services.capital_safety import check_cancel, check_correct_price, check_decrease


def _cfg(enabled=True, max_amount=100000.0):
    return SafetyConfig(order_enabled=enabled, max_qty=1, max_amount=max_amount)


def test_cancel_only_needs_master_switch():
    assert check_cancel(_cfg(True)).allowed is True
    g = check_cancel(_cfg(False))
    assert g.allowed is False and "總開關" in g.reason


def test_correct_price_checks_amount_with_remaining():
    # 新價 × 未成交股數 超過上限要擋
    g = check_correct_price(200.0, 1000, _cfg(max_amount=100000))
    assert g.allowed is False and "超過上限" in g.reason
    assert check_correct_price(90.0, 1000, _cfg(max_amount=100000)).allowed is True
    assert check_correct_price(90.0, 1000, _cfg(False)).allowed is False


def test_decrease_needs_positive_qty():
    assert check_decrease(1, _cfg()).allowed is True
    assert check_decrease(0, _cfg()).allowed is False
    assert check_decrease(1, _cfg(False)).allowed is False
```

- [ ] **Step 2: 加失敗測試(audit action 欄)**

在 `backend/tests/test_capital_audit.py` 末尾加:

```python
def test_audit_writes_action_field(tmp_path):
    import json
    from services.capital_models import CancelOrderRequest
    p = tmp_path / "a.jsonl"
    capital_audit.write(p, env="prod", req=CancelOrderRequest(seq_no="S1"),
                        blocked="下單總開關關閉(CAPITAL_ORDER_ENABLED=false)", action="cancel")
    entry = json.loads(p.read_text(encoding="utf-8").splitlines()[-1])
    assert entry["action"] == "cancel"
    assert entry["req"]["seq_no"] == "S1"
```

(該檔現有 import 樣式照舊;若它 `from services import capital_audit` 就沿用。)

- [ ] **Step 3: 跑兩檔測試確認失敗**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_capital_safety.py tests/test_capital_audit.py -q`
Expected: FAIL(函式/參數不存在)

- [ ] **Step 4: 實作**

`backend/services/capital_models.py` 末尾(`Position` class 之前或之後皆可)加:

```python
class CancelOrderRequest(BaseModel):
    seq_no: str


class CorrectPriceRequest(BaseModel):
    seq_no: str
    price: float


class DecreaseQtyRequest(BaseModel):
    seq_no: str
    qty: int  # 張(與 SendStockOrder.nQty 同慣例;首次實測對群益 App 驗)
```

`backend/services/capital_safety.py` 末尾加:

```python
def _master(cfg: SafetyConfig) -> GateResult | None:
    if not cfg.order_enabled:
        return GateResult(False, "下單總開關關閉(CAPITAL_ORDER_ENABLED=false)")
    return None


def check_cancel(cfg: SafetyConfig) -> GateResult:
    """刪單只降風險:僅過總開關。"""
    return _master(cfg) or GateResult(True)


def check_correct_price(new_price: float, remaining_shares: int, cfg: SafetyConfig) -> GateResult:
    """改價改變曝險:總開關 + 新價×未成交股數過金額閘。"""
    blocked = _master(cfg)
    if blocked:
        return blocked
    if new_price <= 0:
        return GateResult(False, "價格必須大於 0")
    est = new_price * remaining_shares
    if cfg.max_amount and est > cfg.max_amount:
        return GateResult(False, f"預估金額 {est:.0f} 超過上限 {cfg.max_amount:.0f}")
    return GateResult(True)


def check_decrease(qty_lots: int, cfg: SafetyConfig) -> GateResult:
    """減量只降風險:總開關 + 量>0。"""
    blocked = _master(cfg)
    if blocked:
        return blocked
    if qty_lots <= 0:
        return GateResult(False, "減量必須大於 0")
    return GateResult(True)
```

`backend/services/capital_audit.py` 的 `write` 加 `action` 參數:

```python
def write(
    path: Path,
    *,
    env: str,
    req,                          # 任一 pydantic BaseModel(下單/刪/改/減請求)
    blocked: str | None = None,
    result: OrderResult | None = None,
    action: str = "order",        # order / cancel / correct_price / decrease
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "env": env,
        "action": action,
        "req": req.model_dump(mode="json"),
        "blocked": blocked,
        "result": result.model_dump(mode="json") if result else None,
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
```

(同檔頂部 `from services.capital_models import StockOrderRequest, OrderResult` 的 `StockOrderRequest` import 已不需要可移除,`req` 改鴨子型別。)

- [ ] **Step 5: 跑測試 + 全套**

Run: `./.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: 全綠

- [ ] **Step 6: Commit**

```bash
git add backend/services/capital_safety.py backend/services/capital_audit.py backend/services/capital_models.py backend/tests/
git commit -m "feat(capital): 刪/改/減安全閘+稽核 action 欄"
```

---

### Task 5: COM 三支寫入 + client 三 method

**Files:**
- Modify: `backend/services/capital_com.py`(protocol + 實作)
- Modify: `backend/services/capital_client.py`
- Test: `backend/tests/test_capital_client.py`(加測試)

- [ ] **Step 1: 加失敗測試**

在 `backend/tests/test_capital_client.py`:

`FakeCom` 加三方法(維持既有 style):

```python
    def cancel_order(self, user_id, full_account, seq_no):
        self.sent.append(("cancel", seq_no))
        return ("OK", 0)

    def correct_price(self, user_id, full_account, seq_no, price):
        self.sent.append(("correct_price", seq_no, price))
        return ("OK", 0)

    def decrease_qty(self, user_id, full_account, seq_no, qty):
        self.sent.append(("decrease", seq_no, qty))
        return ("OK", 0)
```

檔尾加測試:

```python
from services.capital_models import CancelOrderRequest, CorrectPriceRequest, DecreaseQtyRequest
from services.capital_reply import parse_onnewdata


def _ready_client(com, audit_path, max_amount=2_000_000.0):
    client = CapitalClient(
        com, user_id="u", password="p", full_account="1234567890A",
        env="test", safety=SafetyConfig(order_enabled=True, max_qty=5, max_amount=max_amount),
        audit_path=audit_path,
    )
    # 不跑 COM 執行緒:直接標 ok + 綁事件圈,寫入走佇列前的閘與佇列投遞可同步驗
    client._status = "ok"
    return client


def _drain(client):
    """同步取出佇列裡的 COM 命令並執行(測試替代 COM 執行緒)。"""
    import asyncio
    while not client._cmd_q.empty():
        fn, fut = client._cmd_q.get_nowait()
        fut.get_loop().call_soon_threadsafe(fut.set_result, fn())


def _run_write(client, coro):
    import asyncio

    async def _go():
        task = asyncio.ensure_future(coro)
        await asyncio.sleep(0)      # 讓 coro 先把命令投進佇列
        _drain(client)
        return await task

    return asyncio.run(_go())


def test_cancel_blocked_when_switch_off(tmp_path):
    com = FakeCom()
    client = _client(com, enabled=False, audit_path=tmp_path / "a.jsonl")
    res = asyncio.run(client.cancel_stock_order(CancelOrderRequest(seq_no="S1")))
    assert res.ok is False and "總開關" in res.message
    assert com.sent == []


def test_cancel_goes_through_com(tmp_path):
    com = FakeCom()
    client = _ready_client(com, tmp_path / "a.jsonl")
    client._loop = __import__("asyncio").new_event_loop()  # placeholder;_run_write 內會用真 loop
    res = _run_write_with_fresh_loop(client, com)
    assert res.ok is True
    assert ("cancel", "S1") in com.sent


def _run_write_with_fresh_loop(client, com):
    import asyncio

    async def _go():
        client._loop = asyncio.get_running_loop()
        task = asyncio.ensure_future(client.cancel_stock_order(CancelOrderRequest(seq_no="S1")))
        await asyncio.sleep(0)
        while not client._cmd_q.empty():
            fn, fut = client._cmd_q.get_nowait()
            fut.set_result(fn())
        return await task

    return asyncio.run(_go())


def test_correct_price_uses_remaining_shares_for_amount_gate(tmp_path):
    com = FakeCom()
    client = _ready_client(com, tmp_path / "a.jsonl", max_amount=100_000.0)
    # 造一張未成交 1000 股的單
    arr = ["" for _ in range(47)]
    arr[0], arr[1], arr[2], arr[3], arr[6], arr[8], arr[11], arr[20] = (
        "S9", "TS", "N", "N", "B00R2", "3357", "90.0000", "1000")
    client.store.apply_reply(parse_onnewdata(",".join(arr)))
    # 新價 200 × 1000 股 = 200,000 > 100,000 → 擋
    res = asyncio.run(client.correct_stock_price(CorrectPriceRequest(seq_no="S9", price=200.0)))
    assert res.ok is False and "超過上限" in res.message
    assert com.sent == []


def test_correct_price_unknown_seq_rejected(tmp_path):
    com = FakeCom()
    client = _ready_client(com, tmp_path / "a.jsonl")
    res = asyncio.run(client.correct_stock_price(CorrectPriceRequest(seq_no="nope", price=10.0)))
    assert res.ok is False and "找不到" in res.message
    assert com.sent == []
```

(注意:`test_cancel_goes_through_com` 直接用 `_run_write_with_fresh_loop`,`_ready_client` 後不需要先設 `_loop`——helper 內會綁。`_drain`/`_run_write` 兩個 helper 若未被其他測試用到,留 `_run_write_with_fresh_loop` 一個即可,刪掉未用的。)

- [ ] **Step 2: 跑測試確認失敗**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_capital_client.py -q`
Expected: FAIL(`cancel_stock_order` 等不存在)

- [ ] **Step 3: `capital_com.py` 加 protocol + 實作**

Protocol(`CapitalCom`)`connect_reply` 之後加:

```python
    def cancel_order(self, user_id: str, full_account: str, seq_no: str) -> tuple[str, int]: ...
    def correct_price(self, user_id: str, full_account: str, seq_no: str, price: float) -> tuple[str, int]: ...
    def decrease_qty(self, user_id: str, full_account: str, seq_no: str, qty: int) -> tuple[str, int]: ...
```

`SkcomCapitalCom` 的 `send_stock_order` 之後加(簽名依官方範例 `StockOrder.py`,同步 `bAsync=0`):

```python
    def cancel_order(self, user_id: str, full_account: str, seq_no: str) -> tuple[str, int]:
        message, code = self._order.CancelOrderBySeqNo(user_id, 0, full_account, seq_no)
        return message, code

    def correct_price(self, user_id: str, full_account: str, seq_no: str, price: float) -> tuple[str, int]:
        # 末參數 nTradeType=0(ROD),同官方範例
        message, code = self._order.CorrectPriceBySeqNo(user_id, 0, full_account, seq_no, f"{price:.2f}", 0)
        return message, code

    def decrease_qty(self, user_id: str, full_account: str, seq_no: str, qty: int) -> tuple[str, int]:
        # qty 單位=張(與 SendStockOrder.nQty 同慣例;首次實測對群益 App 驗)
        message, code = self._order.DecreaseOrderBySeqNo(user_id, 0, full_account, seq_no, qty)
        return message, code
```

- [ ] **Step 4: `capital_client.py` 加三 method(DRY:抽共用執行器)**

import 區補:

```python
from services.capital_models import (
    StockOrderRequest, OrderResult,
    CancelOrderRequest, CorrectPriceRequest, DecreaseQtyRequest,
)
from services.capital_safety import (
    SafetyConfig, check_stock_order, check_cancel, check_correct_price, check_decrease,
)
```

`submit_stock_order` 改用共用執行器,並加三 method(整段替換 `submit_stock_order`):

```python
    async def _execute_write(self, *, action: str, req, gate, com_call) -> OrderResult:
        """寫入操作共用骨架:閘 → ready 檢查 → COM 佇列 → 稽核。"""
        if not gate.allowed:
            capital_audit.write(self._audit_path, env=self._env, req=req,
                                blocked=gate.reason, action=action)
            return OrderResult(ok=False, code=-1, message=gate.reason)
        if self._status != "ok" or self._loop is None:
            return OrderResult(ok=False, code=-1, message="群益未就緒(尚未登入或憑證失敗)")

        fut: asyncio.Future = self._loop.create_future()
        self._cmd_q.put((com_call, fut))
        message, code = await fut
        result = OrderResult(
            ok=(code == 0),
            code=code,
            message=f"{self._com.return_code_message(code)} {message}".strip(),
        )
        capital_audit.write(self._audit_path, env=self._env, req=req,
                            result=result, action=action)
        return result

    async def submit_stock_order(self, req: StockOrderRequest) -> OrderResult:
        def _do() -> tuple[str, int]:
            fields = to_stockorder_fields(req, self._full_account)
            return self._com.send_stock_order(self._user_id, fields)

        return await self._execute_write(
            action="order", req=req,
            gate=check_stock_order(req, self._safety), com_call=_do)

    async def cancel_stock_order(self, req: CancelOrderRequest) -> OrderResult:
        def _do() -> tuple[str, int]:
            return self._com.cancel_order(self._user_id, self._full_account, req.seq_no)

        return await self._execute_write(
            action="cancel", req=req,
            gate=check_cancel(self._safety), com_call=_do)

    async def correct_stock_price(self, req: CorrectPriceRequest) -> OrderResult:
        remaining = self.store.remaining_shares(req.seq_no)
        if remaining is None:
            return OrderResult(ok=False, code=-1, message=f"找不到委託 {req.seq_no}")

        def _do() -> tuple[str, int]:
            return self._com.correct_price(self._user_id, self._full_account, req.seq_no, req.price)

        return await self._execute_write(
            action="correct_price", req=req,
            gate=check_correct_price(req.price, remaining, self._safety), com_call=_do)

    async def decrease_stock_qty(self, req: DecreaseQtyRequest) -> OrderResult:
        def _do() -> tuple[str, int]:
            return self._com.decrease_qty(self._user_id, self._full_account, req.seq_no, req.qty)

        return await self._execute_write(
            action="decrease", req=req,
            gate=check_decrease(req.qty, self._safety), com_call=_do)
```

- [ ] **Step 5: 跑測試 + 全套**

Run: `./.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: 全綠(既有 `test_blocked_when_switch_off_does_not_touch_com` 等行為不變)

- [ ] **Step 6: Commit**

```bash
git add backend/services/capital_com.py backend/services/capital_client.py backend/tests/test_capital_client.py
git commit -m "feat(capital): client 刪單/改價/減量(共用寫入骨架+閘+稽核)"
```

---

### Task 6: 三個寫入端點

**Files:**
- Modify: `backend/routes/capital.py`
- Test: `backend/tests/test_capital_route.py`(加測試)

- [ ] **Step 1: 加失敗測試**

`backend/tests/test_capital_route.py`:`FakeClient` 加三 async method:

```python
    async def cancel_stock_order(self, req):
        return OrderResult(ok=True, code=0, message="刪單成功", seq_no=req.seq_no)

    async def correct_stock_price(self, req):
        return OrderResult(ok=True, code=0, message="改價成功", seq_no=req.seq_no)

    async def decrease_stock_qty(self, req):
        return OrderResult(ok=True, code=0, message="減量成功", seq_no=req.seq_no)
```

檔尾加:

```python
def test_cancel_correct_decrease_endpoints(monkeypatch):
    c = _client(monkeypatch, FakeClient())
    r = c.post("/api/capital/order/cancel", json={"seq_no": "S1"})
    assert r.status_code == 200 and r.json()["ok"] is True
    r = c.post("/api/capital/order/correct-price", json={"seq_no": "S1", "price": 100.0})
    assert r.status_code == 200 and r.json()["ok"] is True
    r = c.post("/api/capital/order/decrease", json={"seq_no": "S1", "qty": 1})
    assert r.status_code == 200 and r.json()["ok"] is True


def test_write_endpoints_503_when_disabled(monkeypatch):
    c = _client(monkeypatch, None)
    assert c.post("/api/capital/order/cancel", json={"seq_no": "S1"}).status_code == 503
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_capital_route.py -q`
Expected: 404(端點不存在)

- [ ] **Step 3: 實作端點(`routes/capital.py`)**

import 補:

```python
from services.capital_models import (
    StockOrderRequest, CancelOrderRequest, CorrectPriceRequest, DecreaseQtyRequest,
)
```

檔尾加:

```python
def _require_capital():
    c = capital_factory.get_capital()
    if c is None:
        raise HTTPException(503, detail={"error": "capital_disabled"})
    return c


@router.post("/api/capital/order/cancel")
async def capital_order_cancel(req: CancelOrderRequest) -> dict:
    res = await _require_capital().cancel_stock_order(req)
    return res.model_dump(mode="json")


@router.post("/api/capital/order/correct-price")
async def capital_order_correct_price(req: CorrectPriceRequest) -> dict:
    res = await _require_capital().correct_stock_price(req)
    return res.model_dump(mode="json")


@router.post("/api/capital/order/decrease")
async def capital_order_decrease(req: DecreaseQtyRequest) -> dict:
    res = await _require_capital().decrease_stock_qty(req)
    return res.model_dump(mode="json")
```

(既有 `capital_order_stock` 可順手改用 `_require_capital()`,行為不變。)

- [ ] **Step 4: 跑全套**

Run: `./.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: 全綠

- [ ] **Step 5: Commit**

```bash
git add backend/routes/capital.py backend/tests/test_capital_route.py
git commit -m "feat(capital): 刪單/改價/減量端點"
```

---

### Task 7: 前端 view-model 純函式 + api

**Files:**
- Create: `frontend/src/lib/capital-orders.ts`
- Create: `frontend/src/lib/capital-orders.test.ts`
- Modify: `frontend/src/lib/api.ts:344-348`(CapitalOrder)+ api surface

- [ ] **Step 1: 寫失敗測試**

`frontend/src/lib/capital-orders.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import { buildOrderRow, type CapitalOrder } from "./capital-orders";

const base: CapitalOrder = {
  seq_no: "2313091595225", stock_no: "3357", name: "臺慶科", market: "TS",
  buy_sell: "B", flag_label: "現股", book_no: null,
  status_raw: "C", status_label: "已刪單",
  price: 293, avg_fill_price: null, order_qty: 1, filled_qty: 0, unit: "張",
  time: "14:59:48", pre_order: true, error_msg: null, raw: "",
};

describe("buildOrderRow", () => {
  it("組標題與買賣樣式", () => {
    const r = buildOrderRow(base);
    expect(r.title).toBe("3357 臺慶科");
    expect(r.sideLabel).toBe("買");
    expect(r.sideClass).toBe("text-bull");
    expect(r.flagLabel).toBe("現股");
    expect(r.preOrder).toBe(true);
  });

  it("名稱缺值只顯代號", () => {
    const r = buildOrderRow({ ...base, name: "" });
    expect(r.title).toBe("3357");
  });

  it("賣單綠色", () => {
    const r = buildOrderRow({ ...base, buy_sell: "S" });
    expect(r.sideLabel).toBe("賣");
    expect(r.sideClass).toBe("text-bear");
  });

  it("量文字 = 成交/委託 + 單位;均價有成交才出現", () => {
    const r = buildOrderRow({ ...base, status_label: "部分成交", order_qty: 4, filled_qty: 3, avg_fill_price: 83.65 });
    expect(r.qtyText).toBe("3/4 張");
    expect(r.avgText).toBe("均 83.65");
    expect(buildOrderRow(base).avgText).toBeNull();
  });

  it("活單才可刪改", () => {
    expect(buildOrderRow({ ...base, status_label: "委託成功" }).actionable).toBe(true);
    expect(buildOrderRow({ ...base, status_label: "部分成交" }).actionable).toBe(true);
    expect(buildOrderRow({ ...base, status_label: "預約中" }).actionable).toBe(true);
    expect(buildOrderRow({ ...base, status_label: "全部成交" }).actionable).toBe(false);
    expect(buildOrderRow({ ...base, status_label: "已刪單" }).actionable).toBe(false);
  });

  it("失敗紅字+錯誤訊息", () => {
    const r = buildOrderRow({ ...base, status_label: "失敗", error_msg: "超過漲跌停" });
    expect(r.statusClass).toBe("text-bear");
    expect(r.errorMsg).toBe("超過漲跌停");
  });
});
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npx vitest run src/lib/capital-orders.test.ts`
Expected: FAIL(模組不存在)

- [ ] **Step 3: 實作 `lib/capital-orders.ts`**

```typescript
// 委託列 view-model:把後端聚合的 CapitalOrder 轉成可渲染欄位。純函式,可測。
export interface CapitalOrder {
  seq_no: string; stock_no: string | null; name: string; market: string | null;
  buy_sell: string | null; flag_label: string | null; book_no: string | null;
  status_raw: string | null; status_label: string | null;
  price: number | null; avg_fill_price: number | null;
  order_qty: number; filled_qty: number; unit: string;
  time: string | null; pre_order: boolean; error_msg: string | null; raw: string;
}

export interface OrderRowVM {
  seqNo: string;
  title: string;            // "3357 臺慶科"(無名稱時只代號)
  sideLabel: string;        // 買/賣/—
  sideClass: string;        // text-bull / text-bear / text-ink-dim
  flagLabel: string | null; // 現股/融資/融券…
  statusLabel: string;      // 預約中/委託成功/部分成交/全部成交/已刪單/失敗/逾時/退單
  statusClass: string;      // 失敗類紅字
  priceText: string;        // 委託價
  qtyText: string;          // "3/4 張"
  avgText: string | null;   // "均 83.65"(有成交才有)
  timeText: string | null;
  preOrder: boolean;
  errorMsg: string | null;
  actionable: boolean;      // 活單(預約中/委託成功/部分成交)→ 可刪/改
}

const ACTIVE = new Set(["預約中", "委託成功", "部分成交", "改價", "改量", "改價改量"]);
const FAILED = new Set(["失敗", "逾時", "退單"]);

export function buildOrderRow(o: CapitalOrder): OrderRowVM {
  const title = o.name ? `${o.stock_no ?? ""} ${o.name}`.trim() : (o.stock_no ?? "—");
  const isBuy = o.buy_sell === "B";
  const isSell = o.buy_sell === "S";
  const status = o.status_label ?? "—";
  return {
    seqNo: o.seq_no,
    title,
    sideLabel: isBuy ? "買" : isSell ? "賣" : "—",
    sideClass: isBuy ? "text-bull" : isSell ? "text-bear" : "text-ink-dim",
    flagLabel: o.flag_label,
    statusLabel: status,
    statusClass: FAILED.has(status) ? "text-bear" : "text-ink-muted",
    priceText: o.price != null ? o.price.toFixed(2) : "—",
    qtyText: `${o.filled_qty}/${o.order_qty} ${o.unit}`,
    avgText: o.avg_fill_price != null && o.filled_qty > 0 ? `均 ${o.avg_fill_price.toFixed(2)}` : null,
    timeText: o.time,
    preOrder: o.pre_order,
    errorMsg: o.error_msg,
    actionable: ACTIVE.has(status),
  };
}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `npx vitest run src/lib/capital-orders.test.ts`
Expected: 7 passed

- [ ] **Step 5: 更新 `lib/api.ts`**

`CapitalOrder` interface(`api.ts:344-348`)整段替換為 re-export(單一定義來源):

```typescript
export type { CapitalOrder } from "./capital-orders";
```

(原 interface 刪除;檔內其他使用處 import 不變,TS re-export 即可。)

api surface(`capitalSubmitStock` 之後)加:

```typescript
  capitalCancelOrder: (req: { seq_no: string }) =>
    fetchJSON<CapitalOrderResult>("/api/capital/order/cancel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    }),
  capitalCorrectPrice: (req: { seq_no: string; price: number }) =>
    fetchJSON<CapitalOrderResult>("/api/capital/order/correct-price", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    }),
  capitalDecreaseQty: (req: { seq_no: string; qty: number }) =>
    fetchJSON<CapitalOrderResult>("/api/capital/order/decrease", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    }),
```

(POST 樣式對齊檔內既有 `capitalSubmitStock` 的寫法;若它沒帶 headers 就照它的樣式。)
注意:`api.ts` 內 `export type { CapitalOrder }` 之後,本檔若還有用到 `CapitalOrder` 型別的地方需 `import type { CapitalOrder } from "./capital-orders";`。

- [ ] **Step 6: tsc + vitest 全綠**

Run: `npx tsc --noEmit && npx vitest run`
Expected: 0 errors;全部 pass(`useCapital.ts`/`TradingPanel.tsx` 此時若因 CapitalOrder 欄位改變報錯,先讓 tsc 指出,Task 8 一起修——若錯誤只在 OrdersList 用到的 `qty` 欄位,可暫時容忍到 Task 8;**不可 commit 紅的 tsc**,所以 Step 7 的 commit 若被 tsc 卡住,把 Task 8 Step 1 的 OrdersList 抽檔先做完再一起 commit)

- [ ] **Step 7: Commit**

```bash
git add frontend/src/lib/capital-orders.ts frontend/src/lib/capital-orders.test.ts frontend/src/lib/api.ts
git commit -m "feat(capital): 前端委託列 view-model + 刪改減 api"
```

---

### Task 8: `OrdersList` 元件(含刪/改 UI)+ TradingPanel 接線

**Files:**
- Create: `frontend/src/components/OrdersList.tsx`
- Modify: `frontend/src/components/TradingPanel.tsx`(移除內嵌 OrdersList、接新元件)

- [ ] **Step 1: 建 `frontend/src/components/OrdersList.tsx`**

```tsx
import { useState } from "react";
import { api, type CapitalOrderResult } from "../lib/api";
import { buildOrderRow, type CapitalOrder, type OrderRowVM } from "../lib/capital-orders";

/** 委託清單(聚合列)+ 活單刪/改。結果靠 OnNewData 回報刷新,不樂觀更新。 */
export function OrdersList({ orders, env }: { orders: CapitalOrder[]; env: string }) {
  const [msg, setMsg] = useState<string | null>(null);
  if (orders.length === 0) return <div className="text-xs text-ink-dim py-4 text-center">今日尚無委託</div>;
  return (
    <div className="space-y-0">
      {orders.map((o) => (
        <OrderRow key={o.seq_no} row={buildOrderRow(o)} env={env} onResult={setMsg} />
      ))}
      {msg && <div className="text-center text-xs mt-2 text-ink-muted">{msg}</div>}
    </div>
  );
}

type PendingAction =
  | { kind: "cancel" }
  | { kind: "correct_price"; price: number }
  | { kind: "decrease"; qty: number };

function OrderRow({ row, env, onResult }: { row: OrderRowVM; env: string; onResult: (m: string) => void }) {
  const [editing, setEditing] = useState(false);
  const [price, setPrice] = useState("");
  const [decQty, setDecQty] = useState("");
  const [pending, setPending] = useState<PendingAction | null>(null);

  const doSend = async () => {
    if (!pending) return;
    try {
      let r: CapitalOrderResult;
      if (pending.kind === "cancel") r = await api.capitalCancelOrder({ seq_no: row.seqNo });
      else if (pending.kind === "correct_price") r = await api.capitalCorrectPrice({ seq_no: row.seqNo, price: pending.price });
      else r = await api.capitalDecreaseQty({ seq_no: row.seqNo, qty: pending.qty });
      onResult(`${r.ok ? "✓" : "✗"} ${r.message}`);
    } catch {
      onResult("✗ 送出失敗");
    }
    setPending(null);
    setEditing(false);
  };

  return (
    <div className="border-b border-line py-2.5 text-sm">
      <div className="flex items-center gap-2">
        <span className="font-serif font-medium">{row.title}</span>
        <span className={`text-xs ${row.sideClass}`}>{row.sideLabel}{row.flagLabel ? `·${row.flagLabel}` : ""}</span>
        {row.preOrder && <span className="text-2xs px-1 border border-line text-ink-dim rounded">預約</span>}
        <span className={`ml-auto text-xs px-2 py-0.5 rounded bg-bg-deep ${row.statusClass}`}>{row.statusLabel}</span>
      </div>
      <div className="text-xs text-ink-dim tabular-nums mt-1">
        {row.priceText} · {row.qtyText}{row.avgText ? ` · ${row.avgText}` : ""}{row.timeText ? ` · ${row.timeText}` : ""}
      </div>
      {row.errorMsg && <div className="text-2xs text-bear mt-0.5">{row.errorMsg}</div>}

      {row.actionable && (
        <div className="flex gap-2 mt-1.5 text-xs">
          <button onClick={() => setPending({ kind: "cancel" })}
            className="px-2 py-0.5 border border-line-strong text-ink-muted hover:text-bear hover:border-bear rounded">刪單</button>
          <button onClick={() => setEditing((v) => !v)}
            className="px-2 py-0.5 border border-line-strong text-ink-muted hover:text-ink rounded">改</button>
        </div>
      )}

      {editing && row.actionable && (
        <div className="mt-2 p-2 border border-line rounded space-y-1.5 text-xs">
          <div className="flex items-center gap-2">
            <span className="text-ink-dim w-12">改價</span>
            <input value={price} onChange={(e) => setPrice(e.target.value)} inputMode="decimal" placeholder={row.priceText}
              className="flex-1 bg-bg-deep border border-line px-2 py-1 tabular-nums outline-none focus:border-accent" />
            <button disabled={!Number(price)} onClick={() => setPending({ kind: "correct_price", price: Number(price) })}
              className="px-2 py-1 border border-line-strong disabled:opacity-40 rounded">送出</button>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-ink-dim w-12">減量</span>
            <input value={decQty} onChange={(e) => setDecQty(e.target.value)} inputMode="numeric" placeholder="張"
              className="flex-1 bg-bg-deep border border-line px-2 py-1 tabular-nums outline-none focus:border-accent" />
            <button disabled={!Number(decQty)} onClick={() => setPending({ kind: "decrease", qty: Number(decQty) })}
              className="px-2 py-1 border border-line-strong disabled:opacity-40 rounded">送出</button>
          </div>
        </div>
      )}

      {pending && (
        <ActionConfirm row={row} action={pending} env={env}
          onConfirm={doSend} onClose={() => setPending(null)} />
      )}
    </div>
  );
}

function ActionConfirm({ row, action, env, onConfirm, onClose }: {
  row: OrderRowVM; action: PendingAction; env: string; onConfirm: () => void; onClose: () => void;
}) {
  const prod = env === "prod";
  const desc = action.kind === "cancel" ? "刪單"
    : action.kind === "correct_price" ? `改價 → ${action.price.toFixed(2)}`
    : `減量 ${action.qty} 張`;
  return (
    <>
      <div onClick={onClose} className="fixed inset-0 z-20 bg-bg-deep/85" style={{ backdropFilter: "blur(2px)" }} />
      <div role="dialog" aria-modal="true"
        className={`fixed top-1/2 left-1/2 z-[21] bg-bg-card border p-5 w-[min(340px,90vw)] ${prod ? "border-bull" : "border-line-strong"}`}
        style={{ transform: "translate(-50%, -50%)" }}>
        <h3 className="font-serif font-bold text-lg mb-1">確認{desc.startsWith("刪單") ? "刪單" : "修改委託"}</h3>
        <p className={`text-xs mb-3 ${prod ? "text-bull font-bold" : "text-bear"}`}>
          {prod ? "⚠ 正式環境(真錢)" : env === "test" ? "測試環境" : "環境未知"}
        </p>
        <div className="text-sm space-y-1 tabular-nums">
          <div className="flex justify-between"><span className="text-ink-dim">標的</span><span>{row.title}</span></div>
          <div className="flex justify-between"><span className="text-ink-dim">原委託</span><span>{row.priceText} · {row.qtyText}</span></div>
          <div className="flex justify-between"><span className="text-ink-dim">動作</span><span className="text-bear">{desc}</span></div>
        </div>
        <div className="flex justify-end gap-2 mt-4">
          <button onClick={onClose} className="px-3 py-1.5 text-sm border border-line-strong text-ink-muted hover:text-ink">取消</button>
          <button onClick={onConfirm} className="px-3 py-1.5 text-sm text-bg font-medium bg-bear">確認</button>
        </div>
      </div>
    </>
  );
}
```

- [ ] **Step 2: `TradingPanel.tsx` 接線**

1. 刪掉檔尾整個 `function OrdersList(...)`(`TradingPanel.tsx:139-156`)
2. import 區加:`import { OrdersList } from "./OrdersList";`
3. 渲染處 `<OrdersList orders={orders} />` 改為 `<OrdersList orders={orders} env={ENV} />`
4. `useCapitalOrders` 的型別來自 `lib/api` 的 `CapitalOrder` re-export,無需改 hook

- [ ] **Step 3: tsc + vitest + build 全綠**

Run: `npx tsc --noEmit && npx vitest run && npm run build`
Expected: 全綠。若 `useSignalsStream` 的 `capital_order` WS payload 型別有引用舊欄位,僅是 reload 觸發、payload 型別不變,不應報錯;有報錯就改該型別引用,不改行為。

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/OrdersList.tsx frontend/src/components/TradingPanel.tsx
git commit -m "feat(capital): 委託清單聚合列+活單刪改 UI"
```

---

### Task 9: 端到端驗證(真實 backlog 重播)

**Files:** 無(驗證 task)

- [ ] **Step 1: 後端全套 + 前端全套最後跑一次**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/ -q`
Run: `cd frontend && npx tsc --noEmit && npx vitest run && npm run build`
Expected: 全綠;記下測試數量(報告用)

- [ ] **Step 2: 重啟後端吃新 code(uvicorn --reload 對多檔變更可能殘留舊 import,直接重啟乾淨)**

```powershell
# 殺 port 8000 進程樹後:
Set-Location C:\side-project\treading-king\backend
& .\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

等 `/api/capital/status` = ok(prod 登入 + ConnectByID 重播當日 backlog)。

- [ ] **Step 3: curl 驗聚合+enrich**

Run: `Invoke-RestMethod http://127.0.0.1:8000/api/capital/orders | ConvertTo-Json -Depth 4`

Expected(對照 2026-06-10 已知事實):
- 3357 列有 `name=臺慶科`、`buy_sell=B`、`flag_label=現股`、`status_label=已刪單`、`order_qty=1`、`unit=張`、`pre_order=true`
- 4989 有成交列:`filled_qty>0`、`avg_fill_price` 非 null
- **無任何 `market=TF/TO` 列**(QEF06/TM2606 被過濾)
- 同標的多張單各自一列(3357 應有多列)

- [ ] **Step 4: 瀏覽器驗渲染(chrome-devtools)**

開 `http://localhost:5173` → Monitor → 委託分頁:
- 列出聚合列(名稱+代號、買賣紅綠、資券、x/y 張、時間)
- 已刪單/全部成交列**無**刪改鈕;若有活單,按「刪單」→ 確認彈窗紅底「⚠ 正式環境(真錢)」→ **按取消**(實刪由 user 決定)
- 截圖存證

- [ ] **Step 5: 更新 memory + 回報**

更新 `project_capital_order_panel.md`:聚合清單+刪改減已實作、刪/改/減**真實鏈路未實跑**(首次操作比照安全首單,對群益 App 驗,特別是 DecreaseOrderBySeqNo 的張/股單位)。

---

## Self-Review 紀錄

- Spec 覆蓋:解碼(T1)、聚合+均價+換算(T2)、過濾+名稱(T3)、安全閘+稽核(T4)、COM+client(T5)、端點(T6)、前端 VM+api(T7)、UI+接線(T8)、驗證(T9)✓;「不做」三項皆未實作 ✓
- 型別一致:`OrderRecord` 欄位(T2)= `CapitalOrder` interface(T7)= route `model_dump` 輸出 ✓;`check_correct_price(new_price, remaining_shares, cfg)` T4 定義 = T5 呼叫 ✓
- 風險:`DecreaseOrderBySeqNo` qty 單位假設=張(同 nQty 實證),首次實測必對 App 驗;U/P/B/S/OrderErr=Y 無真實樣本,以構造 fixture 覆蓋
