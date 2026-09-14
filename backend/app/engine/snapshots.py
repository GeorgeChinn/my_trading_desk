"""Intraday slot archive. Separate from data/csv daily bars.

Primary key is (trade_date, slot). Writing 15:30 never overwrites 13:30.
Unclosed prices stay here; scanners still read quotes.json + confirmed csv.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..config import EVENTS_DIR, LAST_SCAN_PATH, SCAN_CACHE_DIR, SNAPSHOTS_DIR, ensure_dirs
from ..store import load_quotes, load_watches, read_json, write_json
from .bars import ts_code
from .clock import MARKET_CLOSE, now_sh, parse_hhmm, session_trading_date
from .pool import is_st_name

INDEX_KEYS = ("sh000001", "sz399006", "sh000300")
STAT_KEYS = ("up_n", "down_n", "limit_up_n", "limit_down_n", "fail_n", "seal_rate", "max_board")
QUOTE_KEYS = (
    "code",
    "name",
    "open",
    "high",
    "low",
    "close",
    "pct",
    "volume",
    "amount",
    "turnover",
    "float_mcap_yi",
    "limit_up",
    "limit_down",
)
# Full A is preferred. Subset only if the compact map is unusually large.
_FULL_QUOTE_CAP = 6500
_INDEX_NAME = SNAPSHOTS_DIR / "_index.json"
_INDEX_KEEP = 60


def normalize_date(value) -> str:
    text = str(value or "").strip()[:10]
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        return text
    return ""


def normalize_slot(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) == 4 and text.isdigit():
        text = f"{text[:2]}:{text[2:]}"
    if ":" not in text:
        return ""
    hour_s, minute_s = text.split(":", 1)
    try:
        hour, minute = int(hour_s), int(minute_s[:2])
    except ValueError:
        return ""
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return ""
    return f"{hour:02d}:{minute:02d}"


def slot_file_stem(slot: str) -> str:
    stamp = normalize_slot(slot)
    return stamp.replace(":", "") if stamp else ""


def snapshot_path(trade_date: str, slot: str, root: Path | None = None) -> Path | None:
    day = normalize_date(trade_date)
    stem = slot_file_stem(slot)
    if not day or not stem:
        return None
    return (root or SNAPSHOTS_DIR) / day / f"{stem}.json"


def events_path(trade_date: str, root: Path | None = None) -> Path | None:
    day = normalize_date(trade_date)
    if not day:
        return None
    return (root or EVENTS_DIR) / f"{day}.json"


def _num(value):
    if value is None or value == "":
        return None
    try:
        val = float(value)
    except (TypeError, ValueError):
        return None
    if val != val:
        return None
    return val


def _round(value, ndigits=3):
    num = _num(value)
    if num is None:
        return None
    return round(num, ndigits)


def _limit_pct(code: str, name: str = "") -> float:
    if is_st_name(name):
        return 0.05
    c = ts_code(code)
    if c.startswith(("3", "68")):
        return 0.20
    if c.startswith(("8", "4")):
        return 0.30
    return 0.10


def _limit_prices(code: str, preclose, name: str = "") -> tuple:
    pre = _num(preclose)
    if not pre or pre <= 0:
        return None, None
    band = _limit_pct(code, name)
    return round(pre * (1.0 + band), 2), round(pre * (1.0 - band), 2)


def _compact_quote(rec: dict) -> dict:
    code = ts_code(str(rec.get("code") or rec.get("symbol") or ""))
    name = str(rec.get("name") or code)
    close = _num(rec.get("close") or rec.get("trade"))
    pre = _num(rec.get("preclose") or rec.get("settlement"))
    pct = _num(rec.get("pct") or rec.get("changepercent"))
    if pct is None and close and pre:
        pct = (close / pre - 1.0) * 100.0
    limit_up, limit_down = _limit_prices(code, pre, name)
    return {
        "code": code,
        "name": name,
        "open": _num(rec.get("open")),
        "high": _num(rec.get("high")),
        "low": _num(rec.get("low")),
        "close": close,
        "pct": _round(pct, 3),
        "volume": _num(rec.get("volume")),
        "amount": _num(rec.get("amount")),
        "turnover": _round(rec.get("turnover"), 3),
        "float_mcap_yi": _round(rec.get("float_mcap_yi"), 2),
        "limit_up": limit_up,
        "limit_down": limit_down,
    }


def _watch_codes() -> set[str]:
    codes: set[str] = set()
    try:
        for item in load_watches() or []:
            code = ts_code(str(item.get("code") or ""))
            if code:
                codes.add(code)
    except Exception:
        pass
    paths = []
    if LAST_SCAN_PATH.exists():
        paths.append(LAST_SCAN_PATH)
    if SCAN_CACHE_DIR.exists():
        paths.extend(SCAN_CACHE_DIR.glob("*.json"))
    keep_gates = {"观察", "试仓", "买入", "持有"}
    for path in paths:
        try:
            data = read_json(path, {})
        except Exception:
            continue
        rows = data.get("rows") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            gate = str(row.get("gate") or row.get("status") or "")
            if gate not in keep_gates:
                continue
            code = ts_code(str(row.get("code") or ""))
            if code:
                codes.add(code)
    return codes


def _subset_quotes(quotes: dict, events: dict) -> dict:
    keep = set(_watch_codes())
    for key in ("limit_up", "fail", "limit_down"):
        for rec in events.get(key) or []:
            if isinstance(rec, dict):
                code = ts_code(str(rec.get("code") or ""))
                if code:
                    keep.add(code)
    return {code: rec for code, rec in quotes.items() if code in keep}


def compute_stats(quotes: dict, zt: list | None = None, zb: list | None = None, dt: list | None = None) -> dict:
    """Counts from the full quote map. Pool lists override limit/fail/down when present."""
    up_n = down_n = 0
    lu_q = ld_q = fail_q = 0
    for rec in (quotes or {}).values():
        if not isinstance(rec, dict):
            continue
        code = ts_code(str(rec.get("code") or rec.get("symbol") or ""))
        name = str(rec.get("name") or "")
        close = _num(rec.get("close") or rec.get("trade"))
        pre = _num(rec.get("preclose") or rec.get("settlement"))
        high = _num(rec.get("high"))
        pct = _num(rec.get("pct") or rec.get("changepercent"))
        if pct is None and close and pre:
            pct = (close / pre - 1.0) * 100.0
        if pct is not None:
            if pct > 0:
                up_n += 1
            elif pct < 0:
                down_n += 1
        if close and pre and pre > 0:
            band = _limit_pct(code, name)
            chg = close / pre - 1.0
            if chg >= band - 0.005:
                lu_q += 1
            elif chg <= -band + 0.005:
                ld_q += 1
            elif high and (high / pre - 1.0) >= band - 0.005:
                fail_q += 1
    zt = [x for x in (zt or []) if isinstance(x, dict)]
    zb = [x for x in (zb or []) if isinstance(x, dict)]
    dt = [x for x in (dt or []) if isinstance(x, dict)]
    limit_up_n = len(zt) if zt else lu_q
    fail_n = len(zb) if zb else fail_q
    limit_down_n = len(dt) if dt else ld_q
    touched = limit_up_n + fail_n
    seal_rate = round(limit_up_n / touched * 100.0, 1) if touched else None
    max_board = 0
    for rec in zt:
        try:
            max_board = max(max_board, int(rec.get("lbc") or rec.get("height") or 0))
        except (TypeError, ValueError):
            continue
    return {
        "up_n": up_n,
        "down_n": down_n,
        "limit_up_n": limit_up_n,
        "limit_down_n": limit_down_n,
        "fail_n": fail_n,
        "seal_rate": seal_rate,
        "max_board": max_board,
    }


def empty_index() -> dict:
    blank = {"open": None, "high": None, "low": None, "close": None, "pct": None}
    return {key: dict(blank) for key in INDEX_KEYS}


def empty_stats() -> dict:
    return {key: None for key in STAT_KEYS}


def empty_snapshot(trade_date: str = "", slot: str = "") -> dict:
    day = normalize_date(trade_date)
    stamp = normalize_slot(slot)
    return {
        "ok": True,
        "found": False,
        "trade_date": day,
        "slot": stamp,
        "item": None,
        "quotes": {},
        "index": empty_index(),
        "stats": empty_stats(),
    }


def _blank_payload(trade_date: str, slot: str) -> dict:
    return {
        "trade_date": trade_date,
        "slot": slot,
        "captured_at": "",
        "market_closed": False,
        "quote_n": 0,
        "index": empty_index(),
        "stats": empty_stats(),
        "quotes": {},
    }


def load_snapshot(trade_date: str, slot: str, root: Path | None = None) -> dict | None:
    """Exact (date, slot) only. Missing slot returns None — never a later slot."""
    path = snapshot_path(trade_date, slot, root=root)
    if path is None or not path.exists() or not path.is_file():
        return None
    data = read_json(path, None)
    return data if isinstance(data, dict) else None


def load_events(trade_date: str, root: Path | None = None) -> dict | None:
    path = events_path(trade_date, root=root)
    if path is None or not path.exists() or not path.is_file():
        return None
    data = read_json(path, None)
    return data if isinstance(data, dict) else None


def write_snapshot_file(payload: dict, root: Path | None = None) -> Path | None:
    """Write one slot file. Does not touch other slots of the same day."""
    day = normalize_date(payload.get("trade_date"))
    slot = normalize_slot(payload.get("slot"))
    path = snapshot_path(day, slot, root=root)
    if path is None:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    out = dict(payload)
    out["trade_date"] = day
    out["slot"] = slot
    write_json(path, out)
    if root is None:
        _upsert_index(
            {
                "trade_date": day,
                "slot": slot,
                "captured_at": out.get("captured_at") or "",
                "quote_n": int(out.get("quote_n") or len(out.get("quotes") or {})),
                "market_closed": bool(out.get("market_closed")),
            }
        )
    return path


def write_events_file(payload: dict, root: Path | None = None) -> Path | None:
    day = normalize_date(payload.get("trade_date"))
    path = events_path(day, root=root)
    if path is None:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    out = dict(payload)
    out["trade_date"] = day
    write_json(path, out)
    return path


def _upsert_index(item: dict) -> None:
    ensure_dirs()
    data = read_json(_INDEX_NAME, {}) if _INDEX_NAME.exists() else {}
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        items = []
    day = item.get("trade_date")
    slot = item.get("slot")
    kept = [
        row
        for row in items
        if not (isinstance(row, dict) and row.get("trade_date") == day and row.get("slot") == slot)
    ]
    kept.append(dict(item))
    kept.sort(key=lambda row: (str(row.get("trade_date") or ""), str(row.get("slot") or "")), reverse=True)
    write_json(_INDEX_NAME, {"items": kept[:_INDEX_KEEP]})


def list_recent_snapshots(limit: int = 16, root: Path | None = None) -> list[dict]:
    base = root or SNAPSHOTS_DIR
    items: list[dict] = []
    if root is None and _INDEX_NAME.exists():
        data = read_json(_INDEX_NAME, {})
        raw = data.get("items") if isinstance(data, dict) else []
        for row in raw or []:
            if not isinstance(row, dict):
                continue
            path = snapshot_path(row.get("trade_date"), row.get("slot"))
            if path is None or not path.exists():
                continue
            items.append(
                {
                    "trade_date": row.get("trade_date"),
                    "slot": row.get("slot"),
                    "captured_at": row.get("captured_at") or "",
                    "quote_n": row.get("quote_n"),
                    "market_closed": bool(row.get("market_closed")),
                }
            )
        if items:
            return items[:limit]
    if not base.exists():
        return []
    days = [p for p in base.iterdir() if p.is_dir() and normalize_date(p.name)]
    days.sort(key=lambda p: p.name, reverse=True)
    for day_dir in days:
        files = [p for p in day_dir.glob("*.json") if p.stem.isdigit() and len(p.stem) == 4]
        files.sort(key=lambda p: p.stem, reverse=True)
        for path in files:
            slot = f"{path.stem[:2]}:{path.stem[2:]}"
            meta = {
                "trade_date": day_dir.name,
                "slot": slot,
                "captured_at": "",
                "quote_n": None,
                "market_closed": False,
            }
            try:
                data = read_json(path, {})
            except Exception:
                data = {}
            if isinstance(data, dict):
                meta["captured_at"] = data.get("captured_at") or ""
                meta["quote_n"] = data.get("quote_n")
                if meta["quote_n"] is None:
                    meta["quote_n"] = len(data.get("quotes") or {})
                meta["market_closed"] = bool(data.get("market_closed"))
            items.append(meta)
            if len(items) >= limit:
                return items
    return items[:limit]


def list_snapshots_for_date(trade_date: str, root: Path | None = None) -> list[dict]:
    day = normalize_date(trade_date)
    if not day:
        return []
    folder = (root or SNAPSHOTS_DIR) / day
    if not folder.exists():
        return []
    out = []
    files = [p for p in folder.glob("*.json") if p.stem.isdigit() and len(p.stem) == 4]
    files.sort(key=lambda p: p.stem)
    for path in files:
        slot = f"{path.stem[:2]}:{path.stem[2:]}"
        try:
            data = read_json(path, {})
        except Exception:
            data = {}
        quotes = data.get("quotes") if isinstance(data, dict) else {}
        quote_n = data.get("quote_n") if isinstance(data, dict) else None
        if quote_n is None:
            quote_n = len(quotes or {})
        out.append(
            {
                "trade_date": day,
                "slot": slot,
                "captured_at": (data or {}).get("captured_at") or "",
                "quote_n": quote_n,
                "market_closed": bool((data or {}).get("market_closed")),
            }
        )
    return out


def refresh_events(trade_date: str, slot: str = "", root: Path | None = None) -> dict:
    from .eastmoney import fetch_down_pool, fetch_fail_pool, fetch_limit_pool

    day = normalize_date(trade_date) or session_trading_date().isoformat()
    stamp = normalize_slot(slot)
    zt = zb = dt = []
    try:
        zt = fetch_limit_pool(day) or []
    except Exception:
        zt = []
    try:
        zb = fetch_fail_pool(day) or []
    except Exception:
        zb = []
    try:
        dt = fetch_down_pool(day) or []
    except Exception:
        dt = []
    payload = {
        "trade_date": day,
        "slot": stamp,
        "updated_at": now_sh().strftime("%Y-%m-%d %H:%M:%S"),
        "limit_up": zt,
        "fail": zb,
        "limit_down": dt,
    }
    write_events_file(payload, root=root)
    return payload


def archive_slot(trade_date: str | None = None, slot: str | None = None, root: Path | None = None) -> dict:
    """Capture quotes.json + EM pools into a slot file. Never writes data/csv."""
    ensure_dirs()
    when = now_sh()
    day = normalize_date(trade_date) or session_trading_date(when).isoformat()
    stamp = normalize_slot(slot) or when.strftime("%H:%M")
    captured_at = when.strftime("%Y-%m-%d %H:%M:%S")
    try:
        closed = parse_hhmm(stamp) >= MARKET_CLOSE
    except Exception:
        closed = False

    quotes_raw = load_quotes() or {}
    compact = {}
    for code, rec in quotes_raw.items():
        if not isinstance(rec, dict):
            continue
        row = dict(rec)
        row.setdefault("code", code)
        packed = _compact_quote(row)
        if packed.get("code"):
            compact[packed["code"]] = packed

    events = refresh_events(day, stamp, root=root)
    stats = compute_stats(
        quotes_raw,
        zt=events.get("limit_up"),
        zb=events.get("fail"),
        dt=events.get("limit_down"),
    )
    stored = compact
    if len(stored) > _FULL_QUOTE_CAP:
        stored = _subset_quotes(stored, events)

    index = empty_index()
    try:
        from .eastmoney import fetch_index_spot

        live = fetch_index_spot() or {}
        for key in INDEX_KEYS:
            row = live.get(key) if isinstance(live, dict) else None
            if isinstance(row, dict):
                index[key] = {
                    "open": _num(row.get("open")),
                    "high": _num(row.get("high")),
                    "low": _num(row.get("low")),
                    "close": _num(row.get("close")),
                    "pct": _round(row.get("pct"), 3),
                }
    except Exception:
        pass

    payload = {
        "trade_date": day,
        "slot": stamp,
        "captured_at": captured_at,
        "market_closed": bool(closed),
        "quote_n": len(stored),
        "index": index,
        "stats": stats,
        "quotes": stored,
    }
    write_snapshot_file(payload, root=root)
    return payload
