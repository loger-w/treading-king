"""Discord notifier — 訊號推送(失敗 silent log)。"""
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import discord_notifier


@pytest.fixture(autouse=True)
def _reset_cached_webhook():
    """每個 test reset module-level cache,避免測試間互相污染。"""
    discord_notifier._WEBHOOK_URL = None
    yield
    discord_notifier._WEBHOOK_URL = None


@pytest.mark.asyncio
async def test_send_signal_noop_when_webhook_unset(monkeypatch):
    monkeypatch.delenv("SIGNALS_DISCORD_WEBHOOK_URL", raising=False)
    with patch("services.discord_notifier.httpx.AsyncClient") as mock_client:
        await discord_notifier.send_signal(
            rule_name="t", symbol="2330", price=600.0, volume=10,
            triggered_at_iso="2026-05-26T01:00:00+00:00",
        )
        mock_client.assert_not_called()


@pytest.mark.asyncio
async def test_send_signal_posts_embed_when_webhook_set(monkeypatch):
    monkeypatch.setenv("SIGNALS_DISCORD_WEBHOOK_URL", "https://discord.test/webhook")
    fake_client = AsyncMock()
    fake_client.__aenter__.return_value = fake_client
    fake_client.__aexit__.return_value = False
    fake_client.post = AsyncMock()
    with patch("services.discord_notifier.httpx.AsyncClient", return_value=fake_client):
        await discord_notifier.send_signal(
            rule_name="MA_5 觸碰",
            symbol="2330",
            price=600.0,
            volume=10,
            triggered_at_iso="2026-05-26T01:00:00+00:00",
            ma_touch={"level": "sma_5", "direction": "from_below", "role": "resistance"},
        )
        fake_client.post.assert_called_once()
        call = fake_client.post.call_args
        assert call.args[0] == "https://discord.test/webhook"
        payload = call.kwargs["json"]
        embed = payload["embeds"][0]
        assert "MA_5 觸碰" in embed["title"]
        field_names = [f["name"] for f in embed["fields"]]
        assert "代號" in field_names
        assert "MA" in field_names


@pytest.mark.asyncio
async def test_send_signal_swallows_httpx_errors(monkeypatch, caplog):
    monkeypatch.setenv("SIGNALS_DISCORD_WEBHOOK_URL", "https://discord.test/webhook")
    fake_client = AsyncMock()
    fake_client.__aenter__.return_value = fake_client
    fake_client.__aexit__.return_value = False
    fake_client.post = AsyncMock(side_effect=Exception("network down"))
    with patch("services.discord_notifier.httpx.AsyncClient", return_value=fake_client):
        # 不該 raise
        await discord_notifier.send_signal(
            rule_name="t", symbol="2330", price=600.0, volume=10,
            triggered_at_iso="2026-05-26T01:00:00+00:00",
        )
    assert "Discord signal notify failed" in caplog.text
