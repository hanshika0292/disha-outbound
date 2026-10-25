"""Send-window and festival-block logic for the sequencer.

All inputs are timezone-aware UTC datetimes; all date arithmetic happens in the
send-window's local timezone via zoneinfo. The "festival_blocks" list refers
to keys in FESTIVAL_BLOCKS below — each maps a year to a (start, end) inclusive
ISO-date range. The year_end_block intentionally spans two calendar years, so
the resolver does date-range overlap (not start-year lookup).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel

FESTIVAL_BLOCKS: dict[str, dict[str, tuple[str, str]]] = {
    "diwali_week": {
        "2025": ("2025-10-17", "2025-10-23"),
        "2026": ("2026-11-03", "2026-11-09"),
    },
    "holi_week": {
        "2025": ("2025-03-10", "2025-03-16"),
        "2026": ("2026-03-02", "2026-03-08"),
    },
    "year_end_block": {
        "2025": ("2025-12-22", "2026-01-02"),
        "2026": ("2026-12-22", "2027-01-02"),
    },
    "thanksgiving_week": {
        "2025": ("2025-11-24", "2025-11-30"),
        "2026": ("2026-11-23", "2026-11-29"),
    },
    "july4_week": {
        "2025": ("2025-06-30", "2025-07-06"),
        "2026": ("2026-06-29", "2026-07-05"),
    },
}

# Cadence JSONs use lowercase ("mon") but the spec's check_send_window expects
# capitalized ("Mon"). We accept both and normalize.
DayName = Literal["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
WEEKDAY_MAP: dict[str, int] = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


class SendWindowResult(BaseModel):
    allowed: bool
    reason: str  # "ok" | "outside_hours" | "wrong_day" | "festival_block"
    defer_until: datetime | None = None


def _normalize_day(day: str) -> int:
    return WEEKDAY_MAP[day.strip().lower()]


def _date_in_range(d: date, start_iso: str, end_iso: str) -> bool:
    return date.fromisoformat(start_iso) <= d <= date.fromisoformat(end_iso)


def _active_block_end(local_date: date, festival_blocks: list[str]) -> date | None:
    """If local_date is inside any active festival block, return that block's end-date."""
    for block_name in festival_blocks:
        block = FESTIVAL_BLOCKS.get(block_name)
        if block is None:
            # Cadence references a name we don't know; treat as inactive but don't crash.
            continue
        # Date-range overlap across all known years for this block (year_end_block spans).
        for _year, (start_iso, end_iso) in block.items():
            if _date_in_range(local_date, start_iso, end_iso):
                return date.fromisoformat(end_iso)
    return None


def _next_allowed_day(from_date: date, allowed_weekday_indices: set[int]) -> date:
    """Step forward day-by-day (max 7) until weekday matches, inclusive of from_date."""
    for offset in range(8):
        candidate = from_date + timedelta(days=offset)
        if candidate.weekday() in allowed_weekday_indices:
            return candidate
    return from_date  # impossible if allowed_weekday_indices is non-empty


def _ranges_from_send_window(send_window: dict[str, Any]) -> list[tuple[int, int]]:
    """Normalize input to a sorted, non-overlapping list of (start_hour, end_hour) tuples.

    Accepts either:
      {"start_hour": 10, "end_hour": 18}              # single range (legacy)
      {"hours": [[10, 12], [15, 18]]}                 # multi-range list (new)
    """
    if "hours" in send_window:
        ranges = [(int(s), int(e)) for s, e in send_window["hours"]]
    else:
        ranges = [(int(send_window["start_hour"]), int(send_window["end_hour"]))]
    return sorted(ranges, key=lambda r: r[0])


def _hour_in_any_range(hour: int, ranges: list[tuple[int, int]]) -> bool:
    return any(s <= hour < e for s, e in ranges)


def _next_range_start_today(local_now: datetime, ranges: list[tuple[int, int]]) -> int | None:
    """Hour of the next range opening today; None if all ranges already past."""
    for s, _ in ranges:
        if local_now.hour < s:
            return s
    return None


def check_send_window(
    now_utc: datetime,
    send_window: dict[str, Any],
    festival_blocks: list[str],
) -> SendWindowResult:
    """Decide whether `now_utc` is inside the sender's allowed window.

    send_window shape (both supported):
      {"timezone": "Asia/Kolkata",
       "allowed_days": ["Mon","Tue","Wed","Thu","Fri"],
       "start_hour": 10, "end_hour": 18}                 # legacy single range

      {"timezone": "Asia/Kolkata",
       "allowed_days": ["mon","tue","wed","thu","fri"],
       "hours": [[10, 12], [15, 18]]}                    # multi-range

    Multi-range semantics: allowed if local_hour falls in ANY (start, end)
    half-open interval. defer_until picks the next opening across all ranges.
    """
    if now_utc.tzinfo is None:
        raise ValueError("now_utc must be timezone-aware")

    tz = ZoneInfo(send_window["timezone"])
    local_now = now_utc.astimezone(tz)
    local_date = local_now.date()

    allowed_indices = {_normalize_day(d) for d in send_window["allowed_days"]}
    ranges = _ranges_from_send_window(send_window)
    first_open_hour = ranges[0][0] if ranges else 9

    block_end = _active_block_end(local_date, festival_blocks)
    if block_end is not None:
        resume_date = block_end + timedelta(days=1)
        next_open_date = _next_allowed_day(resume_date, allowed_indices)
        defer_local = datetime.combine(next_open_date, datetime.min.time(), tzinfo=tz).replace(
            hour=first_open_hour
        )
        return SendWindowResult(
            allowed=False,
            reason="festival_block",
            defer_until=defer_local.astimezone(ZoneInfo("UTC")),
        )

    if local_now.weekday() not in allowed_indices:
        next_open_date = _next_allowed_day(local_date + timedelta(days=1), allowed_indices)
        defer_local = datetime.combine(next_open_date, datetime.min.time(), tzinfo=tz).replace(
            hour=first_open_hour
        )
        return SendWindowResult(
            allowed=False,
            reason="wrong_day",
            defer_until=defer_local.astimezone(ZoneInfo("UTC")),
        )

    if _hour_in_any_range(local_now.hour, ranges):
        return SendWindowResult(allowed=True, reason="ok", defer_until=None)

    # Outside all ranges today. Either before the first range, between two
    # ranges, or after all ranges. Find the next opening.
    next_hour_today = _next_range_start_today(local_now, ranges)
    if next_hour_today is not None:
        defer_local = local_now.replace(hour=next_hour_today, minute=0, second=0, microsecond=0)
    else:
        next_open_date = _next_allowed_day(local_date + timedelta(days=1), allowed_indices)
        defer_local = datetime.combine(next_open_date, datetime.min.time(), tzinfo=tz).replace(
            hour=first_open_hour
        )

    return SendWindowResult(
        allowed=False,
        reason="outside_hours",
        defer_until=defer_local.astimezone(ZoneInfo("UTC")),
    )


def send_window_from_cadence_config(cadence_config: dict[str, Any]) -> dict[str, Any]:
    """Bridge: convert the existing cadence JSON shape to check_send_window's dict.

    Existing seeds use:
      send_window_local_tz: str
      send_windows: {weekdays: [...], hours: [[start, end], ...]}

    Returns the multi-range form so check_send_window honors every window.
    """
    tz = cadence_config.get("send_window_local_tz", "UTC")
    sw = cadence_config.get("send_windows", {})
    allowed_days = list(sw.get("weekdays", ["mon", "tue", "wed", "thu", "fri"]))
    hours_raw = sw.get("hours", [[9, 17]])
    hours = [[int(s), int(e)] for s, e in hours_raw] if hours_raw else [[9, 17]]
    return {
        "timezone": tz,
        "allowed_days": allowed_days,
        "hours": hours,
    }
