from services.local_store.config_store import ConfigStore


def test_fresh_store_seeds_default_bookmark(tmp_path):
    cfg = ConfigStore(tmp_path / "config.json")
    cfg.load()
    groups = cfg.list_groups()
    assert len(groups) == 1
    assert groups[0]["name"] == "自選"
    assert groups[0]["is_system"] is False


def test_create_and_list_group_persists(tmp_path):
    path = tmp_path / "config.json"
    cfg = ConfigStore(path)
    cfg.load()
    g = cfg.create_group("強勢股", sort_order=1)
    assert g["id"]
    cfg2 = ConfigStore(path)
    cfg2.load()
    assert any(x["name"] == "強勢股" for x in cfg2.list_groups())


def test_delete_group_cascades_items(tmp_path):
    # 為何重要:孤兒 item 會讓前端出現「幽靈股票」;且 cascade 必須真的寫到磁碟
    path = tmp_path / "config.json"
    cfg = ConfigStore(path)
    cfg.load()
    g = cfg.create_group("X")
    cfg.add_item(g["id"], "2330")
    assert cfg.delete_group(g["id"]) is True
    assert cfg.list_items(g["id"]) == []
    # round-trip:cascade 必須持久化(否則重載又出現孤兒 item)
    cfg2 = ConfigStore(path)
    cfg2.load()
    assert cfg2.list_items(g["id"]) == []


def test_add_item_dedup_and_counts(tmp_path):
    cfg = ConfigStore(tmp_path / "config.json")
    cfg.load()
    g = cfg.create_group("X")
    cfg.add_item(g["id"], "2330")
    cfg.add_item(g["id"], "2330")  # 同檔不重複
    assert len(cfg.list_items(g["id"])) == 1
    assert cfg.item_counts()[g["id"]] == 1


def test_active_signal_crud(tmp_path):
    path = tmp_path / "config.json"
    cfg = ConfigStore(path)
    cfg.load()
    s = cfg.create_active_signal({
        "name": "突破", "filter_json": {"op": ">"}, "scope": {"type": "watchlist"},
        "cooldown_seconds": 1800, "enabled": True, "notify_discord": True,
    })
    assert s["id"] and s["created_at"]
    cfg.update_active_signal(s["id"], {"enabled": False})
    assert cfg.get_active_signal(s["id"])["enabled"] is False  # 覆蓋 get + 確認 update
    assert cfg.list_active_signals(enabled_only=True) == []
    assert cfg.delete_active_signal(s["id"]) is True
    # round-trip:刪除必須持久化
    cfg2 = ConfigStore(path)
    cfg2.load()
    assert cfg2.get_active_signal(s["id"]) is None


def test_disable_all_active_signals_returns_count(tmp_path):
    cfg = ConfigStore(tmp_path / "config.json")
    cfg.load()
    cfg.create_active_signal({"name": "a", "filter_json": {}, "scope": {},
                              "cooldown_seconds": 60, "enabled": True, "notify_discord": True})
    cfg.create_active_signal({"name": "b", "filter_json": {}, "scope": {},
                              "cooldown_seconds": 60, "enabled": True, "notify_discord": True})
    assert cfg.disable_all_active_signals() == 2
    assert cfg.list_active_signals(enabled_only=True) == []
    assert cfg.disable_all_active_signals() == 0  # 全已停用 → 0


def test_monitor_list_add_remove_dedup(tmp_path):
    path = tmp_path / "config.json"
    cfg = ConfigStore(path)
    cfg.load()
    cfg.add_monitor("2330")
    cfg.add_monitor("2330")
    assert [m["symbol"] for m in cfg.list_monitor()] == ["2330"]
    assert cfg.remove_monitor("2330") is True
    cfg2 = ConfigStore(path)
    cfg2.load()
    assert cfg2.list_monitor() == []  # remove 必須持久化


def test_export_then_import_replaces(tmp_path):
    src = ConfigStore(tmp_path / "a.json")
    src.load()
    src.create_group("帶走的")
    snapshot = src.export_config()
    assert snapshot["schema_version"] == 1
    assert snapshot["exported_at"]

    dst = ConfigStore(tmp_path / "b.json")
    dst.load()
    dst.create_group("本機舊的")
    dst.import_config(snapshot)
    names = [g["name"] for g in dst.list_groups()]
    assert "帶走的" in names
    assert "本機舊的" not in names  # 整包取代


def test_import_backs_up_old_file(tmp_path):
    # 為何重要:誤匯入可從備份救回
    path = tmp_path / "config.json"
    cfg = ConfigStore(path)
    cfg.load()
    cfg.create_group("舊")
    cfg.import_config({"schema_version": 1, "bookmark_groups": [], "watchlist_items": [],
                       "active_signals": [], "monitor_list": []})
    backups = list(tmp_path.glob("config.backup-*.json"))
    assert len(backups) == 1


def test_import_rejects_bad_schema(tmp_path):
    cfg = ConfigStore(tmp_path / "config.json")
    cfg.load()
    import pytest
    with pytest.raises(ValueError):
        cfg.import_config({"schema_version": 999})
