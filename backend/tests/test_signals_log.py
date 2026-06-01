from datetime import datetime, timedelta, timezone

from services.local_store.signals_log import SignalsLog


def test_append_assigns_id_and_persists(tmp_path):
    log = SignalsLog(tmp_path / "signals_log.jsonl")
    log.load()
    r = log.append({"active_signal_id": "a", "symbol": "2330",
                    "trigger_price": 925.0, "trigger_volume": 10, "context_json": {}})
    assert r["id"] == 1 and r["triggered_at"]
    log2 = SignalsLog(tmp_path / "signals_log.jsonl")
    log2.load()
    assert log2.append({"active_signal_id": "a", "symbol": "2330"})["id"] == 2


def test_query_filters_and_limits(tmp_path):
    log = SignalsLog(tmp_path / "signals_log.jsonl")
    log.load()
    log.append({"active_signal_id": "a", "symbol": "2330"})
    log.append({"active_signal_id": "b", "symbol": "2454"})
    assert [r["symbol"] for r in log.query(symbol="2330")] == ["2330"]
    assert len(log.query(limit=1)) == 1


def test_today_rows_excludes_yesterday(tmp_path):
    # 為何重要:今日觸發次數必須以 Asia/Taipei 日界線切
    log = SignalsLog(tmp_path / "signals_log.jsonl")
    log.load()
    old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    log.append({"active_signal_id": "a", "symbol": "2330", "triggered_at": old})
    log.append({"active_signal_id": "a", "symbol": "2454"})  # 今天
    syms = [r["symbol"] for r in log.today_rows()]
    assert "2454" in syms and "2330" not in syms
