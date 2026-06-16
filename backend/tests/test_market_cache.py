from services.local_store.market_cache import MarketCache


def test_replace_and_search_symbols(tmp_path):
    mc = MarketCache(tmp_path / "symbols.json", tmp_path / "daily_ohlc.json")
    mc.load()
    assert mc.symbols_loaded() is False
    mc.replace_symbols([
        {"symbol": "2330", "name": "台積電", "market": "TWSE", "is_etf": False, "is_active": True},
        {"symbol": "2454", "name": "聯發科", "market": "TWSE", "is_etf": False, "is_active": True},
        {"symbol": "0050", "name": "元大台灣50", "market": "TWSE", "is_etf": True, "is_active": True},
    ])
    assert mc.symbols_loaded() is True
    assert mc.has_symbol("2330") is True
    assert [r["symbol"] for r in mc.search("23", 10)] == ["2330"]
    assert [r["symbol"] for r in mc.search("聯發", 10)] == ["2454"]
    mc2 = MarketCache(tmp_path / "symbols.json", tmp_path / "daily_ohlc.json")
    mc2.load()
    assert mc2.has_symbol("0050")


def test_daily_ohlc_upsert_keeps_latest(tmp_path):
    mc = MarketCache(tmp_path / "symbols.json", tmp_path / "daily_ohlc.json")
    mc.load()
    mc.upsert_daily_ohlc([{"symbol": "2330", "date": "2026-05-20",
                           "high": 1.0, "low": 1.0, "close": 1.0}])
    mc.upsert_daily_ohlc([{"symbol": "2330", "date": "2026-05-21",
                           "high": 2.0, "low": 2.0, "close": 2.0}])
    assert mc.get_latest_daily_ohlc("2330")["date"] == "2026-05-21"
    assert mc.get_latest_daily_ohlc("9999") is None


def test_daily_ohlc_identical_upsert_skips_rewrite(tmp_path, monkeypatch):
    # 為何重要:backfill 每天重抓同一批昨日 OHLC 是常態,內容沒變不該全檔重寫
    import services.local_store.market_cache as mc_module
    mc = MarketCache(tmp_path / "symbols.json", tmp_path / "daily_ohlc.json")
    mc.load()
    row = {"symbol": "2330", "date": "2026-05-20", "high": 1.0, "low": 1.0, "close": 1.0}
    mc.upsert_daily_ohlc([row])

    writes = []
    monkeypatch.setattr(mc_module, "atomic_write_json", lambda *a, **k: writes.append(a))
    mc.upsert_daily_ohlc([dict(row)])  # 內容完全相同 → 不寫檔
    assert writes == []
    mc.upsert_daily_ohlc([{**row, "close": 2.0}])  # 同日但值變 → 要寫
    assert len(writes) == 1
    assert mc.get_latest_daily_ohlc("2330")["close"] == 2.0


def test_get_symbol_returns_metadata_or_none(tmp_path):
    mc = MarketCache(tmp_path / "symbols.json", tmp_path / "daily_ohlc.json")
    mc.load()
    mc.replace_symbols([{"symbol": "2330", "name": "台積電", "market": "TWSE", "is_etf": False, "is_active": True}])
    assert mc.get_symbol("2330") == {"symbol": "2330", "name": "台積電", "market": "TWSE", "is_etf": False}
    assert mc.get_symbol("9999") is None


def test_auto_monitor_in_memory(tmp_path):
    mc = MarketCache(tmp_path / "symbols.json", tmp_path / "daily_ohlc.json")
    mc.load()
    mc.replace_auto_monitor([{"symbol": "2330", "change_pct": 5.0, "rank": 1}])
    assert mc.auto_monitor_count() == 1
    assert mc.get_auto_monitor()[0]["symbol"] == "2330"
