from services.local_store import get_local_store
from services.signal_writer import get_signal_writer


def test_append_writes_to_signals_log(local_store_tmp):
    w = get_signal_writer()
    w.append({"active_signal_id": "a", "symbol": "2330",
              "trigger_price": 1.0, "trigger_volume": 1, "context_json": {}})
    rows = get_local_store().signals.query(symbol="2330")
    assert len(rows) == 1 and rows[0]["active_signal_id"] == "a"
