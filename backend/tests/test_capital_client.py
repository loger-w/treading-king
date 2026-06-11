import asyncio
from services.capital_models import StockOrderRequest, BuySell
from services.capital_safety import SafetyConfig
from services.capital_client import CapitalClient


class FakeCom:
    def __init__(self):
        self.sent = []

    def setup(self, on_reply=None, on_balance=None, on_profit=None,
              on_reply_disconnect=None): ...
    def set_authority(self, flag): return 0
    def login(self, u, p): return 0
    def init_order(self): return 0
    def read_cert(self, u): return 0

    def get_real_balance(self, user_id, full_account):
        self.sent.append(("get_real_balance", full_account))
        return 0

    def get_profit_loss_gw(self, user_id, full_account):
        self.sent.append(("get_profit_loss_gw", full_account))
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

    def setup(self, on_reply=None, on_balance=None, on_profit=None,
              on_reply_disconnect=None): self.calls.append("setup")
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


def test_pump_once_swallows_exceptions(tmp_path, monkeypatch):
    """幫浦圈單輪例外(COM 斷線等)不可外洩 — 執行緒死=寫入 future 永不 resolve
    而 status 還是 ok 的靜默失敗。"""
    import services.capital_client as mod
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)   # 防 log 洪水的節流不拖慢測試
    com = FakeCom()
    client = _client(com, enabled=True, audit_path=tmp_path / "a.jsonl")

    def boom():
        raise RuntimeError("COM 斷線")
    com.pump = boom
    client._pump_once()   # 不得 raise


def test_run_thread_exit_drops_status(tmp_path):
    """_run 結束(任何原因)必須把 status 降為 error:
    否則之後的寫入請求進佇列後永遠沒人消化,UI 卻顯示健康。"""
    com = RecordingCom()
    client = _client(com, enabled=True, audit_path=tmp_path / "a.jsonl")
    client._loop = asyncio.new_event_loop()
    client._cmd_q.put(None)    # 模擬終止訊號:init 成功後第一輪就 break
    client._run()
    assert client.status == "error"
    assert client.last_error


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


def _stock_evt(seq, qty="1000", price="90.0000", bs="B00R2"):
    arr = [""] * 47
    arr[0], arr[1], arr[2], arr[3] = seq, "TS", "N", "N"
    arr[6], arr[8], arr[11], arr[20] = bs, "3357", price, qty
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


from services.capital_models import Position, PositionCloseRequest


def test_close_position_second_close_blocked_inflight(tmp_path):
    # 部位快取要等成交回報→debounce→重查回來才更新:第一筆平倉在途的數秒窗口內,
    # 第二筆同股號請求仍看得到原始全量持倉、照樣過量閘 → 兩張全量反向單都會送進
    # 群益(融券回補是 BUY,券商不會以庫存擋)。第二次必須被擋且留稽核。
    com = FakeCom()
    audit = tmp_path / "a.jsonl"
    client = _ready_client(com, audit)
    client.store.set_positions([Position(stock_no="2330", qty=1, kind="cash")])
    res1 = _run_write(client, lambda: client.close_position(
        PositionCloseRequest(stock_no="2330", price=590.0)))
    assert res1.ok is True
    assert len(com.sent) == 1
    res2 = asyncio.run(client.close_position(PositionCloseRequest(stock_no="2330", price=590.0)))
    assert res2.ok is False and "在途" in res2.message
    assert len(com.sent) == 1                  # 第二張絕不可送到 COM
    entry = _last_audit(audit)
    assert entry["action"] == "close" and "在途" in entry["blocked"]


def test_close_blocked_when_same_side_active_order_in_store(tmp_path):
    # in-flight 窗口過後由這層接手:store 已有同檔同向活躍委託(回報已到、尚未成交)
    # 就拒平 — 再送一張等於重複平倉
    com = FakeCom()
    client = _ready_client(com, tmp_path / "a.jsonl")
    client.store.set_positions([Position(stock_no="3357", qty=1, kind="cash")])
    client.store.apply_reply(_stock_evt("S1", bs="S00R2"))   # 活躍賣單(現股多平倉同向)
    res = asyncio.run(client.close_position(PositionCloseRequest(stock_no="3357", price=90.0)))
    assert res.ok is False and "活躍委託" in res.message
    assert com.sent == []


def test_write_timeout_returns_unknown_result(tmp_path, monkeypatch):
    # SendStockOrder 是同步呼叫:群益端掛起時 future 永不 resolve → 請求永久懸掛。
    # 逾時要回「結果未知」(不可回失敗誘發重送)並照樣稽核留帳。
    import services.capital_client as mod
    monkeypatch.setattr(mod, "_WRITE_TIMEOUT_S", 0.01)
    com = FakeCom()
    audit = tmp_path / "a.jsonl"
    client = _ready_client(com, audit)

    async def _go():
        client._loop = asyncio.get_running_loop()
        return await client.cancel_stock_order(CancelOrderRequest(seq_no="S1"))

    res = asyncio.run(_go())                   # 沒人消化佇列 = COM 卡死
    assert res.ok is False and "結果未知" in res.message
    entry = _last_audit(audit)
    assert entry["action"] == "cancel" and "結果未知" in entry["result"]["message"]


def test_run_thread_exit_fails_queued_futures(tmp_path):
    # 執行緒亡故時佇列殘留的命令沒人消化:future 必須被 set_exception,
    # 否則 status 檢查通過後才入佇列的寫入請求會永久懸掛
    com = RecordingCom()
    client = _client(com, enabled=True, audit_path=tmp_path / "a.jsonl")
    loop = asyncio.new_event_loop()
    try:
        client._loop = loop
        fut = loop.create_future()
        client._cmd_q.put(None)                       # 終止訊號
        client._cmd_q.put((lambda: ("OK", 0), fut))   # 終止後殘留的命令
        client._run()
        loop.run_until_complete(asyncio.sleep(0))     # 消化 call_soon_threadsafe
        assert fut.done()
        assert isinstance(fut.exception(), RuntimeError)
    finally:
        loop.close()


def test_reply_disconnect_degrades_status_not_writes(tmp_path):
    # 回報斷線 → status 降 degraded + last_error(前端才不會把過期委託當健康資料);
    # 但送單通道獨立可用,刪單/平倉是降風險操作,不可被擋;部位也只剩 60s 輪詢可靠
    com = RecordingCom()
    client = _client(com, enabled=True, audit_path=tmp_path / "a.jsonl")
    assert client._init_com() is True
    client._handle_reply_disconnect(3002)
    assert client.status == "degraded"
    assert "3002" in client.last_error
    res = _run_write(client, lambda: client.cancel_stock_order(CancelOrderRequest(seq_no="S1")))
    assert res.ok is True                      # degraded 不擋寫入
    client._balance_last_ts = 0.0
    client._maybe_query_balance()
    assert ("get_real_balance", "1234567890A") in com.sent   # degraded 仍要定時查部位


def test_profit_rows_apply_only_matching_kind(tmp_path):
    # 同檔現股+融資並存時損益報告每種類一列、store 以 stock_no 為鍵 last-wins:
    # 不過濾的話融資部位會被後到的現股列蓋掉均價 — 資/券成本基礎不可混用
    com = FakeCom()
    client = _client(com, enabled=True, audit_path=tmp_path / "a.jsonl")
    client._handle_balance("3357,C,2000,1944,0,0,3000,0,0,0,0,3000,0,0,3000,0,155.63,A123456789,1234567890")
    client._handle_balance("##")
    client._handle_profit("000,查詢成功")
    client._handle_profit("臺慶科,3357,新台幣,融資,3000,156.00,0.27,468000,464000,12345,150.55,451650,0,0,665,0,1404,135495,316155,89,,2.73,0,,Y")
    client._handle_profit("臺慶科,3357,新台幣,現股,1000,156.00,0.27,468000,464000,12345,999.00,451650,0,0,665,0,1404,135495,316155,89,,2.73,0,,Y")
    client._handle_profit("##,,,,")
    assert client.store.position_for("3357").avg_price == 150.55   # 現股列(999)不可套到融資部位
