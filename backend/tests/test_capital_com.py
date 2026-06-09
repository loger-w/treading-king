"""capital_com / factory:SKCOM.dll 載入路徑決策。

真實 COM 載入需要群益元件,無法在 CI/mock 跑;這裡只測「決定怎麼載」的純邏輯,
以及 CAPITAL_DLL_DIR 有沒有從 env 一路傳到 COM 層。
"""
import os

import services.capital_factory as capital_factory
from services.capital_com import SkcomCapitalCom, _resolve_skcom_load


def test_resolve_no_dir_keeps_bare_name():
    """沒設 dll_dir → 裸檔名 + 不加搜尋路徑(沿用舊行為,靠 PATH/CWD 找)。"""
    assert _resolve_skcom_load(None) == (None, "SKCOM.dll")


def test_resolve_blank_dir_keeps_bare_name():
    """空字串 / 純空白都視為沒設,不可組出 '\\SKCOM.dll' 這種爛路徑。"""
    assert _resolve_skcom_load("") == (None, "SKCOM.dll")
    assert _resolve_skcom_load("   ") == (None, "SKCOM.dll")


def test_resolve_with_dir_uses_absolute_path():
    """有設 dll_dir → 回該資料夾(要加進 DLL 搜尋路徑)+ 絕對路徑給 GetModule。"""
    d = r"C:\CapitalAPI\x64"
    assert _resolve_skcom_load(d) == (d, os.path.join(d, "SKCOM.dll"))


def test_factory_injects_dll_dir_into_com(monkeypatch):
    """CAPITAL_DLL_DIR 必須從 env 一路傳到 COM 層,setup() 才載得到 DLL。"""
    monkeypatch.setattr(capital_factory, "_client", None)
    monkeypatch.setenv("CAPITAL_USER_ID", "u")
    monkeypatch.setenv("CAPITAL_DLL_DIR", r"C:\CapitalAPI\x64")
    client = capital_factory.get_capital()
    assert client is not None
    assert isinstance(client._com, SkcomCapitalCom)
    assert client._com._dll_dir == r"C:\CapitalAPI\x64"


def test_factory_no_dll_dir_is_none(monkeypatch):
    """沒設 CAPITAL_DLL_DIR → COM 層拿到 None(走裸檔名回退)。"""
    monkeypatch.setattr(capital_factory, "_client", None)
    monkeypatch.setenv("CAPITAL_USER_ID", "u")
    monkeypatch.delenv("CAPITAL_DLL_DIR", raising=False)
    client = capital_factory.get_capital()
    assert client is not None
    assert client._com._dll_dir is None
