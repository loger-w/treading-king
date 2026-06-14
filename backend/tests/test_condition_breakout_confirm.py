"""驗 BreakoutConfirmStrategy schema 驗證 + ActiveFilter 整合。"""
import pytest
from models.condition import ActiveFilter, BreakoutConfirmStrategy


def test_valid_breakout_confirm_strategy():
    s = BreakoutConfirmStrategy(type="cdp_breakout_confirm")
    assert s.levels == ["ah", "nh", "nl", "al"]
    assert s.direction == "both"
    assert s.confirm_bars == 2
    assert s.margin_ticks == 0
    assert s.min_volume_ratio is None


def test_confirm_bars_range():
    BreakoutConfirmStrategy(type="cdp_breakout_confirm", confirm_bars=1)
    BreakoutConfirmStrategy(type="cdp_breakout_confirm", confirm_bars=10)
    with pytest.raises(Exception):
        BreakoutConfirmStrategy(type="cdp_breakout_confirm", confirm_bars=0)
    with pytest.raises(Exception):
        BreakoutConfirmStrategy(type="cdp_breakout_confirm", confirm_bars=11)


def test_min_volume_ratio_range():
    BreakoutConfirmStrategy(type="cdp_breakout_confirm", min_volume_ratio=0.5)
    BreakoutConfirmStrategy(type="cdp_breakout_confirm", min_volume_ratio=20.0)
    with pytest.raises(Exception):
        BreakoutConfirmStrategy(type="cdp_breakout_confirm", min_volume_ratio=0.3)


def test_active_filter_with_breakout_confirm():
    f = ActiveFilter(strategy=BreakoutConfirmStrategy(type="cdp_breakout_confirm"))
    assert f.schema_version == 7
    assert f.strategy.type == "cdp_breakout_confirm"


def test_active_filter_discriminator_routes_correctly():
    """三種 strategy type 都能正確 parse。"""
    for stype in ("limit_up_open_touch", "breakout_retest", "cdp_breakout_confirm"):
        data = {"strategy": {"type": stype}}
        if stype == "breakout_retest":
            data["strategy"]["surge_pct"] = 3.0
        f = ActiveFilter(**data)
        assert f.strategy.type == stype
