"""In-process weekday closer. Does not rewrite confirmed bars during the session."""
from __future__ import annotations

import threading

from ..store import load_settings, save_settings
from .clock import SYNC_TIMES, next_fire_time, should_fire
from .live import sync_live

_stop = threading.Event()
_thread: threading.Thread | None = None
_lock = threading.Lock()


def schedule_snapshot() -> dict:
    settings = load_settings()
    enabled = bool(settings.get("schedule_enabled", True))
    nxt = next_fire_time()
    return {
        "enabled": enabled,
        "times": list(settings.get("schedule_times") or SYNC_TIMES),
        "timezone": "Asia/Shanghai",
        "next_run": nxt.strftime("%Y-%m-%d %H:%M") if enabled else "",
        "last_fired": settings.get("schedule_last_fired") or "",
        "why": "A 股 15:00 收盘。日线大约 15:10–15:30 齐。15:40 拉确认收盘，16:30 补失败。周六日不跑。盘中不改写已确认收盘。",
    }


def _loop() -> None:
    while not _stop.is_set():
        settings = load_settings()
        if not settings.get("schedule_enabled", True):
            _stop.wait(20)
            continue
        key = should_fire(last_fired=settings.get("schedule_last_fired") or "")
        if key:
            save_settings({"schedule_last_fired": key})
            try:
                sync_live(force_bars=False)
                from ..jobs import run_rules_scan
                from ..store import load_universe
                from .cycles import cycles_page
                from .rules_bind import parse_flags
                from .rulesets import get_ruleset

                run_rules_scan()
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
