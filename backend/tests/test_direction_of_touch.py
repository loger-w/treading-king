"""驗 _direction_of_touch — 比較 prev / curr 跟 threshold 判方向。"""
from services.ring_buffer import Tick
from services.signal_engine import SignalEngine


def _tick(p: float) -> Tick:
    return Tick(price=p, size=1, time=0.0)


def test_from_below_when_prev_lower_curr_higher():
    eng = SignalEngine()
    assert eng._direction_of_touch(_tick(99), _tick(101), 100) == "from_below"


def test_from_above_when_prev_higher_curr_lower():
    eng = SignalEngine()
    assert eng._direction_of_touch(_tick(101), _tick(99), 100) == "from_above"


def test_from_below_when_curr_equals_threshold():
    """剛好打到也算 from_below(prev < threshold <= curr)。"""
    eng = SignalEngine()
    assert eng._direction_of_touch(_tick(99), _tick(100), 100) == "from_below"


def test_horizontal_when_prev_none():
    eng = SignalEngine()
    assert eng._direction_of_touch(None, _tick(100), 100) == "horizontal"


def test_horizontal_when_both_sides_of_same_side():
    """prev 跟 curr 都在 threshold 同側,沒跨越。"""
    eng = SignalEngine()
    assert eng._direction_of_touch(_tick(99.5), _tick(99.8), 100) == "horizontal"
