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


def test_audit_writes_action_field(tmp_path):
    import json
    from services.capital_models import CancelOrderRequest
    p = tmp_path / "a.jsonl"
    capital_audit.write(p, env="prod", req=CancelOrderRequest(seq_no="S1"),
                        blocked="下單總開關關閉(CAPITAL_ORDER_ENABLED=false)", action="cancel")
    entry = json.loads(p.read_text(encoding="utf-8").splitlines()[-1])
    assert entry["action"] == "cancel"
    assert entry["req"]["seq_no"] == "S1"


def test_audit_default_action_is_order(tmp_path):
    import json
    from services.capital_models import StockOrderRequest, BuySell
    p = tmp_path / "a.jsonl"
    capital_audit.write(p, env="prod",
                        req=StockOrderRequest(stock_no="2330", buy_sell=BuySell.BUY, price=500.0, qty=1),
                        blocked="x")
    entry = json.loads(p.read_text(encoding="utf-8").splitlines()[-1])
    assert entry["action"] == "order"
