# 群益下單面板 Plan 2 — 後端:回報 + 部位 + routes

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development 或 superpowers:executing-plans 逐 task 實作。Steps 用 checkbox(`- [ ]`)。

**Goal:** 在 Plan 1 的 `CapitalClient` 上補齊 v1 後端:解析群益主動回報(`OnNewData`)→ 委託快取 + WS 推播;查詢即時庫存 → 部位快取;對外開 `routes/capital.py`(status / 送單 / 委託 / 部位)。

**Architecture:** 群益事件(`OnNewData`、`OnRealBalanceReport`)在 COM 執行緒上回呼 → 解析(純函式)→ 更新記憶體快取(加鎖)→ 委託變動用 `call_soon_threadsafe` 橋回 asyncio 並透過既有 `get_broadcaster().broadcast({"event":"capital_order",...})` 推前端。REST 讀快取。富邦不碰。

**Tech Stack:** Python 3.13、pydantic、pytest、FastAPI(`APIRouter` + `TestClient`)、既有 `ws_broadcaster`。

**前置:** Plan 1 已合併/在同分支(`services/capital_client.py` 等已存在)。

**既有慣例(Plan 2 直接沿用,來自 code 實查):**
- 廣播:`from services...`→ `get_broadcaster().broadcast({"event": "...", "data": {...}})`(`ws_broadcaster.py`,async)。同步執行緒橋接:`loop.call_soon_threadsafe(asyncio.create_task, get_broadcaster().broadcast(payload))`(見 `fubon_ws.py:224-238`)。
- WS 信封 `{"event","data"}`,前端走既有 `/ws/realtime`(不需新端點)。
- Route:`router = APIRouter()`(無 prefix);完整路徑寫在 decorator;`HTTPException(status, detail={"error":...})`;`app.include_router(capital.router)`。
- 認證:`/api/*` 需 `X-API-Key`(既有 middleware 自動處理,前端 `fetchJSON` 自動帶)。

---

## Task 1: 回報解析(`OnNewData` 純函式)

**Files:**
- Create: `backend/services/capital_reply.py`
- Test: `backend/tests/test_capital_reply.py`

> 群益 `OnNewData(UserID, bstrData)` 的 `bstrData` 是逗號分隔字串(spec §4.5)。已知索引:`[0]委託序號 [2]委託種類 [3]委託狀態 [8]商品代碼 [10]委託書號 [11]價格 [20]數量 [23][24]日期時間 [-4][-3]錯誤`。委託狀態完整 enum 值是開放項(spec §12 #2),故解析保留 `status_raw`,並用一張可調整對照表給人類標籤(M1 對文件後修表)。

- [ ] **Step 1: 寫失敗測試**(解析意圖:索引抽取正確、狀態保留原值、欄位型別正確)

```python
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
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && python -m pytest tests/test_capital_reply.py -v`
Expected: FAIL(`ModuleNotFoundError: services.capital_reply`)

- [ ] **Step 3: 寫最小實作**

```python
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
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd backend && python -m pytest tests/test_capital_reply.py -v`
Expected: PASS(4 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/services/capital_reply.py backend/tests/test_capital_reply.py
git commit -m "feat(capital): OnNewData 回報解析(純函式)+測試"
```

---

## Task 2: 委託/部位資料模型

**Files:**
- Modify: `backend/services/capital_models.py`(在檔尾追加)
- Test: `backend/tests/test_capital_position.py`

- [ ] **Step 1: 寫失敗測試**(未實現損益毛額計算意圖:張×1000×(現價−均價))

```python
# backend/tests/test_capital_position.py
from services.capital_models import Position


def test_unrealized_gross_pnl():
    p = Position(stock_no="2330", name="台積電", qty=5, avg_price=575.0)
    assert p.unrealized_gross(current_price=590.0) == 75000.0   # 5*1000*(590-575)


def test_short_position_negative_qty():
    p = Position(stock_no="2317", name="鴻海", qty=-2, avg_price=100.0)
    # 放空:跌才賺。現價 95 → (95-100)*-2*1000 = +10000
    assert p.unrealized_gross(current_price=95.0) == 10000.0


def test_zero_when_no_price():
    p = Position(stock_no="2330", name="台積電", qty=5, avg_price=575.0)
    assert p.unrealized_gross(current_price=None) == 0.0
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && python -m pytest tests/test_capital_position.py -v`
Expected: FAIL(`ImportError: cannot import name 'Position'`)

- [ ] **Step 3: 在 `capital_models.py` 檔尾追加**

```python
class OrderRecord(BaseModel):
    seq_no: str
    stock_no: str | None = None
    book_no: str | None = None
    status_raw: str | None = None
    status_label: str | None = None
    price: float | None = None
    qty: int = 0
    raw: str = ""


class Position(BaseModel):
    stock_no: str
    name: str = ""
    qty: int           # 張(放空為負)
    avg_price: float

    def unrealized_gross(self, current_price: float | None) -> float:
        if current_price is None:
            return 0.0
        return self.qty * 1000 * (current_price - self.avg_price)
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd backend && python -m pytest tests/test_capital_position.py -v`
Expected: PASS(3 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/services/capital_models.py backend/tests/test_capital_position.py
git commit -m "feat(capital): OrderRecord / Position 模型 + 未實現損益毛額"
```

---

## Task 3: 快取 + 廣播(可測,不含 COM)

**Files:**
- Create: `backend/services/capital_store.py`
- Test: `backend/tests/test_capital_store.py`

> 把「回報 → 委託快取」「庫存查詢結果 → 部位快取」抽成獨立、可測的 store(執行緒安全)。COM 事件回呼只負責呼叫 store + 觸發廣播。

- [ ] **Step 1: 寫失敗測試**

```python
# backend/tests/test_capital_store.py
from services.capital_reply import parse_onnewdata
from services.capital_models import Position
from services.capital_store import CapitalStore


def _mk(fields):
    arr = [""] * 25
    for i, v in fields.items():
        arr[i] = str(v)
    return ",".join(arr)


def test_reply_upserts_order_by_seqno():
    s = CapitalStore()
    s.apply_reply(parse_onnewdata(_mk({0: "A1", 8: "2330", 3: "0", 20: "3"})))
    s.apply_reply(parse_onnewdata(_mk({0: "A1", 8: "2330", 3: "2", 20: "3"})))  # 同序號更新狀態
    orders = s.orders()
    assert len(orders) == 1
    assert orders[0].seq_no == "A1"
    assert orders[0].status_raw == "2"   # 後到的覆蓋


def test_orders_sorted_newest_first():
    s = CapitalStore()
    s.apply_reply(parse_onnewdata(_mk({0: "A1"})))
    s.apply_reply(parse_onnewdata(_mk({0: "A2"})))
    assert [o.seq_no for o in s.orders()] == ["A2", "A1"]


def test_set_positions_replaces():
    s = CapitalStore()
    s.set_positions([Position(stock_no="2330", name="台積電", qty=5, avg_price=575.0)])
    assert len(s.positions()) == 1
    assert s.position_for("2330").qty == 5
    assert s.position_for("9999") is None
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && python -m pytest tests/test_capital_store.py -v`
Expected: FAIL(`ModuleNotFoundError: services.capital_store`)

- [ ] **Step 3: 寫最小實作**

```python
# backend/services/capital_store.py
"""群益委託/部位記憶體快取(執行緒安全)。COM 事件回呼更新它;REST 讀它。"""
from __future__ import annotations
import threading
from services.capital_models import OrderRecord, Position
from services.capital_reply import ReplyRecord


class CapitalStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._orders: dict[str, OrderRecord] = {}
        self._order_seq: list[str] = []           # 到達順序
        self._positions: dict[str, Position] = {}

    def apply_reply(self, rec: ReplyRecord) -> None:
        if not rec.seq_no:
            return
        with self._lock:
            if rec.seq_no not in self._orders:
                self._order_seq.append(rec.seq_no)
            self._orders[rec.seq_no] = OrderRecord(
                seq_no=rec.seq_no, stock_no=rec.stock_no, book_no=rec.book_no,
                status_raw=rec.status_raw, status_label=rec.status_label,
                price=rec.price, qty=rec.qty, raw=rec.raw,
            )

    def orders(self) -> list[OrderRecord]:
        with self._lock:
            return [self._orders[s] for s in reversed(self._order_seq)]

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

- [ ] **Step 4: 跑測試確認通過**

Run: `cd backend && python -m pytest tests/test_capital_store.py -v`
Expected: PASS(3 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/services/capital_store.py backend/tests/test_capital_store.py
git commit -m "feat(capital): 委託/部位記憶體快取 store(執行緒安全)+測試"
```

---

## Task 4: CapitalClient 接事件 + 廣播 + 查詢(擴充)

**Files:**
- Modify: `backend/services/capital_com.py`(`CapitalCom` 介面 + 真實實作加事件/查詢)
- Modify: `backend/services/capital_client.py`(接 store、回呼、廣播、查詢)
- Test: `backend/tests/test_capital_client_reply.py`

- [ ] **Step 1: 寫失敗測試**(整合意圖:回報進來 → store 更新 + 觸發廣播 callback)

```python
# backend/tests/test_capital_client_reply.py
import asyncio
from services.capital_safety import SafetyConfig
from services.capital_client import CapitalClient


class FakeCom:
    def __init__(self): self.on_reply = None
    def setup(self, on_reply=None): self.on_reply = on_reply
    def set_authority(self, f): return 0
    def login(self, u, p): return 0
    def init_order(self): return 0
    def read_cert(self, u): return 0
    def return_code_message(self, c): return "ok"
    def pump(self): ...


def _client(com):
    return CapitalClient(
        com, user_id="u", password="p", full_account="A", env="test",
        safety=SafetyConfig(order_enabled=True, max_qty=5, max_amount=9e9),
        audit_path=None,
    )


def test_reply_updates_store_and_calls_broadcast():
    com = FakeCom()
    client = _client(com)
    pushed = []
    client.set_broadcast(lambda payload: pushed.append(payload))
    # 模擬 COM 執行緒收到 OnNewData(不啟動真執行緒,直接呼叫 handler)
    client._handle_reply("A1,,1,0,,,,,2330,,B1,590.00,,,,,,,,,3,,,,")
    orders = client.store.orders()
    assert len(orders) == 1 and orders[0].seq_no == "A1"
    assert pushed and pushed[0]["event"] == "capital_order"
    assert pushed[0]["data"]["seq_no"] == "A1"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && python -m pytest tests/test_capital_client_reply.py -v`
Expected: FAIL(`AttributeError: 'CapitalClient' object has no attribute 'set_broadcast'`)

- [ ] **Step 3a: `capital_com.py` — `setup` 收 `on_reply`;真實實作把 `OnNewData` 接到它**

把 `CapitalCom` 介面的 `setup` 改成:
```python
    def setup(self, on_reply) -> None: ...   # on_reply: Callable[[str], None]
```
把 `SkcomCapitalCom.setup` 改成(其餘方法不變):
```python
    def setup(self, on_reply) -> None:
        import comtypes.client
        comtypes.client.GetModule("SKCOM.dll")
        import comtypes.gen.SKCOMLib as sk
        self._sk = sk
        self._center = comtypes.client.CreateObject(sk.SKCenterLib, interface=sk.ISKCenterLib)
        self._order = comtypes.client.CreateObject(sk.SKOrderLib, interface=sk.ISKOrderLib)
        self._reply = comtypes.client.CreateObject(sk.SKReplyLib, interface=sk.ISKReplyLib)
        comtypes.client.GetEvents(self._reply, _ReplyEvents(on_reply))
```
把 `_ReplyEvents` 改成:
```python
class _ReplyEvents:
    def __init__(self, on_reply):
        self._on_reply = on_reply

    def OnReplyMessage(self, bstrUserID, bstrMessage):
        return -1  # 群益慣例:回 -1 抑制彈窗

    def OnNewData(self, bstrUserID, bstrData):
        try:
            self._on_reply(bstrData)
        except Exception:
            pass
```
並在檔頭 import:`from typing import Protocol, Callable`(`Callable` 給介面註解用)。

- [ ] **Step 3b: `capital_client.py` — 接 store / broadcast / handler**

在 `CapitalClient.__init__` 末尾加:
```python
        from services.capital_store import CapitalStore
        self.store = CapitalStore()
        self._broadcast = None  # Callable[[dict], None] | None
```
加方法:
```python
    def set_broadcast(self, fn) -> None:
        """fn(payload: dict) —— 由 app 注入,通常包成 call_soon_threadsafe + broadcaster。"""
        self._broadcast = fn

    def _handle_reply(self, bstr_data: str) -> None:
        from services.capital_reply import parse_onnewdata
        rec = parse_onnewdata(bstr_data)
        self.store.apply_reply(rec)
        if self._broadcast and rec.seq_no:
            self._broadcast({"event": "capital_order", "data": {
                "seq_no": rec.seq_no, "stock_no": rec.stock_no,
                "status_label": rec.status_label, "price": rec.price, "qty": rec.qty,
            }})
```
把 `_run()` 裡的 `self._com.setup()` 改成 `self._com.setup(self._handle_reply)`。

- [ ] **Step 4: 跑測試確認通過**

Run: `cd backend && python -m pytest tests/test_capital_client_reply.py tests/test_capital_client.py -v`
Expected: PASS(舊 2 + 新 1 = 3 passed)
> 註:`test_capital_client.py` 的 `FakeCom.setup(self)` 需同步改成 `setup(self, on_reply=None)`(收一個參數)。一併修。

- [ ] **Step 5: capital_com 語法檢查**

Run: `cd backend && python -c "import ast; ast.parse(open('services/capital_com.py',encoding='utf-8').read()); print('ok')"`
Expected: `ok`

- [ ] **Step 6: Commit**

```bash
git add backend/services/capital_com.py backend/services/capital_client.py backend/tests/test_capital_client_reply.py backend/tests/test_capital_client.py
git commit -m "feat(capital): client 接 OnNewData → store + 廣播 capital_order"
```

---

## Task 5: routes/capital.py + 接線 + 廣播注入

**Files:**
- Create: `backend/routes/capital.py`
- Modify: `backend/main.py`(include_router + 注入 broadcast)
- Test: `backend/tests/test_capital_route.py`

- [ ] **Step 1: 寫失敗測試**(route 行為:未啟用回 503;送單過 client)

```python
# backend/tests/test_capital_route.py
from fastapi.testclient import TestClient
import main
import services.capital_factory as factory
from services.capital_models import OrderResult, Position, OrderRecord


class FakeClient:
    status = "ok"
    last_error = None
    class _Store:
        def orders(self): return [OrderRecord(seq_no="A1", stock_no="2330", status_label="委託成功")]
        def positions(self): return [Position(stock_no="2330", name="台積電", qty=5, avg_price=575.0)]
    store = _Store()
    async def submit_stock_order(self, req): return OrderResult(ok=True, code=0, message="委託成功", seq_no="A1")


def _client(monkeypatch, fake):
    monkeypatch.setattr(factory, "get_capital", lambda: fake)
    return TestClient(main.app)


def test_status_unavailable_when_none(monkeypatch):
    c = _client(monkeypatch, None)
    r = c.get("/api/capital/status")
    assert r.status_code == 200
    assert r.json()["status"] == "disabled"


def test_orders_and_positions(monkeypatch):
    c = _client(monkeypatch, FakeClient())
    assert c.get("/api/capital/orders").json()["orders"][0]["seq_no"] == "A1"
    assert c.get("/api/capital/positions").json()["positions"][0]["stock_no"] == "2330"


def test_submit_order_ok(monkeypatch):
    c = _client(monkeypatch, FakeClient())
    r = c.post("/api/capital/order/stock", json={
        "stock_no": "2330", "buy_sell": "buy", "price": 590.0, "qty": 1,
    })
    assert r.status_code == 200
    assert r.json()["ok"] is True and r.json()["seq_no"] == "A1"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && python -m pytest tests/test_capital_route.py -v`
Expected: FAIL(404 — route 尚未存在)

- [ ] **Step 3: 寫 route**

```python
# backend/routes/capital.py
"""群益下單面板 API。讀快取 / 送單。富邦無關。"""
from __future__ import annotations
from fastapi import APIRouter, HTTPException
from services.capital_factory import get_capital
from services.capital_models import StockOrderRequest

router = APIRouter()


@router.get("/api/capital/status")
async def capital_status() -> dict:
    c = get_capital()
    if c is None:
        return {"status": "disabled"}
    return {"status": c.status, "last_error": c.last_error}


@router.get("/api/capital/orders")
async def capital_orders() -> dict:
    c = get_capital()
    if c is None:
        return {"orders": []}
    return {"orders": [o.model_dump(mode="json") for o in c.store.orders()]}


@router.get("/api/capital/positions")
async def capital_positions() -> dict:
    c = get_capital()
    if c is None:
        return {"positions": []}
    return {"positions": [p.model_dump(mode="json") for p in c.store.positions()]}


@router.post("/api/capital/order/stock")
async def capital_order_stock(req: StockOrderRequest) -> dict:
    c = get_capital()
    if c is None:
        raise HTTPException(503, detail={"error": "capital_disabled"})
    res = await c.submit_stock_order(req)
    return res.model_dump(mode="json")
```

- [ ] **Step 4: 接線 + 廣播注入(`main.py`)**

在 `app.include_router(ws.router)` 後加一行:
```python
app.include_router(capital.router)
```
並把 import 區的 routes 匯入補上 `capital`(routes import tuple 內加 `capital`)。
在 lifespan 的群益啟動區塊(Plan 1 加的那段),`capital.start(...)` 後面加注入廣播:
```python
            from services.ws_broadcaster import get_broadcaster
            loop = asyncio.get_running_loop()
            def _push(payload):
                loop.call_soon_threadsafe(asyncio.create_task, get_broadcaster().broadcast(payload))
            capital.set_broadcast(_push)
```

- [ ] **Step 5: 跑測試確認通過**

Run: `cd backend && python -m pytest tests/test_capital_route.py -v`
Expected: PASS(3 passed)

- [ ] **Step 6: 全套件回歸**

Run: `cd backend && python -m pytest -q`
Expected: PASS(全綠,無回歸)

- [ ] **Step 7: Commit**

```bash
git add backend/routes/capital.py backend/main.py backend/tests/test_capital_route.py
git commit -m "feat(capital): routes(status/orders/positions/送單)+ 接線 + 廣播注入"
```

---

## Task 6: 庫存查詢(COM 真實實作,M1+ 驗)

**Files:**
- Modify: `backend/services/capital_com.py`(加 `get_real_balance` + `OnRealBalanceReport` → 解析 → store.set_positions)
- Modify: `backend/services/capital_client.py`(定時/觸發查庫存)

> 庫存查詢是非同步事件(`GetRealBalanceReport` → `OnRealBalanceReport`),需真實 DLL,無單元測試;由 M1+ smoke 驗。**僅做語法檢查 + 設計落實**,實際欄位解析格式於 M1 對範例 `OnRealBalanceReport` 回傳字串釘死(spec §12)。

- [ ] **Step 1: `capital_com.py` 加查詢 + 事件**(real impl)

`SkcomCapitalCom.setup` 的 `GetEvents(self._reply, ...)` 之外,對 `self._order` 也註冊事件:
```python
        comtypes.client.GetEvents(self._order, _OrderEvents(on_balance))
```
`setup` 簽章改為 `def setup(self, on_reply, on_balance) -> None:`,介面同步改。加方法:
```python
    def get_real_balance(self, user_id: str, full_account: str) -> int:
        return self._order.GetRealBalanceReport(user_id, full_account)
```
加事件類別:
```python
class _OrderEvents:
    def __init__(self, on_balance):
        self._on_balance = on_balance

    def OnRealBalanceReport(self, bstrData):
        try:
            self._on_balance(bstrData)
        except Exception:
            pass
```
> `OnRealBalanceReport(bstrData)` 的逗號欄位(股票代號/張數/均價)精確 index 於 M1 對範例 `StockOrder.py` 的 `OnRealBalanceReport` 處理釘死。先寫一個 `parse_balance(bstrData) -> list[Position]` 的 stub(回 `[]`),M1 補欄位。

- [ ] **Step 2: `capital_client.py` 接 balance**

`__init__` 加 `self._positions_dirty = False`;`setup` 呼叫改 `self._com.setup(self._handle_reply, self._handle_balance)`;加:
```python
    def _handle_balance(self, bstr_data: str) -> None:
        from services.capital_balance import parse_balance
        self.store.set_positions(parse_balance(bstr_data))

    async def refresh_positions(self) -> None:
        if self._status != "ok" or self._loop is None:
            return
        fut = self._loop.create_future()
        def _do():
            return self._com.get_real_balance(self._user_id, self._full_account)
        self._cmd_q.put((_do, fut))
        await fut
```
建 `backend/services/capital_balance.py`:
```python
"""解析 OnRealBalanceReport 庫存字串 → Position list。

M1 對範例釘死欄位 index 前先回空清單(不擋啟動)。
"""
from __future__ import annotations
from services.capital_models import Position


def parse_balance(bstr_data: str) -> list[Position]:
    # TODO(M1): 對範例 StockOrder.py 的 OnRealBalanceReport 欄位 index 解析
    return []
```
> 此 `TODO(M1)` 是「開放項」非「placeholder」:函式可運作(回 `[]`)、不擋 v1 啟動;M1 拿到真實字串後補欄位 index。

- [ ] **Step 3: 語法檢查 + 全套件回歸**

Run: `cd backend && python -c "import ast; [ast.parse(open(f,encoding='utf-8').read()) for f in ['services/capital_com.py','services/capital_client.py','services/capital_balance.py']]; print('ok')"`
Run: `cd backend && python -m pytest -q`
Expected: `ok` + 全綠

- [ ] **Step 4: Commit**

```bash
git add backend/services/capital_com.py backend/services/capital_client.py backend/services/capital_balance.py
git commit -m "feat(capital): 庫存查詢 GetRealBalanceReport 接線(欄位解析待 M1)"
```

---

## 完成準則(Plan 2)
- `python -m pytest -q` 全綠(新增 capital_reply / position / store / client_reply / route 測試)。
- `import main` OK;`/api/capital/status` 在無群益時回 `disabled`。
- 富邦不受影響。
- 開放項:`OnNewData` 狀態 enum、`OnRealBalanceReport` 欄位 index → M1 拿真實字串釘死。

## 下一步
- **Plan 3 — v1 前端**:移除明細 + `TradingPanel`(下單/委託 tab)+ 五檔連動 + 部位卡 + 健康燈,消費本 Plan 的 routes 與 `capital_order` WS 事件。
