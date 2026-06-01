from unittest.mock import MagicMock
from scripts.migrate_supabase_to_local import migrate


def _fake_sb(tables: dict):
    sb = MagicMock()
    def table(name):
        t = MagicMock()
        chain = t.select.return_value
        chain.eq.return_value = chain
        chain.execute.return_value = MagicMock(data=tables.get(name, []))
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
        "active_signals": [], "monitor_list": [], "watchlist": [],
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
