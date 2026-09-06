from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .config import (
    IDEAS_PATH,
    JOURNALS_INDEX,
    JUDGEMENTS_PATH,
    POOL_SNAPSHOT_PATH,
    SETTINGS_PATH,
    SYNC_STATUS_PATH,
    TRADES_PATH,
    UNIVERSE_PATH,
    WATCHES_PATH,
    ensure_dirs,
)

DEFAULT_SETTINGS = {
    "person_present": True,
    "market_regime": "未设置",
    "tushare_token": "",
    "data_label": "尚未连接真实行情",
    "data_source": "",
    "last_trade_date": "",
    "schedule_enabled": True,
    "schedule_times": ["15:40", "16:30"],
    "schedule_last_fired": "",
}


def _atomic_write(path: Path, payload: Any) -> None:
    ensure_dirs()
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False, indent=2)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(raw)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: Any) -> None:
    _atomic_write(path, payload)


def load_universe() -> list[dict]:
    data = read_json(UNIVERSE_PATH, [])
    return data if isinstance(data, list) else []


def save_universe(items: list[dict]) -> None:
    write_json(UNIVERSE_PATH, items)


def load_pool_snapshot() -> dict:
    data = read_json(POOL_SNAPSHOT_PATH, {})
    return data if isinstance(data, dict) else {}


def save_pool_snapshot(payload: dict) -> None:
    write_json(POOL_SNAPSHOT_PATH, payload)


def load_sync_status() -> dict:
    data = read_json(SYNC_STATUS_PATH, {"state": "idle", "message": "尚未同步真实行情"})
    return data if isinstance(data, dict) else {"state": "idle"}


def save_sync_status(payload: dict) -> None:
    write_json(SYNC_STATUS_PATH, payload)


def load_watches() -> list[dict]:
    data = read_json(WATCHES_PATH, [])
    return data if isinstance(data, list) else []


def save_watches(items: list[dict]) -> None:
    write_json(WATCHES_PATH, items)


def load_trades() -> list[dict]:
    data = read_json(TRADES_PATH, [])
    return data if isinstance(data, list) else []


def save_trades(items: list[dict]) -> None:
    write_json(TRADES_PATH, items)


def load_ideas() -> list[dict]:
    data = read_json(IDEAS_PATH, [])
    return data if isinstance(data, list) else []


def save_ideas(items: list[dict]) -> None:
    write_json(IDEAS_PATH, items)


def load_settings() -> dict:
    data = read_json(SETTINGS_PATH, dict(DEFAULT_SETTINGS))
    merged = dict(DEFAULT_SETTINGS)
    if isinstance(data, dict):
        merged.update(data)
    from .engine.clock import asof_date

    coerced = asof_date(merged.get("last_trade_date") or "")
    if (merged.get("last_trade_date") or "") != coerced:
        merged["last_trade_date"] = coerced
        write_json(SETTINGS_PATH, merged)
    else:
        merged["last_trade_date"] = coerced
    return merged


def save_settings(payload: dict) -> dict:
    current = load_settings()
    current.update(payload)
    if "last_trade_date" in current:
        from .engine.clock import asof_date

        current["last_trade_date"] = asof_date(current.get("last_trade_date") or "")
    write_json(SETTINGS_PATH, current)
    return current


def load_judgements() -> list[dict]:
    data = read_json(JUDGEMENTS_PATH, [])
    return data if isinstance(data, list) else []


def save_judgements(items: list[dict]) -> None:
    write_json(JUDGEMENTS_PATH, items)
