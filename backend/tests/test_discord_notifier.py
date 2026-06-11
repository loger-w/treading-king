"""Discord notifier — 訊號觸發 POST 給 bot(失敗 silent log)。"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import discord_notifier


@pytest.fixture(autouse=True)
def _reset_cached_url():
    """每個 test reset module-level cache,避免測試間互相污染。"""
    discord_notifier._PUSH_URL = None
    yield
    discord_notifier._PUSH_URL = None


@pytest.mark.asyncio
async def test_send_signal_noop_when_push_url_unset(monkeypatch):
    monkeypatch.delenv("SIGNALS_BOT_PUSH_URL", raising=False)
    with patch("services.discord_notifier.httpx.AsyncClient") as mock_client:
        await discord_notifier.send_signal(
            rule_name="t", symbol="2330", price=600.0, volume=10,
            triggered_at_iso="2026-06-08T05:30:00+00:00",
        )
        mock_client.assert_not_called()


@pytest.mark.asyncio
async def test_send_signal_posts_to_bot_when_url_set(monkeypatch):
    monkeypatch.setenv("SIGNALS_BOT_PUSH_URL", "http://127.0.0.1:8787/push-signal")
    fake_client = AsyncMock()
    fake_client.__aenter__.return_value = fake_client
    fake_client.__aexit__.return_value = False
    fake_client.post = AsyncMock(return_value=MagicMock(status_code=202))
    with patch("services.discord_notifier.httpx.AsyncClient", return_value=fake_client):
        await discord_notifier.send_signal(
            rule_name="漲停打開碰CDP",
            symbol="2330",
            name="台積電",
            price=600.0,
            volume=10,
            triggered_at_iso="2026-06-08T05:30:00+00:00",
            cdp_touch={"level": "AH", "direction": "from_above", "role": "support", "touch_index": 2},
            ma_touch=None,
        )
        fake_client.post.assert_called_once()
        call = fake_client.post.call_args
        assert call.args[0] == "http://127.0.0.1:8787/push-signal"
        body = call.kwargs["json"]
        assert body["symbol"] == "2330"
        assert body["name"] == "台積電"
        assert body["rule_name"] == "漲停打開碰CDP"
        assert body["price"] == 600.0
        assert body["volume"] == 10
        assert body["triggered_at"] == "2026-06-08T05:30:00+00:00"
        assert body["cdp_touch"]["level"] == "AH"
        assert body["ma_touch"] is None


@pytest.mark.asyncio
async def test_send_signal_swallows_errors(monkeypatch, caplog):
    monkeypatch.setenv("SIGNALS_BOT_PUSH_URL", "http://127.0.0.1:8787/push-signal")
    fake_client = AsyncMock()
    fake_client.__aenter__.return_value = fake_client
    fake_client.__aexit__.return_value = False
    fake_client.post = AsyncMock(side_effect=Exception("connection refused"))
    with patch("services.discord_notifier.httpx.AsyncClient", return_value=fake_client):
        # 不該 raise
        await discord_notifier.send_signal(
            rule_name="t", symbol="2330", price=600.0, volume=10,
            triggered_at_iso="2026-06-08T05:30:00+00:00",
        )
    assert "Discord signal push failed" in caplog.text


@pytest.mark.asyncio
async def test_send_signal_logs_non_2xx_response(monkeypatch, caplog):
    """httpx 不對 4xx/5xx 拋例外 — bot 回非 2xx(schema 漂移 / URL 路徑錯)
    必須留 warning 痕跡,否則圖卡無聲丟失,只能事後對 signals_log 才發現。"""
    monkeypatch.setenv("SIGNALS_BOT_PUSH_URL", "http://127.0.0.1:8787/push-signal")
    fake_client = AsyncMock()
    fake_client.__aenter__.return_value = fake_client
    fake_client.__aexit__.return_value = False
    fake_client.post = AsyncMock(
        return_value=MagicMock(status_code=400, text='{"error":"invalid payload"}')
    )
    with patch("services.discord_notifier.httpx.AsyncClient", return_value=fake_client):
        await discord_notifier.send_signal(
            rule_name="t", symbol="2330", price=600.0, volume=10,
            triggered_at_iso="2026-06-08T05:30:00+00:00",
        )
    assert "Discord signal push rejected" in caplog.text
    assert "400" in caplog.text


@pytest.mark.asyncio
async def test_send_signal_name_defaults_to_none_in_body(monkeypatch):
    """未帶 name(期貨 / symbols 快取查不到)→ payload name 為 None。"""
    monkeypatch.setenv("SIGNALS_BOT_PUSH_URL", "http://127.0.0.1:8787/push-signal")
    fake_client = AsyncMock()
    fake_client.__aenter__.return_value = fake_client
    fake_client.__aexit__.return_value = False
    fake_client.post = AsyncMock(return_value=MagicMock(status_code=202))
    with patch("services.discord_notifier.httpx.AsyncClient", return_value=fake_client):
        await discord_notifier.send_signal(
            rule_name="t", symbol="MXFF6", price=20000.0, volume=1,
            triggered_at_iso="2026-06-08T05:30:00+00:00",
        )
        body = fake_client.post.call_args.kwargs["json"]
        assert body["name"] is None
