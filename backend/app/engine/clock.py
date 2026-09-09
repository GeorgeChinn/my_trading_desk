"""A-share calendar helpers. Update times come from 数据与设置."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
# 默认两档：收盘附近、傍晚补失败。用户可在设置里改成任意半点。
SYNC_TIMES = ("15:30", "16:30")
SLOT_MINUTES = (0, 30)


def half_hour_slots() -> list[str]:
    return [f"{h:02d}:{m:02d}" for h in range(24) for m in SLOT_MINUTES]


def _snap_slot(text: str) -> str | None:
    try:
        hour, minute = parse_hhmm(text)
    except Exception:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    if minute < 15:
        minute = 0
    elif minute < 45:
        minute = 30
    else:
        hour = (hour + 1) % 24
        minute = 0
    return f"{hour:02d}:{minute:02d}"


def normalize_times(raw) -> tuple[str, ...]:
    slots = set(half_hour_slots())
    out = []
    for item in raw or []:
        text = _snap_slot(str(item or "").strip()[:5])
        if text in slots and text not in out:
            out.append(text)
    out.sort()
    return tuple(out) or SYNC_TIMES


def configured_times() -> tuple[str, ...]:
    try:
        from ..config import SETTINGS_PATH
        from ..store import read_json

        data = read_json(SETTINGS_PATH, {}) if SETTINGS_PATH.exists() else {}
        raw = data.get("schedule_times") if isinstance(data, dict) else None
    except Exception:
        raw = None
    return normalize_times(raw)


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
    """Last session whose bar should already be treated as current. Never Sat/Sun."""
    when = when or now_sh()
    day = when.date()
    if day.weekday() >= 5:
        return previous_weekday(day)
    times = configured_times()
    first_h, first_m = parse_hhmm(times[0])
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


def next_fire_time(when: datetime | None = None, times: tuple[str, ...] | None = None) -> datetime:
    when = when or now_sh()
    times = tuple(times) if times else configured_times()
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


def should_fire(when: datetime | None = None, times: tuple[str, ...] | None = None, last_fired: str = "") -> str | None:
    """Return 'YYYY-MM-DD HH:MM' if this minute is a fire slot and not already used."""
    when = when or now_sh()
    if when.weekday() >= 5:
        return None
    times = tuple(times) if times else configured_times()
    stamp = when.strftime("%H:%M")
    if stamp not in times:
        return None
    key = when.strftime("%Y-%m-%d ") + stamp
    if last_fired == key:
        return None
    return key
