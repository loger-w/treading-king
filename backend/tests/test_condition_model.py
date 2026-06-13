"""驗 ConditionField 含 sma_5 / sma_20,Condition 接受這些 field。"""
import pytest
from pydantic import ValidationError

from models.condition import ALL_FIELDS, Condition


def test_sma_fields_in_all_fields():
    assert "sma_5"  in ALL_FIELDS
    assert "sma_20" in ALL_FIELDS


def test_condition_accepts_sma_5_field():
    c = Condition(field="sma_5", operator="gte", value=100.0)
    assert c.field == "sma_5"


def test_condition_value_can_reference_sma_20():
    """value=sma_20 表示「跟 sma_20 比較」(cross-field)。"""
    c = Condition(field="close", operator="gte", value="sma_20")
    assert c.value == "sma_20"


def test_condition_rejects_unknown_field():
    with pytest.raises(ValidationError):
        Condition(field="sma_60", operator="gte", value=100.0)  # type: ignore


def test_ma_proximity_default_levels_both():
    from models.condition import MAProximityCondition
    p = MAProximityCondition()
    assert p.levels == ["sma_5", "sma_20"]
    assert p.tolerance_ticks == 0


def test_ma_proximity_rejects_invalid_level():
    from models.condition import MAProximityCondition
    with pytest.raises(ValidationError):
        MAProximityCondition(levels=["sma_60"])  # type: ignore


def test_cdp_proximity_rearm_defaults_to_none():
    """未顯式設定 = None,實際距離由引擎解析(max(預設, tolerance+1))。"""
    from models.condition import CdpProximityCondition
    c = CdpProximityCondition()
    assert c.rearm_ticks is None


def test_cdp_proximity_high_tolerance_without_rearm_loads():
    """回歸:tolerance 5~10 不帶 rearm_ticks 必須可載入 — 前端編輯器允許
    tolerance 0~10 且不送 rearm 欄位;舊設定檔/匯入檔同樣不能因新欄位失效。"""
    from models.condition import CdpProximityCondition
    for tol in (5, 7, 10):
        c = CdpProximityCondition(tolerance_ticks=tol)
        assert c.rearm_ticks is None


def test_cdp_proximity_explicit_rearm_must_exceed_tolerance():
    from models.condition import CdpProximityCondition
    with pytest.raises(ValidationError):
        CdpProximityCondition(tolerance_ticks=5, rearm_ticks=3)


def test_cdp_proximity_rearm_zero_disables_regardless_of_tolerance():
    from models.condition import CdpProximityCondition
    c = CdpProximityCondition(tolerance_ticks=5, rearm_ticks=0)
    assert c.rearm_ticks == 0


def test_ma_proximity_rearm_same_rules():
    from models.condition import MAProximityCondition
    assert MAProximityCondition().rearm_ticks is None
    assert MAProximityCondition(tolerance_ticks=8).rearm_ticks is None
    with pytest.raises(ValidationError):
        MAProximityCondition(tolerance_ticks=5, rearm_ticks=5)  # 必須「大於」


def test_active_signal_out_loads_row_with_high_tolerance():
    """refresh_active_signals 走 ActiveSignalOut 物化 filter_json — 這條路炸掉
    會讓磁碟上的規則被靜默跳過,必須保證舊 row 永遠可載入。"""
    from models.condition import ActiveSignalOut
    out = ActiveSignalOut(
        id="x", created_at="2026-01-01", name="t",
        filter_json={"cdp_proximity": {"levels": ["nh"], "tolerance_ticks": 6}},
        scope={"type": "watchlist"},
    )
    assert out.filter_json.cdp_proximity.rearm_ticks is None


def test_active_filter_schema_bumps_to_5():
    from models.condition import ActiveFilter
    f = ActiveFilter(conditions=[Condition(field="close", operator="gt", value=100)])
    assert f.schema_version == 6  # 5→6:加 cdp_breakout_confirm strategy
    assert f.ma_proximity is None


def test_day_metric_fields_in_all_fields():
    assert "day_change_pct" in ALL_FIELDS
    assert "day_volume" in ALL_FIELDS


def test_condition_accepts_day_change_pct():
    c = Condition(field="day_change_pct", operator="gt", value=6)
    assert c.field == "day_change_pct"


def test_condition_value_can_reference_day_volume():
    c = Condition(field="close", operator="gt", value="day_volume")
    assert c.value == "day_volume"


def test_active_filter_loads_old_schema_2_data():
    """schema_version=2 的舊 filter_json 要能正常 load,ma_proximity 自動補 None。"""
    from models.condition import ActiveFilter
    old = {
        "schema_version": 2,
        "conditions": [{"field": "close", "operator": "gt", "value": 100}],
        "logic": "AND",
        "window_conditions": [],
        "cdp_proximity": None,
    }
    f = ActiveFilter(**old)
    assert f.ma_proximity is None


def test_active_signal_create_default_notify_discord_true():
    from models.condition import ActiveSignalCreate, ActiveFilter, Condition, WatchlistScope
    payload = ActiveSignalCreate(
        name="t",
        filter_json=ActiveFilter(conditions=[Condition(field="close", operator="gt", value=0)]),
        scope=WatchlistScope(type="watchlist"),
    )
    assert payload.notify_discord is True


def test_active_signal_create_notify_discord_can_be_false():
    from models.condition import ActiveSignalCreate, ActiveFilter, Condition, WatchlistScope
    payload = ActiveSignalCreate(
        name="t",
        filter_json=ActiveFilter(conditions=[Condition(field="close", operator="gt", value=0)]),
        scope=WatchlistScope(type="watchlist"),
        notify_discord=False,
    )
    assert payload.notify_discord is False


def test_active_signal_create_rejects_symbols_scope():
    """引擎一律以 monitor_list 為評估範圍 — create/update 收 symbols scope
    會讓直接打 API 的 client 以為訊號被限縮在指定清單,必須在驗證層擋掉。"""
    from models.condition import ActiveSignalCreate
    with pytest.raises(ValidationError):
        ActiveSignalCreate(
            name="t",
            filter_json={"conditions": [{"field": "close", "operator": "gt", "value": 0}]},
            scope={"type": "symbols", "symbols": ["2330"]},
        )


def test_active_signal_out_tolerates_legacy_symbols_scope():
    """磁碟 / 匯入檔可能還有舊版 symbols scope 的 row — 載入不可炸,
    否則一筆舊規則就讓整批載入失敗。"""
    from models.condition import ActiveSignalOut
    out = ActiveSignalOut(
        id="x", created_at="2026-01-01T00:00:00Z",
        name="legacy",
        filter_json={"conditions": [{"field": "close", "operator": "gt", "value": 0}]},
        scope={"type": "symbols", "symbols": ["2330"]},
    )
    assert out.scope.type == "symbols"
