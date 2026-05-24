from __future__ import annotations
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from services.fubon_futures_ws import target_after_hours_flag

TPE = ZoneInfo("Asia/Taipei")


def dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=TPE)


@pytest.mark.parametrize(
    "iso,expected",
    [
        ("2026-05-25T10:00:00", False),  # day → afterHours=False
        ("2026-05-25T22:00:00", True),   # night → afterHours=True
        ("2026-05-26T03:00:00", True),   # 跨日夜盤
        ("2026-05-25T14:00:00", None),   # 休市 → 不訂閱
        ("2026-05-30T10:00:00", None),   # 週六 → 不訂閱
        ("2026-05-25T13:45:00", None),   # 收盤瞬間 → 不訂閱
        ("2026-05-25T08:44:00", None),   # 開盤前休市 → 不訂閱
    ],
)
def test_target_after_hours_flag(iso: str, expected):
    assert target_after_hours_flag(dt(iso)) == expected
