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


def test_active_filter_schema_bumps_to_3():
    from models.condition import ActiveFilter
    f = ActiveFilter(conditions=[Condition(field="close", operator="gt", value=100)])
    assert f.schema_version == 3
    assert f.ma_proximity is None


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
