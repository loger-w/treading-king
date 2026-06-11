# 庫存均價+損益(GetProfitLossGWReport)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 依 `docs/superpowers/specs/2026-06-11-capital-avg-price-pnl-design.md`,庫存查詢 flush 後串行查未實現損益試算,把 `[10]`平均買進成本回填 `Position.avg_price` — 前端零改動,損益顯示鏈自動亮起。

**Architecture:** 全後端。複用 BalanceCollector(泛化 parse 參數)收 OnProfitLossGWReport 事件;`_on_balance_complete` 末端(已在 COM 執行緒)直接發損益查詢,天然串行避開 1019;flush 後 `store.apply_avg_prices` 回填。

**Tech Stack:** FastAPI + comtypes(SKCOM)+ pytest。分支 `feat/capital-order-panel-v2`(接著 PR #23 的 commit)。

**測試指令:** `cd backend && .\.venv\Scripts\python.exe -m pytest -q`(⚠ 必須 venv python,系統 python 缺 pydantic)

---

### Task 1: parse_profit_line + BalanceCollector 泛化

**Files:**
- Modify: `backend/services/capital_balance.py`
- Test: `backend/tests/test_capital_balance.py`

- [ ] **Step 1: 寫失敗測試** — `test_capital_balance.py` import 行加 `parse_profit_line`,檔尾追加:

```python
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
```

- [ ] **Step 2: 確認失敗** — Run: `.\.venv\Scripts\python.exe -m pytest tests/test_capital_balance.py -q`,Expected: ImportError `parse_profit_line`。

- [ ] **Step 3: 實作** — `capital_balance.py`:

(a) `dedupe_positions` 後追加:

```python
_PNL_IDX_STOCK_NO = 1
_PNL_IDX_AVG = 10        # 平均買進(券賣)成本(4-2-p 未實現-彙總)
_PNL_MIN_FIELDS = 11


def parse_profit_line(raw: str) -> tuple[str, float] | None:
    """OnProfitLossGWReport(未實現-彙總)一筆 → (股號, 均價);
    查詢結果列(000開頭)/結束標記/欄位不足/數字壞/均價≤0 → None(缺均價不出垃圾)。"""
    if not raw or raw.startswith("#"):
        return None
    parts = raw.split(",")
    if len(parts) < _PNL_MIN_FIELDS or parts[0].strip() == "000":
        return None
    stock_no = parts[_PNL_IDX_STOCK_NO].strip()
    try:
        avg = float(parts[_PNL_IDX_AVG])
    except ValueError:
        logger.warning("profit line 解析失敗: %r", raw)
        return None
    if not stock_no or avg <= 0:
        return None
    return (stock_no, avg)
```

(b) `BalanceCollector` 泛化(docstring 補一句「parse 可換,profit 報告共用」):

```python
    def __init__(self, on_complete: Callable[[list], None], timeout_s: float = 1.0,
                 parse: Callable[[str], object | None] = parse_balance_line) -> None:
        self._on_complete = on_complete
        self._timeout_s = timeout_s
        self._parse = parse
        self._staging: list = []
        self._last_feed: float | None = None
```

`feed` 裡 `parse_balance_line(raw)` 改 `self._parse(raw)`。
⚠ class 定義在 `parse_balance_line` 之後、`parse_profit_line` 要放在 class 前(預設參數求值順序)。

- [ ] **Step 4: 確認通過** — Run: `.\.venv\Scripts\python.exe -m pytest tests/test_capital_balance.py -q`,Expected: 全 PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/services/capital_balance.py backend/tests/test_capital_balance.py
git commit -m "feat(capital): 損益試算列解析+收集器泛化(parse可換)"
```

---

### Task 2: store 均價回填 + set_positions 沿用語意

**Files:**
- Modify: `backend/services/capital_store.py:187-189`(set_positions)
- Test: `backend/tests/test_capital_store.py`

- [ ] **Step 1: 寫失敗測試** — `test_capital_store.py` import 行確認有 `Position`,檔尾追加:

```python
def test_apply_avg_prices_fills_existing_only():
    s = CapitalStore()
    s.set_positions([Position(stock_no="3357", qty=3, kind="margin")])
    s.apply_avg_prices({"3357": 150.55, "9999": 1.0})   # 查無股號忽略(部位以即時庫存為權威)
    assert s.position_for("3357").avg_price == 150.55
    assert len(s.positions()) == 1


def test_set_positions_carries_avg_same_kind_only():
    """損益查詢回來前,新一輪庫存覆寫不可閃掉已知均價;但種類變了成本基礎不同,不沿用。"""
    s = CapitalStore()
    s.set_positions([Position(stock_no="3357", qty=3, kind="margin")])
    s.apply_avg_prices({"3357": 150.55})
    s.set_positions([Position(stock_no="3357", qty=4, kind="margin")])
    assert s.position_for("3357").avg_price == 150.55
    s.set_positions([Position(stock_no="3357", qty=4, kind="cash")])
    assert s.position_for("3357").avg_price is None
```

- [ ] **Step 2: 確認失敗** — Run: `.\.venv\Scripts\python.exe -m pytest tests/test_capital_store.py -q`,Expected: `apply_avg_prices` AttributeError。

- [ ] **Step 3: 實作** — `capital_store.py` 的 `set_positions` 換成:

```python
    def set_positions(self, positions: list[Position]) -> None:
        with self._lock:
            old = self._positions
            for p in positions:
                prev = old.get(p.stock_no)
                # 損益查詢回來前沿用既有均價(同種類才沿用 — 資/券成本基礎不同)
                if p.avg_price is None and prev is not None and prev.kind == p.kind:
                    p.avg_price = prev.avg_price
            self._positions = {p.stock_no: p for p in positions}

    def apply_avg_prices(self, avg: dict[str, float]) -> None:
        """損益試算回填均價;查無股號忽略(部位清單以即時庫存為權威)。"""
        with self._lock:
            for stock_no, price in avg.items():
                p = self._positions.get(stock_no)
                if p is not None:
                    p.avg_price = price
```

- [ ] **Step 4: 確認通過** — Run: `.\.venv\Scripts\python.exe -m pytest tests/test_capital_store.py -q`,Expected: 全 PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/services/capital_store.py backend/tests/test_capital_store.py
git commit -m "feat(capital): store均價回填+庫存覆寫沿用同種類均價"
```

---

### Task 3: COM 層 — 查詢方法 + 事件 sink

**Files:**
- Modify: `backend/services/capital_com.py`
- Test: `backend/tests/test_capital_com.py`

- [ ] **Step 1: 寫失敗測試** — `test_capital_com.py` 檔尾追加:

```python
def test_order_events_sink_forwards_profit_and_swallows_exception():
    from services.capital_com import _OrderEvents

    got = []
    sink = _OrderEvents(on_profit=got.append)
    sink.OnProfitLossGWReport("000,查詢成功")
    assert got == ["000,查詢成功"]

    def boom(_):
        raise RuntimeError("boom")
    _OrderEvents(on_profit=boom).OnProfitLossGWReport("x")   # 例外不可炸 COM 事件迴圈
    _OrderEvents().OnProfitLossGWReport("x")                  # 無回呼 noop
```

- [ ] **Step 2: 確認失敗** — Run: `.\.venv\Scripts\python.exe -m pytest tests/test_capital_com.py -q`,Expected: TypeError(`on_profit` 參數不存在)。

- [ ] **Step 3: 實作** — `capital_com.py`:

(a) Protocol `setup` 簽名與 `get_real_balance` 後:

```python
    def setup(self, on_reply: "Callable[[str], None] | None" = None,
              on_balance: "Callable[[str], None] | None" = None,
              on_profit: "Callable[[str], None] | None" = None) -> None: ...
```
```python
    def get_profit_loss_gw(self, user_id: str, full_account: str) -> int: ...  # 結果走 OnProfitLossGWReport
```

(b) `SkcomCapitalCom.setup` 簽名同步加 `on_profit=None`,`_OrderEvents(on_balance)` 改 `_OrderEvents(on_balance, on_profit)`。

(c) `get_real_balance` 後加:

```python
    def get_profit_loss_gw(self, user_id: str, full_account: str) -> int:
        # 未實現損益試算(彙總、全部商品);結果走 OnProfitLossGWReport 事件。
        # 字串欄一律帶空字串 — comtypes 未設的 BSTR 是 None,群益端行為未定義
        q = self._sk.TSPROFITLOSSGWQUERY()
        q.bstrFullAccount = full_account
        q.nTPQueryType = 0     # 0=未實現
        q.nFunc = 0            # 0=彙總
        q.bstrStockNo = ""
        q.bstrTradeType = ""
        q.bstrStartDate = ""
        q.bstrEndDate = ""
        q.bstrBookNo = ""
        q.bstrSeqNo = ""
        return self._order.GetProfitLossGWReport(user_id, q)
```

(d) `_OrderEvents` 換成:

```python
class _OrderEvents:
    """SKOrderLib 事件 sink(即時庫存+損益試算);回呼例外不可炸掉 COM 事件迴圈。"""

    def __init__(self, on_balance: "Callable[[str], None] | None" = None,
                 on_profit: "Callable[[str], None] | None" = None) -> None:
        self._on_balance = on_balance
        self._on_profit = on_profit

    def OnRealBalanceReport(self, bstrData):
        if self._on_balance:
            try:
                self._on_balance(bstrData)
            except Exception:
                pass

    def OnProfitLossGWReport(self, bstrData):
        if self._on_profit:
            try:
                self._on_profit(bstrData)
            except Exception:
                pass
```

- [ ] **Step 4: 確認通過** — Run: `.\.venv\Scripts\python.exe -m pytest tests/test_capital_com.py -q`,Expected: 全 PASS(含既有 setup 防 GC 測試)。

- [ ] **Step 5: Commit**

```bash
git add backend/services/capital_com.py backend/tests/test_capital_com.py
git commit -m "feat(capital): COM層損益試算查詢+OnProfitLossGWReport sink"
```

---

### Task 4: client 串接 — 庫存 flush 後串行查均價

**Files:**
- Modify: `backend/services/capital_client.py`
- Test: `backend/tests/test_capital_client.py`

- [ ] **Step 1: 擴充 FakeCom + 寫失敗測試** — `test_capital_client.py`:

(a) `FakeCom.setup` 改 `def setup(self, on_reply=None, on_balance=None, on_profit=None): ...`;
`RecordingCom.setup` 同步改簽名(維持 append "setup")。`FakeCom` 加:

```python
    def get_profit_loss_gw(self, user_id, full_account):
        self.sent.append(("get_profit_loss_gw", full_account))
        return 0
```

(b) 檔尾追加測試:

```python
def test_balance_flush_chains_profit_query_then_avg_applied(tmp_path):
    """庫存 flush → 串行發損益查詢(避開 1019);損益 flush → 均價回填部位。"""
    com = FakeCom()
    client = _client(com, enabled=True, audit_path=tmp_path / "a.jsonl")
    client._handle_balance("3357,C,2000,1944,0,0,3000,0,0,0,0,3000,0,0,3000,0,155.63,A123456789,1234567890")
    client._handle_balance("##")
    assert ("get_profit_loss_gw", "1234567890A") in com.sent
    client._handle_profit("000,查詢成功")
    client._handle_profit("臺慶科,3357,新台幣,融資,3000,156.00,0.27,468000,464000,12345,150.55,451650,0,0,665,0,1404,135495,316155,89,,2.73,0,,Y")
    client._handle_profit("##,,,,")
    assert client.store.position_for("3357").avg_price == 150.55
```

- [ ] **Step 2: 確認失敗** — Run: `.\.venv\Scripts\python.exe -m pytest tests/test_capital_client.py -q`,Expected: `_handle_profit` AttributeError(既有 balance 測試會先因 `get_profit_loss_gw` 鏈炸 — 屬同一缺口)。

- [ ] **Step 3: 實作** — `capital_client.py`:

(a) import 改:

```python
from services.capital_balance import BalanceCollector, dedupe_positions, parse_profit_line
```

(b) `__init__` 的 `self._balance = ...` 下一行加:

```python
        self._profit = BalanceCollector(on_complete=self._on_profit_complete, parse=parse_profit_line)
```

(c) `_handle_balance` 後加:

```python
    def _handle_profit(self, raw: str) -> None:
        """OnProfitLossGWReport 事件(COM 執行緒)。"""
        self._profit.feed(raw)

    def _on_profit_complete(self, rows) -> None:
        """rows = [(stock_no, avg)] — 回填均價後再推部位事件讓前端 refetch。"""
        self.store.apply_avg_prices(dict(rows))
        if self._broadcast:
            self._broadcast({"event": "capital_position", "data": {"avg_count": len(rows)}})
```

(d) `_on_balance_complete` 換成:

```python
    def _on_balance_complete(self, positions) -> None:
        self.store.set_positions(dedupe_positions(positions))
        self._balance_last_ts = time.monotonic()
        if self._broadcast:
            self._broadcast({"event": "capital_position", "data": {"count": len(positions)}})
        # 庫存查詢剛完結(同 COM 執行緒)→ 串行接著查均價,避開 1019 查詢處理中
        self._profit.reset()
        rc = self._com.get_profit_loss_gw(self._user_id, self._full_account)
        if rc != 0:
            logger.warning("GetProfitLossGWReport rc=%s: %s", rc, self._com.return_code_message(rc))
```

(e) `_init_com` 的 setup 呼叫改:

```python
            self._com.setup(self._handle_reply, self._handle_balance, self._handle_profit)
```

(f) `_run` 幫浦圈 `self._balance.poll()` 下一行加:

```python
            self._profit.poll()            # 損益沒等到 ## 的 flush 保險
```

- [ ] **Step 4: 全後端測試** — Run: `.\.venv\Scripts\python.exe -m pytest -q`,Expected: 全 PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/services/capital_client.py backend/tests/test_capital_client.py
git commit -m "feat(capital): 庫存flush後串行查損益試算,均價回填部位"
```

---

### Task 5: smoke tap + 全套驗證 + 清理

**Files:**
- Modify: `backend/scripts/capital_smoke.py`

- [ ] **Step 1: smoke 加 profit tap** — `capital_smoke.py` 的 balance tap 區塊(`client._handle_balance = tap` 之後)追加,並把 `await asyncio.sleep(8)` 改 `await asyncio.sleep(12)`(兩段串行查詢):

```python
        orig_profit = client._handle_profit
        def tap_profit(raw: str) -> None:
            print(f"PNL| {raw!r}")
            orig_profit(raw)
        client._handle_profit = tap_profit
```

持倉列印改成含種類與均價:

```python
        for p in positions:
            avg = f"{p.avg_price:.2f}" if p.avg_price is not None else "—"
            print(f"持倉: {p.stock_no} {p.qty} 張 ({p.kind}) 均 {avg}")
```

- [ ] **Step 2: 全套驗證** —

```bash
cd backend && .\.venv\Scripts\python.exe -m pytest -q     # Expected: 全 PASS
cd ../frontend && npx vitest run                           # Expected: 全 PASS(前端零改動驗證)
```

- [ ] **Step 3: 清理暫存** — 刪 `backend/scripts/_dump_docx.py` 與 `.superpowers/plgw_dump.txt`(docx 抽取暫存,不入庫)。

- [ ] **Step 4: Commit**

```bash
git add backend/scripts/capital_smoke.py
git commit -m "feat(capital): smoke --balance 加印損益試算原始字串(校準用)"
```

- [ ] **Step 5: 實機校準(user 配合)** — 跑 `cd backend; $env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe scripts\capital_smoke.py --balance`,核對:`PNL|` 原始字串的 `[10]` 是否每股均價、持倉均價是否與群益 App 一致;假設錯就以真實樣本修 `_PNL_IDX_*` 與測試。

---

## Self-Review 紀錄

- **Spec 覆蓋**:官方介面→Task 3;collector 泛化+parse→Task 1;store 回填/沿用→Task 2;串行鏈+pump→Task 4;smoke 校準→Task 5;前端零改動驗證→Task 5 Step 2 ✓
- **佔位符**:無;每步含完整碼 ✓
- **型別一致**:`parse_profit_line -> tuple[str, float] | None`(Task 1 定義、Task 4 dict(rows) 使用);`get_profit_loss_gw(user_id, full_account) -> int`(Task 3 定義、Task 4 呼叫、FakeCom 同簽名);`apply_avg_prices(dict[str, float])`(Task 2 定義、Task 4 使用)✓
