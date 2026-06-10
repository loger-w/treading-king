import asyncio
from services.capital_models import StockOrderRequest, BuySell
from services.capital_safety import SafetyConfig
from services.capital_client import CapitalClient


class FakeCom:
    def __init__(self):
        self.sent = []

    def setup(self, on_reply=None): ...
    def set_authority(self, flag): return 0
    def login(self, u, p): return 0
    def init_order(self): return 0
    def read_cert(self, u): return 0

    def send_stock_order(self, user_id, fields):
        self.sent.append(fields)
        return ("OK", 0)

    def return_code_message(self, code): return "成功" if code == 0 else "錯誤"
    def pump(self): ...


class RecordingCom(FakeCom):
    """記錄啟動時呼叫到的 COM 方法順序,驗證啟動序列。"""

    def __init__(self):
        super().__init__()
        self.calls = []

    def setup(self, on_reply=None): self.calls.append("setup")
    def set_authority(self, flag): self.calls.append("set_authority"); return 0
    def login(self, u, p): self.calls.append("login"); return 0
    def init_order(self): self.calls.append("init_order"); return 0
    def read_cert(self, u): self.calls.append("read_cert"); return 0
    def connect_reply(self, user_id): self.calls.append("connect_reply"); return 0


def _client(com, enabled, audit_path):
    return CapitalClient(
        com, user_id="u", password="p", full_account="1234567890A",
        env="test", safety=SafetyConfig(order_enabled=enabled, max_qty=5, max_amount=2_000_000),
        audit_path=audit_path,
    )


def _req(qty=1):
    return StockOrderRequest(stock_no="2330", buy_sell=BuySell.BUY, price=590.0, qty=qty)


def test_blocked_when_switch_off_does_not_touch_com(tmp_path):
    com = FakeCom()
    client = _client(com, enabled=False, audit_path=tmp_path / "audit.jsonl")
    res = asyncio.run(client.submit_stock_order(_req()))
    assert res.ok is False
    assert "總開關" in res.message
    assert com.sent == []          # 絕不可送到 COM


def test_blocked_when_not_ready(tmp_path):
    com = FakeCom()
    client = _client(com, enabled=True, audit_path=tmp_path / "audit.jsonl")  # 未 start(),status != ok
    res = asyncio.run(client.submit_stock_order(_req()))
    assert res.ok is False
    assert "未就緒" in res.message
    assert com.sent == []


def test_startup_connects_reply_channel(tmp_path):
    # 沒呼叫 SKReplyLib_ConnectByID(connect_reply)就連不上回報主機,
    # OnNewData 永遠不會推 → 委託/成交/刪單回報全收不到、面板「委託」永遠空。
    com = RecordingCom()
    client = _client(com, enabled=True, audit_path=tmp_path / "audit.jsonl")
    ok = client._init_com()
    assert ok is True
    assert client.status == "ok"
    assert "connect_reply" in com.calls
    # 必須在登入+憑證之後才連回報(需要有效 session)
    assert com.calls.index("connect_reply") > com.calls.index("read_cert")
