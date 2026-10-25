"""Multi-range send-window coverage. No DB."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from disha.sequencer.timing import check_send_window

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")

IN_MULTI = {
    "timezone": "Asia/Kolkata",
    "allowed_days": ["Mon", "Tue", "Wed", "Thu", "Fri"],
    "hours": [[10, 12], [15, 18]],
}


def _at_ist(day: int, hour: int) -> datetime:
    return datetime(2026, 5, day, hour, 0, tzinfo=IST).astimezone(UTC)


def test_within_first_window_allowed() -> None:
    result = check_send_window(_at_ist(27, 11), IN_MULTI, [])
    assert result.allowed
    assert result.reason == "ok"


def test_within_second_window_allowed() -> None:
    result = check_send_window(_at_ist(27, 16), IN_MULTI, [])
    assert result.allowed


def test_between_windows_defers_to_second_window() -> None:
    # 13:00 IST is between [10, 12) and [15, 18) — defer to 15:00 IST same day
    result = check_send_window(_at_ist(27, 13), IN_MULTI, [])
    assert not result.allowed
    assert result.reason == "outside_hours"
    local = result.defer_until.astimezone(IST)  # type: ignore[union-attr]
    assert local.date() == _at_ist(27, 13).astimezone(IST).date()
    assert local.hour == 15


def test_after_both_windows_defers_to_next_day_first() -> None:
    # 19:00 IST Wed is past 18 → defer to Thu 10:00 IST
    result = check_send_window(_at_ist(27, 19), IN_MULTI, [])
    assert not result.allowed
    local = result.defer_until.astimezone(IST)  # type: ignore[union-attr]
    assert local.weekday() == 3  # Thu
    assert local.hour == 10


def test_before_first_window_defers_same_day() -> None:
    result = check_send_window(_at_ist(27, 9), IN_MULTI, [])
    assert not result.allowed
    local = result.defer_until.astimezone(IST)  # type: ignore[union-attr]
    assert local.hour == 10
    assert local.date() == _at_ist(27, 9).astimezone(IST).date()


def test_legacy_single_range_format_still_works() -> None:
    # Backward compat: {start_hour, end_hour} works alongside {hours: [...]}
    legacy = {
        "timezone": "Asia/Kolkata",
        "allowed_days": ["Mon", "Tue", "Wed", "Thu", "Fri"],
        "start_hour": 10,
        "end_hour": 18,
    }
    assert check_send_window(_at_ist(27, 11), legacy, []).allowed
    assert check_send_window(_at_ist(27, 13), legacy, []).allowed  # legacy treats 13 as in [10,18)
    assert not check_send_window(_at_ist(27, 9), legacy, []).allowed
