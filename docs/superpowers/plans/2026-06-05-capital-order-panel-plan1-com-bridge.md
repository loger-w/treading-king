# 群益下單面板 Plan 1 — COM 橋接 + 測試環境送單 de-risk

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在後端建立一條可在群益**測試環境**安全送出一筆台股委託的最小鏈路(登入→憑證→送單),所有寫入受安全閘保護,純邏輯(安全閘/欄位映射/稽核)全有單元測試。

**Architecture:** 單例 `CapitalClient` 跑一條**專屬 COM 執行緒**(`pythoncom` 訊息幫浦 + 命令佇列),群益 COM 細節藏在 `CapitalCom` 介面後(真實 `comtypes` 實作 + 測試用 fake)。安全閘、群益欄位映射、稽核都是**純函式**,可不依賴 DLL 做 TDD;真實 DLL 互動由 M1 smoke 腳本在測試環境驗證。富邦完全不碰。

**Tech Stack:** Python 3.13 (64-bit, backend venv)、`comtypes`、`pywin32`(`pythoncom`)、`pydantic`、`pytest`、FastAPI lifespan。

**對應 spec:** `docs/superpowers/specs/2026-06-05-capital-order-panel-design.md`(§4 API、§5 後端、§7 安全、§9 v1、§13 環境前置)

---

## M0 — 環境前置(手動,非 TDD;必須先完成才能跑 M1 Task 7)

> 這些是 DLL/帳號層級的前置,無法寫測試。**有外部前置(群益 API 開通)可能需數個工作天**,請先啟動。

- [ ] **群益帳號開通 API 權限**:線上申請 API 憑證 + 簽署 API 使用同意書(群益官網)。確認**測試環境**可用(是否需獨立測試帳號/開放時段 → 登入時實測,對應 spec §12 #4)。
- [ ] **安裝憑證**到本機(群益憑證 e 管家 / 智慧管家),`ReadCertByID` 需要。
- [ ] **註冊 COM 元件**:以系統管理員執行 `C:\Users\USER\Downloads\CapitalAPI_2.13.58\CapitalAPI_2.13.58\元件\x64\install.bat`(`regsvr32` 註冊 `SKCOM.dll` 等)。
- [ ] **安裝 Python 套件**到 backend venv:
  ```powershell
  C:\side-project\treading-king\backend\.venv\Scripts\python.exe -m pip install comtypes pywin32
  ```
- [ ] **驗證 comtypes 能載入元件**(確認註冊成功):
  ```powershell
  C:\side-project\treading-king\backend\.venv\Scripts\python.exe -c "import comtypes.client as c; c.GetModule('SKCOM.dll'); import comtypes.gen.SKCOMLib as sk; print('SKCOM OK', hasattr(sk,'SKCenterLib'))"
  ```
  預期輸出:`SKCOM OK True`。失敗代表 regsvr32 沒成功或位元數不符。
- [ ] **新增 `.env` 設定**(`backend/.env`):
  ```
  CAPITAL_USER_ID=（群益登入帳號/身分證）
  CAPITAL_PASSWORD=（登入密碼）
  CAPITAL_FULL_ACCOUNT=（分公司IB4碼+帳號7碼,共11碼)
  CAPITAL_ENV=test
  CAPITAL_ORDER_ENABLED=false
  CAPITAL_MAX_QTY=5
  CAPITAL_MAX_AMOUNT=2000000
  ```
- [ ] **`.gitignore` 確認** `backend/.env` 已被忽略(機敏資訊不進 git)。

---

## Task 1: 資料模型 + 群益欄位映射(純函式)

**Files:**
- Create: `backend/services/capital_models.py`
- Create: `backend/services/capital_mapping.py`
- Test: `backend/tests/test_capital_mapping.py`

- [ ] **Step 1: 寫失敗測試**(映射意圖:群益 enum 對應正確 —— 改錯會送錯單,必 fail)

```python
# backend/tests/test_capital_mapping.py
from services.capital_models import (
    StockOrderRequest, BuySell, PriceType, TimeInForce, TradeKind,
)
from services.capital_mapping import to_stockorder_fields


def _req(**kw):
    base = dict(stock_no="2330", buy_sell=BuySell.BUY, price=590.0, qty=1)
    base.update(kw)
    return StockOrderRequest(**base)


def test_buy_limit_rod_cash_maps_to_capital_enums():
    f = to_stockorder_fields(_req(), full_account="1234567890A")
    assert f["bstrStockNo"] == "2330"
    assert f["bstrFullAccount"] == "1234567890A"
    assert f["sBuySell"] == 0           # 買=0
    assert f["nSpecialTradeType"] == 2  # 限價=2
    assert f["nTradeType"] == 0         # ROD=0
    assert f["sFlag"] == 0              # 現股=0
    assert f["bstrPrice"] == "590.00"   # 價格字串,兩位小數
    assert f["nQty"] == 1


def test_sell_market_fok_short_maps():
    f = to_stockorder_fields(
        _req(buy_sell=BuySell.SELL, price_type=PriceType.MARKET,
             time_in_force=TimeInForce.FOK, trade_kind=TradeKind.SHORT),
        full_account="1234567890A",
    )
    assert f["sBuySell"] == 1            # 賣=1
    assert f["nSpecialTradeType"] == 1   # 市價=1
    assert f["nTradeType"] == 2          # FOK=2
    assert f["sFlag"] == 2               # 融券=2


def test_margin_maps_to_one():
    f = to_stockorder_fields(_req(trade_kind=TradeKind.MARGIN), full_account="x")
    assert f["sFlag"] == 1               # 融資=1
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && python -m pytest tests/test_capital_mapping.py -v`
Expected: FAIL(`ModuleNotFoundError: services.capital_models`)

- [ ] **Step 3: 寫最小實作**

```python
# backend/services/capital_models.py
from __future__ import annotations
from enum import Enum
from pydantic import BaseModel


class CapitalEnv(str, Enum):
    TEST = "test"
    PROD = "prod"


class BuySell(str, Enum):
    BUY = "buy"
    SELL = "sell"


class PriceType(str, Enum):
    LIMIT = "limit"
    MARKET = "market"


class TimeInForce(str, Enum):
    ROD = "ROD"
    IOC = "IOC"
    FOK = "FOK"


class TradeKind(str, Enum):
    CASH = "cash"      # 現股
    MARGIN = "margin"  # 融資
    SHORT = "short"    # 融券


class StockOrderRequest(BaseModel):
    stock_no: str
    buy_sell: BuySell
    price: float
    qty: int  # 張
    price_type: PriceType = PriceType.LIMIT
    time_in_force: TimeInForce = TimeInForce.ROD
    trade_kind: TradeKind = TradeKind.CASH


class OrderResult(BaseModel):
    ok: bool
    code: int
    message: str
    seq_no: str | None = None
```

```python
# backend/services/capital_mapping.py
"""把 StockOrderRequest 轉成群益 STOCKORDER 欄位 dict。

純函式,不碰 COM,方便測群益 enum 對應(對錯=送錯單)。
enum 值來源:官方 Python 範例 sk.STOCKORDER(spec §4.2)。
"""
from __future__ import annotations
from services.capital_models import (
    StockOrderRequest, BuySell, PriceType, TimeInForce, TradeKind,
)

_BUYSELL = {BuySell.BUY: 0, BuySell.SELL: 1}
_SPECIAL = {PriceType.MARKET: 1, PriceType.LIMIT: 2}
_TIF = {TimeInForce.ROD: 0, TimeInForce.IOC: 1, TimeInForce.FOK: 2}
_FLAG = {TradeKind.CASH: 0, TradeKind.MARGIN: 1, TradeKind.SHORT: 2}


def to_stockorder_fields(req: StockOrderRequest, full_account: str) -> dict:
    return {
        "bstrFullAccount": full_account,
        "bstrStockNo": req.stock_no,
        "sBuySell": _BUYSELL[req.buy_sell],
        "bstrPrice": f"{req.price:.2f}",
        "nQty": req.qty,
        "nSpecialTradeType": _SPECIAL[req.price_type],
        "nTradeType": _TIF[req.time_in_force],
        "sFlag": _FLAG[req.trade_kind],
        "sPeriod": 0,  # 盤中(v1 僅盤中整股)
        "sPrime": 0,   # 上市櫃
    }
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd backend && python -m pytest tests/test_capital_mapping.py -v`
Expected: PASS(3 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/services/capital_models.py backend/services/capital_mapping.py backend/tests/test_capital_mapping.py
git commit -m "feat(capital): order models + 群益 STOCKORDER 欄位映射(純函式+測試)"
```

---

## Task 2: 安全閘(純函式)

**Files:**
- Create: `backend/services/capital_safety.py`
- Test: `backend/tests/test_capital_safety.py`

- [ ] **Step 1: 寫失敗測試**(安全意圖:主開關關/超量/超額必擋 —— 真錢核心)

```python
# backend/tests/test_capital_safety.py
from services.capital_models import StockOrderRequest, BuySell
from services.capital_safety import SafetyConfig, check_stock_order


def _req(qty=1, price=590.0):
    return StockOrderRequest(stock_no="2330", buy_sell=BuySell.BUY, price=price, qty=qty)


def _cfg(enabled=True, max_qty=5, max_amount=2_000_000.0):
    return SafetyConfig(order_enabled=enabled, max_qty=max_qty, max_amount=max_amount)


def test_master_switch_off_blocks():
    r = check_stock_order(_req(), _cfg(enabled=False))
    assert r.allowed is False
    assert "總開關" in r.reason


def test_qty_over_limit_blocks():
    r = check_stock_order(_req(qty=6), _cfg(max_qty=5))
    assert r.allowed is False
    assert "數量" in r.reason


def test_amount_over_limit_blocks():
    # 10 張 * 590 * 1000 = 5,900,000 > 2,000,000
    r = check_stock_order(_req(qty=10), _cfg(max_qty=100, max_amount=2_000_000))
    assert r.allowed is False
    assert "金額" in r.reason


def test_zero_qty_blocks():
    assert check_stock_order(_req(qty=0), _cfg()).allowed is False


def test_valid_order_allowed():
    r = check_stock_order(_req(qty=1), _cfg())
    assert r.allowed is True
    assert r.reason is None
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && python -m pytest tests/test_capital_safety.py -v`
Expected: FAIL(`ModuleNotFoundError: services.capital_safety`)

- [ ] **Step 3: 寫最小實作**

```python
# backend/services/capital_safety.py
"""下單安全閘 —— 純函式,所有寫入(下單/改/刪/平倉)送群益前必過。"""
from __future__ import annotations
from dataclasses import dataclass
from services.capital_models import StockOrderRequest


@dataclass(frozen=True)
class SafetyConfig:
    order_enabled: bool
    max_qty: int
    max_amount: float


@dataclass(frozen=True)
class GateResult:
    allowed: bool
    reason: str | None = None


def check_stock_order(req: StockOrderRequest, cfg: SafetyConfig) -> GateResult:
    if not cfg.order_enabled:
        return GateResult(False, "下單總開關關閉(CAPITAL_ORDER_ENABLED=false)")
    if req.qty <= 0:
        return GateResult(False, "數量必須大於 0")
    if cfg.max_qty and req.qty > cfg.max_qty:
        return GateResult(False, f"數量 {req.qty} 張超過上限 {cfg.max_qty} 張")
    est = req.price * req.qty * 1000
    if cfg.max_amount and est > cfg.max_amount:
        return GateResult(False, f"預估金額 {est:.0f} 超過上限 {cfg.max_amount:.0f}")
    return GateResult(True)
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd backend && python -m pytest tests/test_capital_safety.py -v`
Expected: PASS(5 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/services/capital_safety.py backend/tests/test_capital_safety.py
git commit -m "feat(capital): 下單安全閘(主開關/數量/金額上限)+測試"
```

---

## Task 3: 稽核記錄(jsonl)

**Files:**
- Create: `backend/services/capital_audit.py`
- Test: `backend/tests/test_capital_audit.py`

- [ ] **Step 1: 寫失敗測試**(每筆寫入都要留痕,擋下的也要)

```python
# backend/tests/test_capital_audit.py
import json
from services.capital_models import StockOrderRequest, BuySell, OrderResult
from services import capital_audit


def _req():
    return StockOrderRequest(stock_no="2330", buy_sell=BuySell.BUY, price=590.0, qty=1)


def test_blocked_order_is_audited(tmp_path):
    path = tmp_path / "audit.jsonl"
    capital_audit.write(path, env="test", req=_req(), blocked="總開關關閉")
    line = json.loads(path.read_text(encoding="utf-8").strip())
    assert line["env"] == "test"
    assert line["blocked"] == "總開關關閉"
    assert line["req"]["stock_no"] == "2330"
    assert line["ts"]  # 有時間戳


def test_result_order_is_audited(tmp_path):
    path = tmp_path / "audit.jsonl"
    res = OrderResult(ok=True, code=0, message="委託成功", seq_no="A001")
    capital_audit.write(path, env="test", req=_req(), result=res)
    line = json.loads(path.read_text(encoding="utf-8").strip())
    assert line["result"]["ok"] is True
    assert line["result"]["seq_no"] == "A001"


def test_appends_not_overwrites(tmp_path):
    path = tmp_path / "audit.jsonl"
    capital_audit.write(path, env="test", req=_req(), blocked="a")
    capital_audit.write(path, env="test", req=_req(), blocked="b")
    assert len(path.read_text(encoding="utf-8").strip().splitlines()) == 2
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && python -m pytest tests/test_capital_audit.py -v`
Expected: FAIL(`ModuleNotFoundError: services.capital_audit`)

- [ ] **Step 3: 寫最小實作**

```python
# backend/services/capital_audit.py
"""下單稽核 —— 每筆寫入(含被擋下的)append 到 jsonl。"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from services.capital_models import StockOrderRequest, OrderResult

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "capital_orders.jsonl"


def write(
    path: Path,
    *,
    env: str,
    req: StockOrderRequest,
    blocked: str | None = None,
    result: OrderResult | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "env": env,
        "req": req.model_dump(mode="json"),
        "blocked": blocked,
        "result": result.model_dump(mode="json") if result else None,
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd backend && python -m pytest tests/test_capital_audit.py -v`
Expected: PASS(3 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/services/capital_audit.py backend/tests/test_capital_audit.py
git commit -m "feat(capital): 下單稽核 jsonl(含被擋下的)+測試"
```

---

## Task 4: COM 介面 + 真實 comtypes 實作(無單元測試,M1 smoke 驗)

**Files:**
- Create: `backend/services/capital_com.py`

> 此檔包真實 DLL,無法單元測試(需註冊的 SKCOM.dll)。介面 `CapitalCom` 讓 `CapitalClient` 可注入 fake(Task 5 測)。真實實作由 Task 7 smoke 在測試環境驗。

- [ ] **Step 1: 寫介面 + 真實實作**

```python
# backend/services/capital_com.py
"""群益 COM 封裝。CapitalCom 是介面;SkcomCapitalCom 是真實 comtypes 實作。

真實實作的所有方法都必須在「同一條」CoInitialize 過的執行緒上呼叫
(COM apartment 親和性)—— 由 CapitalClient 的專屬執行緒保證。
"""
from __future__ import annotations
from typing import Protocol


class CapitalCom(Protocol):
    def setup(self) -> None: ...
    def set_authority(self, flag: int) -> int: ...        # 0=正式 2=測試
    def login(self, user_id: str, password: str) -> int: ...
    def init_order(self) -> int: ...
    def read_cert(self, user_id: str) -> int: ...
    def send_stock_order(self, user_id: str, fields: dict) -> tuple[str, int]: ...
    def return_code_message(self, code: int) -> str: ...
    def pump(self) -> None: ...


class SkcomCapitalCom:
    """真實群益 SKCOM 實作(comtypes)。"""

    def __init__(self) -> None:
        self._sk = None
        self._center = None
        self._order = None
        self._reply = None

    def setup(self) -> None:
        import comtypes.client
        comtypes.client.GetModule("SKCOM.dll")
        import comtypes.gen.SKCOMLib as sk
        self._sk = sk
        self._center = comtypes.client.CreateObject(sk.SKCenterLib, interface=sk.ISKCenterLib)
        self._order = comtypes.client.CreateObject(sk.SKOrderLib, interface=sk.ISKOrderLib)
        self._reply = comtypes.client.CreateObject(sk.SKReplyLib, interface=sk.ISKReplyLib)
        # OnReplyMessage 必須回 -1 抑制群益彈窗(spec §4.6)
        comtypes.client.GetEvents(self._reply, _ReplyEvents())

    def set_authority(self, flag: int) -> int:
        return self._center.SKCenterLib_SetAuthority(flag)

    def login(self, user_id: str, password: str) -> int:
        return self._center.SKCenterLib_Login(user_id, password)

    def init_order(self) -> int:
        return self._order.SKOrderLib_Initialize()

    def read_cert(self, user_id: str) -> int:
        return self._order.ReadCertByID(user_id)

    def send_stock_order(self, user_id: str, fields: dict) -> tuple[str, int]:
        order = self._sk.STOCKORDER()
        for k, v in fields.items():
            setattr(order, k, v)
        # bAsync=0 同步,回 (message, nCode)
        message, code = self._order.SendStockOrder(user_id, 0, order)
        return message, code

    def return_code_message(self, code: int) -> str:
        return self._center.SKCenterLib_GetReturnCodeMessage(code)

    def pump(self) -> None:
        import pythoncom
        pythoncom.PumpWaitingMessages()


class _ReplyEvents:
    def OnReplyMessage(self, bstrUserID, bstrMessage):
        return -1  # 群益慣例:回 -1 抑制彈窗
```

- [ ] **Step 2: 語法檢查(import 無誤,不需 DLL)**

Run: `cd backend && python -c "import ast; ast.parse(open('services/capital_com.py',encoding='utf-8').read()); print('syntax ok')"`
Expected: `syntax ok`
> 註:此處不 import 執行(避免在無 DLL/CI 環境觸發 GetModule)。真實載入在 Task 7。

- [ ] **Step 3: Commit**

```bash
git add backend/services/capital_com.py
git commit -m "feat(capital): CapitalCom 介面 + 真實 SKCOM(comtypes)實作"
```

---

## Task 5: CapitalClient(專屬 COM 執行緒 + 送單整合安全閘/稽核)

**Files:**
- Create: `backend/services/capital_client.py`
- Test: `backend/tests/test_capital_client.py`

- [ ] **Step 1: 寫失敗測試**(整合意圖:閘擋下時不得送 COM、要稽核;未就緒要擋)

```python
# backend/tests/test_capital_client.py
import asyncio
from services.capital_models import StockOrderRequest, BuySell
from services.capital_safety import SafetyConfig
from services.capital_client import CapitalClient


class FakeCom:
    def __init__(self):
        self.sent = []
    def setup(self): ...
    def set_authority(self, flag): return 0
    def login(self, u, p): return 0
    def init_order(self): return 0
    def read_cert(self, u): return 0
    def send_stock_order(self, user_id, fields):
        self.sent.append(fields)
        return ("OK", 0)
    def return_code_message(self, code): return "成功" if code == 0 else "錯誤"
    def pump(self): ...


def _client(com, enabled):
    return CapitalClient(
        com, user_id="u", password="p", full_account="1234567890A",
        env="test", safety=SafetyConfig(order_enabled=enabled, max_qty=5, max_amount=2_000_000),
        audit_path=None,
    )


def _req(qty=1):
    return StockOrderRequest(stock_no="2330", buy_sell=BuySell.BUY, price=590.0, qty=qty)


def test_blocked_when_switch_off_does_not_touch_com():
    com = FakeCom()
    client = _client(com, enabled=False)
    res = asyncio.run(client.submit_stock_order(_req()))
    assert res.ok is False
    assert "總開關" in res.message
    assert com.sent == []          # 絕不可送到 COM


def test_blocked_when_not_ready():
    com = FakeCom()
    client = _client(com, enabled=True)   # 未 start(),status != ok
    res = asyncio.run(client.submit_stock_order(_req()))
    assert res.ok is False
    assert "未就緒" in res.message
    assert com.sent == []
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && python -m pytest tests/test_capital_client.py -v`
Expected: FAIL(`ModuleNotFoundError: services.capital_client`)

- [ ] **Step 3: 寫最小實作**

```python
# backend/services/capital_client.py
"""群益下單單例 —— 一條專屬 COM 執行緒(訊息幫浦 + 命令佇列),橋回 asyncio。

所有群益呼叫都在 _run() 那條執行緒上(COM apartment 親和性)。
送單前一律過安全閘 + 稽核。富邦完全不碰。
"""
from __future__ import annotations
import asyncio
import logging
import queue
import threading
from pathlib import Path

from services.capital_com import CapitalCom
from services.capital_mapping import to_stockorder_fields
from services.capital_models import StockOrderRequest, OrderResult
from services.capital_safety import SafetyConfig, check_stock_order
from services import capital_audit

logger = logging.getLogger(__name__)


class CapitalClient:
    def __init__(
        self,
        com: CapitalCom,
        *,
        user_id: str,
        password: str,
        full_account: str,
        env: str,
        safety: SafetyConfig,
        audit_path: Path | None,
    ) -> None:
        self._com = com
        self._user_id = user_id
        self._password = password
        self._full_account = full_account
        self._env = env
        self._safety = safety
        self._audit_path = audit_path or capital_audit.DEFAULT_PATH
        self._cmd_q: "queue.Queue" = queue.Queue()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._status = "error"      # error | ok | degraded
        self._last_error: str | None = None

    @property
    def status(self) -> str:
        return self._status

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._thread = threading.Thread(target=self._run, daemon=True, name="capital-com")
        self._thread.start()

    def _run(self) -> None:
        import pythoncom
        pythoncom.CoInitialize()
        try:
            self._com.setup()
            self._com.set_authority(2 if self._env == "test" else 0)
            code = self._com.login(self._user_id, self._password)
            if code != 0:
                raise RuntimeError("Login: " + self._com.return_code_message(code))
            self._com.init_order()
            code = self._com.read_cert(self._user_id)
            if code != 0:
                raise RuntimeError("ReadCertByID: " + self._com.return_code_message(code))
            self._status = "ok"
            logger.info("Capital login + cert OK (env=%s)", self._env)
        except Exception as e:  # noqa: BLE001
            self._status = "error"
            self._last_error = f"{type(e).__name__}: {e}"
            logger.error("Capital init failed: %s", self._last_error)
            return

        while True:
            self._com.pump()
            try:
                cmd = self._cmd_q.get(timeout=0.05)
            except queue.Empty:
                continue
            if cmd is None:
                break
            fn, fut = cmd
            try:
                result = fn()
                self._loop.call_soon_threadsafe(fut.set_result, result)
            except Exception as e:  # noqa: BLE001
                self._loop.call_soon_threadsafe(fut.set_exception, e)

    async def submit_stock_order(self, req: StockOrderRequest) -> OrderResult:
        gate = check_stock_order(req, self._safety)
        if not gate.allowed:
            capital_audit.write(self._audit_path, env=self._env, req=req, blocked=gate.reason)
            return OrderResult(ok=False, code=-1, message=gate.reason)
        if self._status != "ok" or self._loop is None:
            return OrderResult(ok=False, code=-1, message="群益未就緒(尚未登入或憑證失敗)")

        fut: asyncio.Future = self._loop.create_future()

        def _do() -> tuple[str, int]:
            fields = to_stockorder_fields(req, self._full_account)
            return self._com.send_stock_order(self._user_id, fields)

        self._cmd_q.put((_do, fut))
        message, code = await fut
        result = OrderResult(
            ok=(code == 0),
            code=code,
            message=f"{self._com.return_code_message(code)} {message}".strip(),
        )
        capital_audit.write(self._audit_path, env=self._env, req=req, result=result)
        return result
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd backend && python -m pytest tests/test_capital_client.py -v`
Expected: PASS(2 passed)
> 說明:兩個測試都走「閘擋下 / 未就緒」的早退路徑,不啟動執行緒,故無需真實 COM。真實送單由 Task 7 smoke 驗。

- [ ] **Step 5: Commit**

```bash
git add backend/services/capital_client.py backend/tests/test_capital_client.py
git commit -m "feat(capital): CapitalClient 專屬 COM 執行緒 + 送單(過閘+稽核)+測試"
```

---

## Task 6: 單例存取 + app 生命週期接線

**Files:**
- Create: `backend/services/capital_factory.py`
- Modify: `backend/main.py`(lifespan startup,插在行 84 之後、`yield`(行 87)之前)

- [ ] **Step 1: 寫單例工廠**(從 env 組裝;不在 import 時連線)

```python
# backend/services/capital_factory.py
"""從環境變數組裝 CapitalClient 單例。"""
from __future__ import annotations
import os
from services.capital_client import CapitalClient
from services.capital_com import SkcomCapitalCom
from services.capital_safety import SafetyConfig

_client: CapitalClient | None = None


def get_capital() -> CapitalClient | None:
    """未設定 CAPITAL_USER_ID 時回 None(功能未啟用)。"""
    global _client
    if _client is not None:
        return _client
    user_id = os.getenv("CAPITAL_USER_ID", "").strip()
    if not user_id:
        return None
    _client = CapitalClient(
        SkcomCapitalCom(),
        user_id=user_id,
        password=os.getenv("CAPITAL_PASSWORD", "").strip(),
        full_account=os.getenv("CAPITAL_FULL_ACCOUNT", "").strip(),
        env=os.getenv("CAPITAL_ENV", "test").strip(),
        safety=SafetyConfig(
            order_enabled=os.getenv("CAPITAL_ORDER_ENABLED", "false").strip().lower() == "true",
            max_qty=int(os.getenv("CAPITAL_MAX_QTY", "0") or 0),
            max_amount=float(os.getenv("CAPITAL_MAX_AMOUNT", "0") or 0),
        ),
        audit_path=None,
    )
    return _client
```

- [ ] **Step 2: 接 lifespan**(在富邦/MXF 啟動之後、`yield` 之前;包 try/except 不得影響富邦)

`backend/main.py` 已具備:`import asyncio`(行 4)、`load_dotenv(...)`(行 14,故 `.env` 的 `CAPITAL_*` 會被載入)、lifespan `@asynccontextmanager`(行 40-99)、`logger`(行 37)、`import os`(行 6)。在 `top_gainers_task = asyncio.create_task(top_gainers_loop())`(行 83-84)之後、`logger.info("Startup done…`(行 85)之前,插入:

```python
    # --- 群益下單(可選,未設定或失敗都不影響富邦) ---
    try:
        from services.capital_factory import get_capital
        capital = get_capital()
        if capital is not None:
            capital.start(asyncio.get_running_loop())
            logger.info("Capital client started (env=%s)", os.getenv("CAPITAL_ENV", "test"))
    except Exception as e:  # noqa: BLE001
        logger.error("Capital startup skipped: %s", e)
```

> 不接 shutdown:CapitalClient 的 COM 執行緒是 daemon,隨行程結束;優雅關閉(`stop()`)留待 Plan 2。

- [ ] **Step 3: 驗證 app 仍能啟動(無 CAPITAL_USER_ID 時 get_capital 回 None,不連線)**

Run: `cd backend && python -c "import main; print('app import ok')"`
Expected: `app import ok`(無群益設定時不會嘗試連線)

- [ ] **Step 4: Commit**

```bash
git add backend/services/capital_factory.py backend/main.py
git commit -m "feat(capital): 單例工廠 + lifespan 接線(可選,失敗不影響富邦)"
```

---

## Task 7: M1 de-risk — 測試環境登入/憑證/送單 smoke(手動)

**Files:**
- Create: `backend/scripts/capital_smoke.py`

> 這是整個 Plan 的**除風險核心**:第一次證明 comtypes + 群益測試環境 + 憑證 + 送單真的會動。需 M0 完成 + 盤中/測試環境開放時段。

- [ ] **Step 1: 寫 smoke 腳本**

```python
# backend/scripts/capital_smoke.py
"""群益測試環境 smoke:登入 → 憑證 → (可選)送一筆測試單。

用法(在 backend/,venv):
  python scripts/capital_smoke.py                # 只登入+憑證+狀態
  python scripts/capital_smoke.py --send-test    # 額外送一筆測試單(需 CAPITAL_ORDER_ENABLED=true)

讀 backend/.env(CAPITAL_*)。CAPITAL_ENV 必須 = test。
"""
from __future__ import annotations
import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from services.capital_factory import get_capital  # noqa: E402
from services.capital_models import StockOrderRequest, BuySell, PriceType  # noqa: E402


async def main(send_test: bool) -> int:
    if os.getenv("CAPITAL_ENV", "test").strip() != "test":
        print("CAPITAL_ENV 不是 test,為安全中止。")
        return 2
    client = get_capital()
    if client is None:
        print("未設定 CAPITAL_USER_ID,無法測試。")
        return 2

    client.start(asyncio.get_running_loop())
    # 等登入序列(輪詢 status,最多 ~20 秒)
    for _ in range(200):
        if client.status != "error":
            break
        await asyncio.sleep(0.1)
    print(f"狀態: {client.status}  最後錯誤: {client.last_error}")
    if client.status != "ok":
        return 1

    if send_test:
        req = StockOrderRequest(
            stock_no="2330", buy_sell=BuySell.BUY, price=500.0, qty=1,
            price_type=PriceType.LIMIT,
        )
        res = await client.submit_stock_order(req)
        print(f"送單結果: ok={res.ok} code={res.code} msg={res.message}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--send-test", action="store_true")
    raise SystemExit(asyncio.run(main(ap.parse_args().send_test)))
```

- [ ] **Step 2: 確認 `python-dotenv` 已裝**(富邦那邊可能已用;沒有就裝)

Run: `cd backend && python -c "import dotenv; print('dotenv ok')"`
若失敗:`C:\side-project\treading-king\backend\.venv\Scripts\python.exe -m pip install python-dotenv`

- [ ] **Step 3: 跑登入 smoke(只登入,不送單)**

Run: `cd backend && python scripts/capital_smoke.py`
Expected: `狀態: ok  最後錯誤: None`
> 若 `error`:看 `最後錯誤`。常見:測試環境未開放時段、API 未開通、憑證未裝、regsvr32 沒成功、密碼錯。逐一對 M0 排除(對應 spec §12 #4)。

- [ ] **Step 4: 跑送單 smoke(測試環境送一筆,需暫時開總開關)**

先把 `backend/.env` 的 `CAPITAL_ORDER_ENABLED=true`(僅測試環境、僅此驗證),然後:
Run: `cd backend && python scripts/capital_smoke.py --send-test`
Expected: `送單結果: ok=True code=0 msg=...`(或測試環境回的委託成功訊息)
**驗證後立刻把 `CAPITAL_ORDER_ENABLED` 改回 `false`。**

- [ ] **Step 5: 確認稽核有寫入**

Run: `cd backend && python -c "print(open('data/capital_orders.jsonl',encoding='utf-8').read())"`
Expected: 至少一行 JSON,含 `env":"test"` 與 `result`。

- [ ] **Step 6: Commit**

```bash
git add backend/scripts/capital_smoke.py
git commit -m "feat(capital): 測試環境 smoke 腳本(登入/憑證/送單 de-risk)"
```

---

## 完成準則(Plan 1)
- 純邏輯(映射/安全閘/稽核/client 早退)單元測試全綠:`cd backend && python -m pytest tests/test_capital_*.py -v`
- M1 smoke 在**測試環境**:登入 `ok` + 送一筆測試單 `code=0` + 稽核有記錄。
- 富邦行情/訊號不受影響(app 正常啟動)。
- 群益功能未設定(無 `CAPITAL_USER_ID`)時,後端照常運作。

## 下一步(後續 Plan,本 Plan 不含)
- **Plan 2 — v1 後端餘下**:`OnNewData` 回報 → 委託快取 + WS 推;`GetRealBalanceReport`/`GetProfitLossGWReport` → 部位快取;`routes/capital.py`(status/order/orders/positions)。
- **Plan 3 — v1 前端**:移除明細(`TradeTape`)+ `TradingPanel`(下單 tab + 委託 tab + 部位卡)+ 五檔連動 + 健康燈。
- v2 / v3 各自再開 plan。
