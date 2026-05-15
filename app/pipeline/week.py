from __future__ import annotations

from datetime import UTC, date, datetime, timedelta


DAY_ORDER = {
    "MONDAY": 0,
    "TUESDAY": 1,
    "WEDNESDAY": 2,
    "THURSDAY": 3,
    "FRIDAY": 4,
    "SATURDAY": 5,
    "SUNDAY": 6,
}


def iso_week_key(target: date) -> str:
    year, week, _ = target.isocalendar()
    return f"{year}-W{week:02d}"


def next_week_key(now: datetime | None = None) -> str:
    current = now or datetime.now(UTC)
    return iso_week_key((current + timedelta(days=7)).date())


def is_last_enabled_upload_day(upload_days: list[str], now: datetime | None = None) -> bool:
    if not upload_days:
        return False
    current = now or datetime.now(UTC)
    enabled_indexes = [DAY_ORDER[day] for day in upload_days]
    return current.weekday() == max(enabled_indexes)
