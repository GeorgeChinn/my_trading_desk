"""In-process weekday closer. Does not rewrite confirmed bars during the session."""
from __future__ import annotations

import threading

from ..store import load_settings, save_settings
from .clock import configured_times, half_hour_slots, next_fire_time, normalize_times, should_fire
from .live import sync_live

_stop = threading.Event()
_thread: threading.Thread | None = None
_lock = threading.Lock()


def schedule_snapshot() -> dict:
    settings = load_settings()
    enabled = bool(settings.get("schedule_enabled", True))
    times = normalize_times(settings.get("schedule_times"))
    nxt = next_fire_time(times=times)
    shown = " / ".join(times) if times else "—"
    return {
        "enabled": enabled,
        "times": list(times),
        "slots": half_hour_slots(),
        "timezone": "Asia/Shanghai",
        "next_run": nxt.strftime("%Y-%m-%d %H:%M") if enabled else "",
        "last_fired": settings.get("schedule_last_fired") or "",
        "why": f"按你勾的更新时间点拉数并重扫。当前：{shown}（北京时间，工作日）。周六日不跑。",
    }


def _loop() -> None:
    while not _stop.is_set():
        settings = load_settings()
        if not settings.get("schedule_enabled", True):
            _stop.wait(20)
            continue
        times = normalize_times(settings.get("schedule_times"))
        key = should_fire(last_fired=settings.get("schedule_last_fired") or "", times=times)
        if key:
            save_settings({"schedule_last_fired": key})
            try:
                sync_live(force_bars=False)
                from ..jobs import run_rules_scan
                from ..store import load_universe
                from .cycles import cycles_page
                from .emotions import build_emotions
                from .rules_bind import parse_flags
                from .rulesets import get_ruleset

                run_rules_scan()
                try:
                    build_emotions(force=True)
                except Exception:
                    pass
                rs = get_ruleset("rules")
                if rs and rs.get("engine_ok"):
                    cycles_page(load_universe(), parse_flags(rs["text"]), rs, warm=True)
            except Exception:
                pass
        _stop.wait(20)


def start_scheduler() -> None:
    global _thread
    with _lock:
        if _thread and _thread.is_alive():
            return
        _stop.clear()
        _thread = threading.Thread(target=_loop, name="close-sync", daemon=True)
        _thread.start()


def stop_scheduler() -> None:
    _stop.set()
