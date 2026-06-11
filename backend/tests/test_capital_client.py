import asyncio
from services.capital_models import StockOrderRequest, BuySell
from services.capital_safety import SafetyConfig
from services.capital_client import CapitalClient


class FakeCom:
    def __init__(self):
        self.sent = []

    def setup(self, on_reply=None, on_balance=None): ...
    def set_authority(self, flag): return 0
    def login(self, u, p): return 0
    def init_order(self): return 0
    def read_cert(self, u): return 0

    def get_real_balance(self, user_id, full_account):
        self.sent.append(("get_real_balance", full_account))
        return 0

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

    def setup(self, on_reply=None, on_balance=None): self.calls.append("setup")
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


def test_handle_balance_lines_then_end_marker_updates_store(tmp_path):
    com = FakeCom()
    client = _client(com, enabled=True, audit_path=tmp_path / "a.jsonl")
    # 真實格式(去敏):[0]股號 [1]種類 [14]即時庫存(股)
    client._handle_balance("3357,C,2000,1944,0,0,3000,0,0,0,0,3000,0,0,3000,0,155.63,A123456789,1234567890")
    client._handle_balance("##")
    pos = client.store.positions()
    assert len(pos) == 1
    assert pos[0].stock_no == "3357"
    assert pos[0].qty == 3
    assert pos[0].kind == "margin"


def test_fill_reply_marks_balance_dirty(tmp_path):
    """成交回報(D)後要排程一次庫存重查(debounce 由 _run 圈消化)。"""
    com = FakeCom()
    client = _client(com, enabled=True, audit_path=tmp_path / "a.jsonl")
    client._mark_balance_dirty()
    assert client._balance_due is not None


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
    make_coro = lambda: client.cancel_stock_order(...)(lambda 延遲建立,避免 loop 未綁)。
    drain 的 try/except 與 production _run 同構:fn() 例外 → set_exception。"""
    async def _go():
        client._loop = asyncio.get_running_loop()
        task = asyncio.ensure_future(make_coro())
        await asyncio.sleep(0)      # 讓 coro 先把命令投進佇列
        while not client._cmd_q.empty():
            fn, fut = client._cmd_q.get_nowait()
            try:
                fut.set_result(fn())
            except Exception as e:
                fut.set_exception(e)
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


def _last_audit(path):
    import json
    return json.loads(path.read_text(encoding="utf-8").splitlines()[-1])


def test_not_ready_write_is_audited(tmp_path):
    com = FakeCom()
    audit = tmp_path / "a.jsonl"
    client = _client(com, enabled=True, audit_path=audit)   # 未 start,status != ok
    res = asyncio.run(client.cancel_stock_order(CancelOrderRequest(seq_no="S1")))
    assert res.ok is False and "未就緒" in res.message
    entry = _last_audit(audit)
    assert entry["action"] == "cancel" and "未就緒" in entry["blocked"]


def test_correct_price_unknown_seq_is_audited(tmp_path):
    com = FakeCom()
    audit = tmp_path / "a.jsonl"
    client = _ready_client(com, audit)
    res = asyncio.run(client.correct_stock_price(CorrectPriceRequest(seq_no="nope", price=10.0)))
    assert res.ok is False
    entry = _last_audit(audit)
    assert entry["action"] == "correct_price" and "找不到委託" in entry["blocked"]


def test_com_exception_returns_result_and_audited(tmp_path):
    class BoomCom(FakeCom):
        def cancel_order(self, user_id, full_account, seq_no):
            raise RuntimeError("COM died")

    audit = tmp_path / "a.jsonl"
    client = _ready_client(BoomCom(), audit)
    res = _run_write(client, lambda: client.cancel_stock_order(CancelOrderRequest(seq_no="S1")))
    assert res.ok is False and "COM 例外" in res.message
    entry = _last_audit(audit)
    assert entry["action"] == "cancel"
    assert entry["result"]["ok"] is False and "COM 例外" in entry["result"]["message"]


def test_correct_price_switch_off_reports_master_reason(tmp_path):
    # 總開關要先於 store 查找:關閉時拒絕理由/稽核 blocked 必須是「總開關」,
    # 不可被「找不到委託」遮蔽(事後查帳要看得出當時開關已關)
    com = FakeCom()
    audit = tmp_path / "a.jsonl"
    client = _client(com, enabled=False, audit_path=audit)
    res = asyncio.run(client.correct_stock_price(CorrectPriceRequest(seq_no="nope", price=10.0)))
    assert res.ok is False and "總開關" in res.message
    entry = _last_audit(audit)
    assert entry["action"] == "correct_price" and "總開關" in entry["blocked"]
    assert com.sent == []


def _tf_evt(seq="2315596711743"):
    arr = [""] * 47
    arr[0], arr[1], arr[2], arr[3] = seq, "TF", "N", "N"
    arr[6], arr[8], arr[11], arr[20] = "BNR20", "QEF06", "873.0000", "1"
    return parse_onnewdata(",".join(arr))


def test_writes_reject_known_futures_seq(tmp_path):
    # v1 寫入鏈只支援證券:期貨口數會讓改價金額閘(價×量)低估數十倍名目曝險,
    # 證券帳號打期貨序號行為也未定 — 已知非證券的單三支寫入都要擋下並留稽核
    com = FakeCom()
    audit = tmp_path / "a.jsonl"
    client = _ready_client(com, audit)
    client.store.apply_reply(_tf_evt("F1"))
    for call in (
        lambda: client.cancel_stock_order(CancelOrderRequest(seq_no="F1")),
        lambda: client.correct_stock_price(CorrectPriceRequest(seq_no="F1", price=900.0)),
        lambda: client.decrease_stock_qty(DecreaseQtyRequest(seq_no="F1", qty=1)),
    ):
        res = asyncio.run(call())
        assert res.ok is False and "非證券" in res.message
        assert "非證券" in _last_audit(audit)["blocked"]
    assert com.sent == []


def test_correct_price_rejected_for_cancelled_order(tmp_path):
    # 已刪單的 order-filled 差額不是未成交量:改價要被「無未成交」擋下,不留給券商兜底
    com = FakeCom()
    client = _ready_client(com, tmp_path / "a.jsonl")
    client.store.apply_reply(_stock_evt("S9"))
    cancel_evt = _stock_evt("S9").model_copy(update={"status_raw": "C"})
    client.store.apply_reply(cancel_evt)
    res = asyncio.run(client.correct_stock_price(CorrectPriceRequest(seq_no="S9", price=95.0)))
    assert res.ok is False and "無未成交" in res.message
    assert com.sent == []


def test_audit_failure_after_send_does_not_fail_order(tmp_path, monkeypatch):
    # 命令已出手後稽核寫不進去(磁碟滿等)只能記 log:
    # 把已送進群益的單回 500 會誘發 user 重送 → 真錢重複下單
    from services import capital_audit

    com = FakeCom()
    client = _ready_client(com, tmp_path / "a.jsonl")

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(capital_audit, "write", boom)
    res = _run_write(client, lambda: client.cancel_stock_order(CancelOrderRequest(seq_no="S1")))
    assert res.ok is True                      # 單已送出,結果照實回報
    assert ("cancel", "S1") in com.sent
