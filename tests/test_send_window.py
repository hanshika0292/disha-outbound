"""Pure-Python tests for the send-window checker. No DB."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from disha.sequencer.timing import check_send_window

IN_WINDOW = {
    "timezone": "Asia/Kolkata",
    "allowed_days": ["Mon", "Tue", "Wed", "Thu", "Fri"],
    "start_hour": 10,
    "end_hour": 18,
}


class TestSendWindow:
    def test_allowed_within_window(self) -> None:
        # Wed 2026-05-27 14:00 IST = 08:30 UTC
        now = datetime(2026, 5, 27, 8, 30, tzinfo=ZoneInfo("UTC"))
        result = check_send_window(now, IN_WINDOW, [])
        assert result.allowed
        assert result.reason == "ok"
        assert result.defer_until is None

    def test_blocked_outside_hours_before_start(self) -> None:
        # Wed 09:00 IST = 03:30 UTC, before start_hour=10
        now = datetime(2026, 5, 27, 3, 30, tzinfo=ZoneInfo("UTC"))
        result = check_send_window(now, IN_WINDOW, [])
        assert not result.allowed
        assert result.reason == "outside_hours"
        # Should defer to same day at 10:00 IST = 04:30 UTC
        assert result.defer_until is not None
        local = result.defer_until.astimezone(ZoneInfo("Asia/Kolkata"))
        assert local.hour == 10
        assert local.date() == datetime(2026, 5, 27).date()

    def test_blocked_outside_hours_after_end(self) -> None:
        # Wed 19:00 IST = 13:30 UTC, past end_hour=18 → next allowed day
        now = datetime(2026, 5, 27, 13, 30, tzinfo=ZoneInfo("UTC"))
        result = check_send_window(now, IN_WINDOW, [])
        assert not result.allowed
        assert result.reason == "outside_hours"
        local = result.defer_until.astimezone(ZoneInfo("Asia/Kolkata"))  # type: ignore[union-attr]
        assert local.hour == 10
        assert local.weekday() == 3  # Thu

    def test_blocked_wrong_day(self) -> None:
        # Sun 2026-05-31 14:00 IST → defer to Mon 10:00 IST
        now = datetime(2026, 5, 31, 8, 30, tzinfo=ZoneInfo("UTC"))
        result = check_send_window(now, IN_WINDOW, [])
        assert not result.allowed
        assert result.reason == "wrong_day"
        local = result.defer_until.astimezone(ZoneInfo("Asia/Kolkata"))  # type: ignore[union-attr]
        assert local.weekday() == 0  # Mon
        assert local.hour == 10

    def test_blocked_festival(self) -> None:
        # Diwali 2026 = 2026-11-03 to 2026-11-09. Thu 2026-11-05 14:00 IST.
        now = datetime(2026, 11, 5, 8, 30, tzinfo=ZoneInfo("UTC"))
        result = check_send_window(now, IN_WINDOW, ["diwali_week"])
        assert not result.allowed
        assert result.reason == "festival_block"
        # Defer to day after block (2026-11-10, Tue) at start_hour
        local = result.defer_until.astimezone(ZoneInfo("Asia/Kolkata"))  # type: ignore[union-attr]
        assert local.date() == datetime(2026, 11, 10).date()
        assert local.hour == 10

    def test_year_end_block_spans_calendar_years(self) -> None:
        # year_end_block 2026: 2026-12-22 to 2027-01-02. Pick 2026-12-30.
        now = datetime(2026, 12, 30, 8, 30, tzinfo=ZoneInfo("UTC"))
        result = check_send_window(now, IN_WINDOW, ["year_end_block"])
        assert not result.allowed
        assert result.reason == "festival_block"
        local = result.defer_until.astimezone(ZoneInfo("Asia/Kolkata"))  # type: ignore[union-attr]
        # Day after block end = 2027-01-03 (Sun) → next allowed weekday = Mon 2027-01-04
        assert local.date() == datetime(2027, 1, 4).date()

    def test_year_end_block_inside_january_window(self) -> None:
        # 2027-01-01 still inside the year_end_block defined under year=2026
        now = datetime(2027, 1, 1, 8, 30, tzinfo=ZoneInfo("UTC"))
        result = check_send_window(now, IN_WINDOW, ["year_end_block"])
        assert not result.allowed
        assert result.reason == "festival_block"

    def test_unknown_festival_block_treated_as_inactive(self) -> None:
        # An unknown block name should not raise; behavior == no block active.
        now = datetime(2026, 5, 27, 8, 30, tzinfo=ZoneInfo("UTC"))
        result = check_send_window(now, IN_WINDOW, ["nonexistent_block"])
        assert result.allowed

    def test_raises_on_naive_datetime(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            check_send_window(datetime(2026, 5, 27, 14, 0), IN_WINDOW, [])
