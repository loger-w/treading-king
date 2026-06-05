"""驗 ActiveFilter.strategy(discriminated union)+ strategy-only filter 合法。"""
import pytest

from models.condition import (
    ActiveFilter, BreakoutRetestStrategy, LimitUpOpenTouchStrategy,
)


def test_limit_up_strategy_only_filter_valid():
    f = ActiveFilter(strategy=LimitUpOpenTouchStrategy(type="limit_up_open_touch"))
    assert f.strategy.lock_seconds == 60          # 預設
    assert f.strategy.levels == ["ah", "nh", "cdp", "nl", "al"]
    assert f.conditions == []                      # strategy-only 允許 conditions 空


def test_breakout_strategy_defaults():
    f = ActiveFilter(strategy=BreakoutRetestStrategy(type="breakout_retest"))
    assert f.strategy.surge_pct == 3.0
    assert f.strategy.early_window_minutes == 10
    assert f.strategy.retest_within_minutes == 10


def test_discriminator_picks_right_model_from_dict():
    f = ActiveFilter.model_validate(
        {"strategy": {"type": "breakout_retest", "surge_pct": 5}}
    )
    assert isinstance(f.strategy, BreakoutRetestStrategy)
    assert f.strategy.surge_pct == 5.0


def test_schema_version_bumped_to_5():
    assert ActiveFilter(strategy=LimitUpOpenTouchStrategy(type="limit_up_open_touch")).schema_version == 5


def test_empty_filter_without_strategy_rejected():
    with pytest.raises(ValueError):
        ActiveFilter()   # 無 conditions / window / proximity / strategy
