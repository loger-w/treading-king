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


def test_schema_version_bumped_to_8():
    assert ActiveFilter(strategy=LimitUpOpenTouchStrategy(type="limit_up_open_touch")).schema_version == 8


def test_peak_divergence_strategy_defaults():
    from models.condition import PeakDivergenceStrategy
    f = ActiveFilter(strategy=PeakDivergenceStrategy(type="peak_divergence"))
    assert f.strategy.pullback_pct == 1.0
    assert f.strategy.not_exceed_tolerance_pct == 0.0
    assert f.strategy.volume_shrink_ratio == 0.8
    assert f.strategy.max_gap_minutes == 120
    assert f.strategy.min_main_peak_volume_ratio is None
    assert f.conditions == []                  # strategy-only 允許 conditions 空


def test_peak_divergence_discriminator_from_dict():
    from models.condition import PeakDivergenceStrategy
    f = ActiveFilter.model_validate(
        {"strategy": {"type": "peak_divergence", "pullback_pct": 1.5}}
    )
    assert isinstance(f.strategy, PeakDivergenceStrategy)
    assert f.strategy.pullback_pct == 1.5


def test_empty_filter_without_strategy_rejected():
    with pytest.raises(ValueError):
        ActiveFilter()   # 無 conditions / window / proximity / strategy
