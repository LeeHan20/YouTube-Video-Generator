from datetime import UTC, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_now() -> str:
    return utc_now().isoformat()


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO datetime string or the KST display format 'YYYY-MM-DD\\nHH:MM'."""
    if not value:
        return None
    # Handle KST display format: "2026-06-03\n20:00"
    if "\n" in value:
        parts = value.split("\n", 1)
        normalized = f"{parts[0].strip()}T{parts[1].strip()}:00+09:00"
        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def lock_expiry(minutes: int = 30) -> str:
    return (utc_now() + timedelta(minutes=minutes)).isoformat()
