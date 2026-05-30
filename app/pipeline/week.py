from __future__ import annotations

from datetime import UTC, date, datetime, timedelta


DAY_ORDER = {
    "MONDAY": 0,
    "월요일": 0,
    "월": 0,
    "TUESDAY": 1,
    "화요일": 1,
    "화": 1,
    "WEDNESDAY": 2,
    "수요일": 2,
    "수": 2,
    "THURSDAY": 3,
    "목요일": 3,
    "목": 3,
    "FRIDAY": 4,
    "금요일": 4,
    "금": 4,
    "SATURDAY": 5,
    "토요일": 5,
    "토": 5,
    "SUNDAY": 6,
    "일요일": 6,
    "일": 6,
}

DAY_LABELS = {
    "MONDAY": "월요일",
    "TUESDAY": "화요일",
    "WEDNESDAY": "수요일",
    "THURSDAY": "목요일",
    "FRIDAY": "금요일",
    "SATURDAY": "토요일",
    "SUNDAY": "일요일",
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
