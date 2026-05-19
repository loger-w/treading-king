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
