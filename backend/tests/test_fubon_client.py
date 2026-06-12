"""FubonClient 登入語意測試 — 不碰真 SDK,注入假 fubon_neo.sdk。

守的是這套系統的核心決策:行情授權必須先登入,但本系統純行情、下單走群益,
故「交易層登入失敗」不該擋住行情 —— 真正能否取得行情由 init_realtime 判定。
這批測試把「為什麼這樣設計」釘死,防止日後 review 又把 DMA 失敗改回 fail-hard。
"""
from __future__ import annotations

import sys
import types

import pytest

from services.fubon_client import FubonClient, FubonStatus


class _Result:
    """模擬 SDK Result:富邦登入失敗不丟例外、只回 is_success=False + message。"""

    def __init__(self, is_success: bool, message: str | None = None) -> None:
        self.is_success = is_success
        self.message = message


def _install_fake_sdk(monkeypatch, *, login_result, init_realtime_raises=None):
    """把假的 fubon_neo.sdk 注入 sys.modules,回傳記錄呼叫的 dict。"""
    rec: dict = {}

    class FakeSDK:
        def __init__(self) -> None:
            self.init_realtime_calls = 0
            rec["sdk"] = self

        def apikey_dma_login(self, pid, key):
            rec["login"] = ("dma", pid, key)
            return login_result

        def apikey_login(self, pid, key, cert_path, cert_pass):
            rec["login"] = ("cert", pid, key, cert_path, cert_pass)
            return login_result

        def init_realtime(self, mode):
            self.init_realtime_calls += 1
            if init_realtime_raises is not None:
                raise init_realtime_raises

        def logout(self):
            rec["logout"] = True

    class Mode:
        Normal = "Normal"
        Speed = "Speed"

    sdk_mod = types.ModuleType("fubon_neo.sdk")
    sdk_mod.FubonSDK = FakeSDK
    sdk_mod.Mode = Mode
    monkeypatch.setitem(sys.modules, "fubon_neo", types.ModuleType("fubon_neo"))
    monkeypatch.setitem(sys.modules, "fubon_neo.sdk", sdk_mod)
    return rec


@pytest.fixture
def dma_env(monkeypatch):
    """純行情情境:有帳號+金鑰、無富邦憑證 → 走 apikey_dma_login。"""
    monkeypatch.setenv("FUBON_ACCOUNT", "A123456789")
    monkeypatch.setenv("FUBON_API_KEY", "test-key")
    monkeypatch.delenv("FUBON_PERSONAL_ID", raising=False)
    monkeypatch.delenv("FUBON_CERT_PATH", raising=False)


def test_dma_login_failure_does_not_block_marketdata(monkeypatch, dma_env):
    # 金鑰有效、只是沒申請 DMA 交易權 → 富邦回「查無資料」。
    # 行情不該因此中斷:init_realtime 仍須被呼叫、SDK 仍須掛上。
    rec = _install_fake_sdk(monkeypatch, login_result=_Result(False, "查無資料"))
    client = FubonClient()

    client._do_login_sync()  # 不得 raise

    assert client._sdk is rec["sdk"]
    assert rec["sdk"].init_realtime_calls == 1
    assert rec["login"][0] == "dma"


def test_marketdata_init_failure_raises_with_login_message(monkeypatch, dma_env):
    # 金鑰真的壞掉:登入訊息不同、且 init_realtime 會掛 → 必須 raise,
    # 且錯誤要同時帶出登入訊息與 init_realtime 真因,不被晦澀 token 例外遮蔽。
    _install_fake_sdk(
        monkeypatch,
        login_result=_Result(False, "API認證,APIKEY尚未申請"),
        init_realtime_raises=ValueError("尚未登入，無法產生信任認證"),
    )
    client = FubonClient()

    with pytest.raises(RuntimeError) as ei:
        client._do_login_sync()

    assert "APIKEY尚未申請" in str(ei.value)
    assert "尚未登入" in str(ei.value)
    assert client._sdk is None


@pytest.mark.asyncio
async def test_init_status_ok_despite_dma_login_failure(monkeypatch, dma_env):
    # 端到端:DMA 登入失敗(查無資料)下,對外狀態仍須是 OK(行情可用)。
    _install_fake_sdk(monkeypatch, login_result=_Result(False, "查無資料"))
    client = FubonClient()

    await client.init()

    assert client.status == FubonStatus.OK


def test_dma_login_granted_uses_dma_and_succeeds(monkeypatch, dma_env):
    # 將來若申請到 DMA 交易權 → 登入回成功,程式零修改自動沿用同一條 dma 路徑。
    rec = _install_fake_sdk(monkeypatch, login_result=_Result(True))
    client = FubonClient()

    client._do_login_sync()

    assert client._sdk is rec["sdk"]
    assert rec["login"][0] == "dma"


def test_cert_present_uses_apikey_login(monkeypatch, dma_env):
    # 設了富邦憑證 → 改走 apikey_login(帶 cert_path/cert_pass)。
    monkeypatch.setenv("FUBON_CERT_PATH", r"C:\fake\fubon.pfx")
    monkeypatch.setenv("FUBON_CERT_PASS", "pw")
    rec = _install_fake_sdk(monkeypatch, login_result=_Result(True))
    client = FubonClient()

    client._do_login_sync()

    assert rec["login"][0] == "cert"
    assert rec["login"][3] == r"C:\fake\fubon.pfx"
    assert rec["login"][4] == "pw"
