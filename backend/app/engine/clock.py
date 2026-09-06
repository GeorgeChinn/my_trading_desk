"""A-share calendar helpers. Confirmed daily bars are after the 15:00 close."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
# 15:00 收盘，日线大约 15:10–15:30 才齐。15:40 第一趟稳，16:30 补失败。
SYNC_TIMES = ("15:40", "16:30")


def now_sh() -> datetime:
    return datetime.now(SHANGHAI)


def parse_hhmm(text: str) -> tuple[int, int]:
    hour, minute = text.split(":")
    return int(hour), int(minute)


def previous_weekday(day):
    cursor = day
    while cursor.weekday() >= 5:
        cursor -= timedelta(days=1)
    return cursor


def parse_day(value):
    if value is None or value == "":
        return None
    if hasattr(value, "hour"):
        return value.date()
    if hasattr(value, "weekday") and hasattr(value, "year") and not hasattr(value, "hour"):
        return value
    text = str(value).strip()[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def is_weekend_date(value) -> bool:
    day = parse_day(value)
    return bool(day is not None and day.weekday() >= 5)


def expected_close_date(when: datetime | None = None):
    """Last session whose daily bar should already be treated as confirmed. Never Sat/Sun."""
    when = when or now_sh()
    day = when.date()
    if day.weekday() >= 5:
        return previous_weekday(day)
    first_h, first_m = parse_hhmm(SYNC_TIMES[0])
    if (when.hour, when.minute) < (first_h, first_m):
        return previous_weekday(day - timedelta(days=1))
    return day


def session_date(value=None):
    """Weekday session on or before value. Clamped to expected_close_date(). Never Sat/Sun."""
    expect = expected_close_date()
    day = parse_day(value) if value not in (None, "") else expect
    if day is None:
        return expect
    if day.weekday() >= 5:
        day = previous_weekday(day)
    if day > expect:
        return expect
    return day


def asof_date(stored: str | None = None) -> str:
    return session_date(stored).isoformat()


def next_fire_time(when: datetime | None = None, times: tuple[str, ...] = SYNC_TIMES) -> datetime:
    when = when or now_sh()
    cursor = when.replace(second=0, microsecond=0)
    for _ in range(14):
        if cursor.weekday() < 5:
            for stamp in times:
                hour, minute = parse_hhmm(stamp)
                candidate = cursor.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if candidate > when:
                    return candidate
        cursor = (cursor + timedelta(days=1)).replace(hour=0, minute=0)
    return when + timedelta(days=1)


def should_fire(when: datetime | None = None, times: tuple[str, ...] = SYNC_TIMES, last_fired: str = "") -> str | None:
    """Return 'HH:MM' if this minute is a fire slot and not already used."""
    when = when or now_sh()
    if when.weekday() >= 5:
        return None
    stamp = when.strftime("%H:%M")
    if stamp not in times:
        return None
    key = when.strftime("%Y-%m-%d ") + stamp
    if last_fired == key:
        return None
    return key
