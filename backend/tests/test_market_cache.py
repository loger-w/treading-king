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


def test_top_gainers_in_memory(tmp_path):
    mc = MarketCache(tmp_path / "symbols.json", tmp_path / "daily_ohlc.json")
    mc.load()
    mc.replace_top_gainers([{"symbol": "2330", "change_pct": 5.0, "rank": 1}])
    assert mc.top_gainers_count() == 1
    assert mc.get_top_gainers()[0]["symbol"] == "2330"
