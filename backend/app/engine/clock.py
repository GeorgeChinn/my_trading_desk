"""A-share calendar helpers. Update times come from 数据与设置."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
# 默认两档：收盘附近、傍晚补失败。用户可在设置里改成任意半点。
SYNC_TIMES = ("15:30", "16:30")
SLOT_MINUTES = (0, 30)
# A 股连续竞价收盘。未到此时点的 K 线不得写入 data/csv。
MARKET_CLOSE = (15, 0)


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


def session_trading_date(when: datetime | None = None):
    """Weekday session the latest quote snapshot belongs to. Not gated by 数据与设置 times."""
    when = when or now_sh()
    day = when.date()
    if day.weekday() >= 5:
        return previous_weekday(day)
    return day


def market_has_closed(when: datetime | None = None) -> bool:
    """True when today's A-share session is already closed (or it is the weekend)."""
    when = when or now_sh()
    if when.weekday() >= 5:
        return True
    return (when.hour, when.minute) >= MARKET_CLOSE


def confirmed_bar_date(when: datetime | None = None):
    """Last session whose daily bar may be written to data/csv. A-share close 15:00."""
    when = when or now_sh()
    day = when.date()
    if day.weekday() >= 5:
        return previous_weekday(day)
    if (when.hour, when.minute) < MARKET_CLOSE:
        return previous_weekday(day - timedelta(days=1))
    return day


def expected_close_date(when: datetime | None = None):
    """Latest-quote session date. RULES/RULES2/RULES4 follow this; CSV uses confirmed_bar_date()."""
    return session_trading_date(when)


def csv_write_slots(times: tuple[str, ...] | None = None) -> tuple[str, ...]:
    times = tuple(times) if times else configured_times()
    return tuple(stamp for stamp in times if parse_hhmm(stamp) >= MARKET_CLOSE)


def should_write_csv(when: datetime | None = None, times: tuple[str, ...] | None = None) -> bool:
    """Selected slot at/after 15:00 on a weekday writes official daily bars."""
    when = when or now_sh()
    if when.weekday() >= 5:
        return False
    times = tuple(times) if times else configured_times()
    stamp = when.strftime("%H:%M")
    return stamp in times and parse_hhmm(stamp) >= MARKET_CLOSE


def session_date(value=None):
    """Weekday session on or before value. Clamped to session_trading_date(). Never Sat/Sun."""
    expect = session_trading_date()
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
