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
    # 為何重要:孤兒 item 會讓前端出現「幽靈股票」
    cfg = ConfigStore(tmp_path / "config.json")
    cfg.load()
    g = cfg.create_group("X")
    cfg.add_item(g["id"], "2330")
    assert cfg.delete_group(g["id"]) is True
    assert cfg.list_items(g["id"]) == []


def test_add_item_dedup_and_counts(tmp_path):
    cfg = ConfigStore(tmp_path / "config.json")
    cfg.load()
    g = cfg.create_group("X")
    cfg.add_item(g["id"], "2330")
    cfg.add_item(g["id"], "2330")  # 同檔不重複
    assert len(cfg.list_items(g["id"])) == 1
    assert cfg.item_counts()[g["id"]] == 1
