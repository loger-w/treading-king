"""驗台股漲停價 = 昨收 × 1.1,尾數捨去(不超過 +10%),tick 以漲停價級距為準。"""
from services.cdp import limit_up_price


def test_round_number_aligned():
    # 100 × 1.1 = 110.0,110 在 100–500 級距(tick 0.5),已對齊
    assert limit_up_price(100.0) == 110.0


def test_truncates_down_not_exceeding_10pct():
    # 10.05 × 1.1 = 11.055;11 在 10–50 級距(tick 0.05)→ 捨去到 11.05(= +9.95%)
    # 若用四捨五入會變 11.06(> +10%)— 漲停價絕不可超過 +10%
    assert limit_up_price(10.05) == 11.05


def test_uses_limit_price_tick_band():
    # 49 × 1.1 = 53.9;53.9 在 50–100 級距(tick 0.1)→ 53.9
    assert limit_up_price(49.0) == 53.9


def test_high_price_5_dollar_tick():
    # 1000 × 1.1 = 1100;>= 1000 級距 tick 5.0 → 1100.0
    assert limit_up_price(1000.0) == 1100.0


def test_sub_10_one_cent_tick():
    # 5.0 × 1.1 = 5.5;< 10 級距 tick 0.01 → 5.5
    assert limit_up_price(5.0) == 5.5


def test_half_cent_tail_must_floor_not_preround():
    # ref×1.1 合法地帶 0.1 分(deci-cent)尾數:9.05×1.1=9.955。
    # 先 round 到整數分會把該被捨去的半分提前進位 → 9.96(+10.06%,超法定)。
    # 鎖漲停 latch 用此值比對成交價,算錯則該價位的漲停打開策略永不觸發。
    assert limit_up_price(9.05) == 9.95
    assert limit_up_price(5.55) == 6.10
    assert limit_up_price(10.45) == 11.45
