"""驗 MountainBounceStrategy model 解析。"""
from models.condition import ActiveFilter, MountainBounceStrategy


def test_mountain_bounce_defaults():
    s = MountainBounceStrategy(type="mountain_bounce")
    assert s.levels == ["ah", "nh", "cdp"]
    assert s.confirm_bars == 2
    assert s.tolerance_pct == 0.0
    assert s.require_below_vwap is True


def test_mountain_bounce_custom():
    s = MountainBounceStrategy(
        type="mountain_bounce", levels=["nh"], confirm_bars=3,
        tolerance_pct=0.3, require_below_vwap=True,
    )
    assert s.levels == ["nh"]
    assert s.confirm_bars == 3
    assert s.require_below_vwap is True


def test_mountain_bounce_in_active_filter():
    f = ActiveFilter(strategy=MountainBounceStrategy(type="mountain_bounce"))
    assert f.strategy.type == "mountain_bounce"
    assert f.schema_version == 8


def test_mountain_bounce_roundtrip_json():
    f = ActiveFilter(strategy=MountainBounceStrategy(type="mountain_bounce"))
    raw = f.model_dump_json()
    f2 = ActiveFilter.model_validate_json(raw)
    assert f2.strategy.type == "mountain_bounce"
    assert f2.strategy.levels == ["ah", "nh", "cdp"]
