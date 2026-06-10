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

    def cancel_order(self, user_id, full_account, seq_no):
        self.sent.append(("cancel", seq_no))
        return ("OK", 0)

    def correct_price(self, user_id, full_account, seq_no, price):
        self.sent.append(("correct_price", seq_no, price))
        return ("OK", 0)

    def decrease_qty(self, user_id, full_account, seq_no, qty):
        self.sent.append(("decrease", seq_no, qty))
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


from services.capital_models import CancelOrderRequest, CorrectPriceRequest, DecreaseQtyRequest
from services.capital_reply import parse_onnewdata


def _ready_client(com, audit_path, max_amount=2_000_000.0):
    """status 標 ok、不跑 COM 執行緒;閘與佇列投遞用 _run_write 同步驗。"""
    client = CapitalClient(
        com, user_id="u", password="p", full_account="1234567890A",
        env="test", safety=SafetyConfig(order_enabled=True, max_qty=5, max_amount=max_amount),
        audit_path=audit_path,
    )
    client._status = "ok"
    return client


def _run_write(client, make_coro):
    """測試替代 COM 執行緒:綁 running loop、投遞後同步 drain 佇列。
    make_coro = lambda: client.cancel_stock_order(...)(lambda 延遲建立,避免 loop 未綁)。"""
    async def _go():
        client._loop = asyncio.get_running_loop()
        task = asyncio.ensure_future(make_coro())
        await asyncio.sleep(0)      # 讓 coro 先把命令投進佇列
        while not client._cmd_q.empty():
            fn, fut = client._cmd_q.get_nowait()
            fut.set_result(fn())
        return await task
    return asyncio.run(_go())


def _stock_evt(seq, qty="1000", price="90.0000"):
    arr = [""] * 47
    arr[0], arr[1], arr[2], arr[3] = seq, "TS", "N", "N"
    arr[6], arr[8], arr[11], arr[20] = "B00R2", "3357", price, qty
    return parse_onnewdata(",".join(arr))


def test_cancel_blocked_when_switch_off(tmp_path):
    com = FakeCom()
    client = _client(com, enabled=False, audit_path=tmp_path / "a.jsonl")
    res = asyncio.run(client.cancel_stock_order(CancelOrderRequest(seq_no="S1")))
    assert res.ok is False and "總開關" in res.message
    assert com.sent == []


def test_cancel_goes_through_com(tmp_path):
    com = FakeCom()
    client = _ready_client(com, tmp_path / "a.jsonl")
    res = _run_write(client, lambda: client.cancel_stock_order(CancelOrderRequest(seq_no="S1")))
    assert res.ok is True
    assert ("cancel", "S1") in com.sent


def test_correct_price_uses_remaining_shares_for_amount_gate(tmp_path):
    com = FakeCom()
    client = _ready_client(com, tmp_path / "a.jsonl", max_amount=100_000.0)
    client.store.apply_reply(_stock_evt("S9"))   # 未成交 1000 股
    # 新價 200 × 1000 股 = 200,000 > 100,000 → 擋
    res = asyncio.run(client.correct_stock_price(CorrectPriceRequest(seq_no="S9", price=200.0)))
    assert res.ok is False and "超過上限" in res.message
    assert com.sent == []


def test_correct_price_passes_gate_and_reaches_com(tmp_path):
    com = FakeCom()
    client = _ready_client(com, tmp_path / "a.jsonl", max_amount=100_000.0)
    client.store.apply_reply(_stock_evt("S9"))
    res = _run_write(client, lambda: client.correct_stock_price(CorrectPriceRequest(seq_no="S9", price=95.0)))
    assert res.ok is True
    assert ("correct_price", "S9", 95.0) in com.sent


def test_correct_price_unknown_seq_rejected(tmp_path):
    com = FakeCom()
    client = _ready_client(com, tmp_path / "a.jsonl")
    res = asyncio.run(client.correct_stock_price(CorrectPriceRequest(seq_no="nope", price=10.0)))
    assert res.ok is False and "找不到" in res.message
    assert com.sent == []


def test_decrease_goes_through_com_and_audits_action(tmp_path):
    import json
    com = FakeCom()
    audit = tmp_path / "a.jsonl"
    client = _ready_client(com, audit)
    res = _run_write(client, lambda: client.decrease_stock_qty(DecreaseQtyRequest(seq_no="S1", qty=1)))
    assert res.ok is True
    assert ("decrease", "S1", 1) in com.sent
    entry = json.loads(audit.read_text(encoding="utf-8").splitlines()[-1])
    assert entry["action"] == "decrease"
