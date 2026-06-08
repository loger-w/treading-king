"""Discord notifier — 訊號觸發 POST 給 bot(失敗 silent log)。"""
from unittest.mock import AsyncMock, patch

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
    fake_client.post = AsyncMock()
    with patch("services.discord_notifier.httpx.AsyncClient", return_value=fake_client):
        await discord_notifier.send_signal(
            rule_name="漲停打開碰CDP",
            symbol="2330",
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
