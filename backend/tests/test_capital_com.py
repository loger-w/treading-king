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


def test_setup_retains_reply_event_refs(monkeypatch):
    """根因防呆:setup() 必須存住 reply 事件 sink + GetEvents 連線。

    若丟掉(像 `GetEvents(reply, _ReplyEvents())` 不接回傳),CPython refcount 歸零即 GC
    → comtypes Unadvise → 登入時群益看不到 OnReplyMessage → 回
    SK_WARNING_REGISTER_REPLYLIB_ONREPLYMESSAGE_FIRST。官方範例把 sink 與 handler 都存
    在長生命變數。這裡 mock 掉 comtypes 層,驗證 setup() 有把兩者存到 self。
    """
    import sys
    import types

    captured = []   # (source, sink, conn) — setup 現在 advise 兩條:reply + order(庫存事件)

    client = types.ModuleType("comtypes.client")
    client.GetModule = lambda *a, **k: None
    client.CreateObject = lambda coclass, interface=None: types.SimpleNamespace(coclass=coclass)

    def fake_get_events(source, sink):
        conn = types.SimpleNamespace(tag=f"advise-{len(captured)}")
        captured.append((source, sink, conn))
        return conn

    client.GetEvents = fake_get_events

    comtypes_mod = types.ModuleType("comtypes")
    gen = types.ModuleType("comtypes.gen")
    skcomlib = types.ModuleType("comtypes.gen.SKCOMLib")
    for nm in ("SKCenterLib", "ISKCenterLib", "SKOrderLib", "ISKOrderLib", "SKReplyLib", "ISKReplyLib"):
        setattr(skcomlib, nm, nm)
    comtypes_mod.client = client
    comtypes_mod.gen = gen
    gen.SKCOMLib = skcomlib

    monkeypatch.setitem(sys.modules, "comtypes", comtypes_mod)
    monkeypatch.setitem(sys.modules, "comtypes.client", client)
    monkeypatch.setitem(sys.modules, "comtypes.gen", gen)
    monkeypatch.setitem(sys.modules, "comtypes.gen.SKCOMLib", skcomlib)

    com = SkcomCapitalCom()
    com.setup()

    by_sink = {id(sink): (source, sink, conn) for source, sink, conn in captured}
    assert len(captured) == 2                         # reply + order 兩條 advise
    # reply 那條:sink 與 conn 都要存到 self
    _, reply_sink, reply_conn = by_sink[id(com._reply_sink)]
    assert com._reply_conn is reply_conn
    assert reply_sink is not None
    # order(庫存事件)那條同理 — 丟掉會被 GC → Unadvise,OnRealBalanceReport 永遠收不到
    _, order_sink, order_conn = by_sink[id(com._order_sink)]
    assert com._order_conn is order_conn
    assert order_sink is not None


def test_order_events_sink_forwards_balance_and_swallows_exception():
    from services.capital_com import _OrderEvents

    got = []
    sink = _OrderEvents(on_balance=got.append)
    sink.OnRealBalanceReport("TS,1234567,2330,...")
    assert got == ["TS,1234567,2330,..."]

    def boom(_):
        raise RuntimeError("handler boom")
    sink2 = _OrderEvents(on_balance=boom)
    sink2.OnRealBalanceReport("x")   # event-loop must not see the exception


def test_order_events_sink_none_handler_is_noop():
    from services.capital_com import _OrderEvents
    _OrderEvents(on_balance=None).OnRealBalanceReport("x")
