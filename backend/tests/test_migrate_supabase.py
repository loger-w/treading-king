import pytest
from unittest.mock import MagicMock
from scripts.migrate_supabase_to_local import migrate


def _fake_sb(tables: dict):
    """模擬 PostgREST 的 range 分頁:execute 只回 range(start, end) 那一頁。"""
    sb = MagicMock()
    def table(name):
        rows = tables.get(name, [])
        t = MagicMock()
        chain = t.select.return_value
        chain.eq.return_value = chain
        chain.order.return_value = chain
        state = {"slice": (0, len(rows) - 1)}
        def _range(start, end):
            state["slice"] = (start, end)
            return chain
        chain.range.side_effect = _range
        def _execute():
            start, end = state["slice"]
            return MagicMock(data=rows[start:end + 1])
        chain.execute.side_effect = _execute
        return t
    sb.table.side_effect = table
    return sb


def test_migrate_writes_config_without_user_label(local_store_tmp):
    sb = _fake_sb({
        "bookmark_groups": [{"id": "g1", "user_label": "loger", "name": "自選",
                             "sort_order": 0, "is_system": False, "source_type": None,
                             "created_at": "2026-05-01T00:00:00Z"}],
        "watchlist_items": [{"id": "i1", "group_id": "g1", "symbol": "2330",
                             "added_at": "2026-05-01T00:00:00Z", "note": None}],
        "active_signals": [], "monitor_list": [],
        "signals_log": [{"id": 1, "active_signal_id": None, "symbol": "2330",
                         "triggered_at": "2026-05-01T01:00:00Z", "trigger_price": 900.0,
                         "trigger_volume": 5, "context_json": {}, "user_label": "loger"}],
    })
    summary = migrate(sb, user_label="loger")
    from services.local_store import get_local_store
    st = get_local_store(); st.init()
    grp = st.config.list_groups()
    assert any(g["name"] == "自選" for g in grp)
    assert "user_label" not in grp[0]
    assert st.signals.query(symbol="2330")
    assert summary["bookmark_groups"] == 1
    assert summary["signals_log"] == 1
    assert summary["watchlist_items"] == 1


def test_migrate_paginates_past_page_size(local_store_tmp, monkeypatch):
    # 為何重要:PostgREST 單次回應預設上限 1000 筆,不分頁會靜默截斷
    # (實際發生過:signals_log 漏了 435 筆)。把頁大小縮成 2 驗分頁迴圈撈完。
    import scripts.migrate_supabase_to_local as mig
    monkeypatch.setattr(mig, "_PAGE_SIZE", 2)
    sb = _fake_sb({
        "bookmark_groups": [{"id": "g1", "user_label": "loger", "name": "自選",
                             "sort_order": 0, "is_system": False, "source_type": None,
                             "created_at": "2026-05-01T00:00:00Z"}],
        "watchlist_items": [{"id": f"i{i}", "group_id": "g1", "symbol": f"23{i:02d}",
                             "added_at": "2026-05-01T00:00:00Z", "note": None}
                            for i in range(5)],
        "active_signals": [], "monitor_list": [],
        "signals_log": [{"id": i, "active_signal_id": None, "symbol": "2330",
                         "triggered_at": f"2026-05-01T0{i}:00:00Z", "trigger_price": 1.0,
                         "trigger_volume": 1, "context_json": {}, "user_label": "loger"}
                        for i in range(5)],
    })
    summary = mig.migrate(sb, user_label="loger")
    assert summary["watchlist_items"] == 5  # > 頁大小,沒被截斷
    assert summary["signals_log"] == 5
    from services.local_store import get_local_store
    st = get_local_store(); st.init()
    assert len(st.signals.query(symbol="2330")) == 5


def test_migrate_aborts_on_rerun(local_store_tmp):
    sb = _fake_sb({
        "bookmark_groups": [], "watchlist_items": [], "active_signals": [],
        "monitor_list": [], "signals_log": [
            {"id": 1, "symbol": "2330", "triggered_at": "2026-05-01T01:00:00Z"}],
    })
    migrate(sb, user_label="loger")          # 第一次跑:建立 signals_log 檔案
    with pytest.raises(RuntimeError):
        migrate(sb, user_label="loger")      # 第二次跑:必須中止
    # force=True 可繞過守衛
    migrate(sb, user_label="loger", force=True)
