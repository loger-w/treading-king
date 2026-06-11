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


def test_load_skips_non_object_lines(tmp_path):
    # 為何重要:合法 JSON 但非 object 的行(手改/外部損壞)不能炸啟動,
    # 也不能進 rows 讓 query 在 API 時 500
    p = tmp_path / "signals_log.jsonl"
    p.write_text(
        '123\n'
        '"x"\n'
        'null\n'
        'not json\n'
        '{"id": 7, "symbol": "2330", "triggered_at": "2026-05-01T01:00:00+00:00"}\n',
        encoding="utf-8")
    log = SignalsLog(p)
    log.load()  # 不 raise
    assert [r["symbol"] for r in log.query()] == ["2330"]
    assert log.append({"symbol": "2454"})["id"] == 8  # next_id 仍從合法行接續


def test_query_served_from_memory_not_file(tmp_path):
    # 為何重要:append-only 檔無上限成長,query 若每次同步全檔重讀
    # 會隨檔案變大卡住事件迴圈(包括行情 tick 消費)
    p = tmp_path / "signals_log.jsonl"
    log = SignalsLog(p)
    log.load()
    log.append({"active_signal_id": "a", "symbol": "2330"})
    p.unlink()  # 檔案消失也照常回 → 證明查的是記憶體
    assert [r["symbol"] for r in log.query()] == ["2330"]
    assert [r["symbol"] for r in log.today_rows()] == ["2330"]


def test_today_rows_excludes_yesterday(tmp_path):
    # 為何重要:今日觸發次數必須以 Asia/Taipei 日界線切
    log = SignalsLog(tmp_path / "signals_log.jsonl")
    log.load()
    old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    log.append({"active_signal_id": "a", "symbol": "2330", "triggered_at": old})
    log.append({"active_signal_id": "a", "symbol": "2454"})  # 今天
    syms = [r["symbol"] for r in log.today_rows()]
    assert "2454" in syms and "2330" not in syms
