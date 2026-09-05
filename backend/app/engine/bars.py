from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..config import CSV_DIR, ensure_dirs
from .indicators import kdj, macd_7428, sma


def ts_code(code: str) -> str:
    raw = code.strip().upper().replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
    if raw.startswith("SH") or raw.startswith("SZ"):
        raw = raw[2:]
    return raw.zfill(6) if raw.isdigit() else raw


def suffix_for(code: str) -> str:
    c = ts_code(code)
    if c.startswith(("6", "9")):
        return "SH"
    if c.startswith(("0", "2", "3")):
        return "SZ"
    if c.startswith(("4", "8")):
        return "BJ"
    return "SH"


def parse_date(text: str) -> str:
    raw = text.strip().replace("/", "-")
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    datetime.strptime(raw, "%Y-%m-%d")
    return raw


def csv_path_for(code: str) -> Path:
    return CSV_DIR / f"{ts_code(code)}.csv"


def peek_last_bar(code: str) -> dict | None:
    """只读表头 + 最后一行，避免为筛池把整份 CSV 读进内存。"""
    path = csv_path_for(code)
    if not path.exists():
        return None
    with path.open("rb") as handle:
        header_raw = handle.readline()
        handle.seek(0, 2)
        size = handle.tell()
        if size <= len(header_raw):
            return None
        handle.seek(max(len(header_raw), size - 720))
        tail = handle.read().decode("utf-8", errors="ignore").strip().splitlines()
    header = header_raw.decode("utf-8-sig", errors="ignore").strip()
    if not tail:
        return None
    last_line = tail[-1]
    if last_line.strip() == header.strip():
        return None
    try:
        fields = next(csv.reader([header]))
        vals = next(csv.reader([last_line]))
    except Exception:
        return None
    rec = {}
    for i, name in enumerate(fields):
        rec[(name or "").strip().lower()] = vals[i].strip() if i < len(vals) else ""
    try:
        date = parse_date(str(rec.get("date") or ""))
        c = float(rec.get("close") or 0)
    except (TypeError, ValueError):
        return None
    try:
        volume = float(rec.get("volume") or 0)
    except ValueError:
        volume = 0.0
    try:
        amount = float(rec.get("amount") or 0)
    except ValueError:
        amount = 0.0
    if amount <= 0:
        amount = c * volume
    return {
        "code": ts_code(code),
        "date": date,
        "close": c,
        "volume": volume,
        "amount": amount,
        "name": str(rec.get("name") or "").strip(),
    }


def list_csv_files() -> list[dict]:
    ensure_dirs()
    rows = []
    for path in sorted(CSV_DIR.glob("*.csv")):
        rows.append(
            {
                "file": path.name,
                "code": path.stem,
                "bytes": path.stat().st_size,
            }
        )
    return rows


def _row_to_bar(code: str, fields: dict, row: dict) -> dict | None:
    try:
        date = parse_date(str(row[fields["date"]]))
        o = float(row[fields["open"]])
        h = float(row[fields["high"]])
        l = float(row[fields["low"]])
        c = float(row[fields["close"]])
    except (KeyError, TypeError, ValueError):
        return None
    volume = 0.0
    if "volume" in fields and row.get(fields["volume"]) not in (None, ""):
        try:
            volume = float(row[fields["volume"]])
        except ValueError:
            volume = 0.0
    amount = None
    if "amount" in fields and row.get(fields["amount"]) not in (None, ""):
        try:
            amount = float(row[fields["amount"]])
        except ValueError:
            amount = None
    if amount is None:
        amount = c * volume
    stock_name = ""
    if "name" in fields and row.get(fields["name"]) not in (None, ""):
        stock_name = str(row[fields["name"]]).strip()
    return {
        "code": ts_code(code),
        "date": date,
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": volume,
        "amount": amount,
        "name": stock_name,
    }


def _fill_names(bars: list[dict]) -> list[dict]:
    last_name = ""
    for row in reversed(bars):
        if row.get("name"):
            last_name = row["name"]
            break
    if last_name:
        for row in bars:
            if not row.get("name"):
                row["name"] = last_name
    return bars


def _load_bars_tail(code: str, last_n: int) -> list[dict] | None:
    path = csv_path_for(code)
    if not path.exists():
        return []
    with path.open("rb") as handle:
        header_raw = handle.readline()
        handle.seek(0, 2)
        size = handle.tell()
        need = max(2048, (last_n + 12) * 140)
        handle.seek(max(len(header_raw), size - need))
        chunk = handle.read()
    header = header_raw.decode("utf-8-sig", errors="ignore").strip()
    text = chunk.decode("utf-8", errors="ignore")
    lines = text.splitlines()
    if size > need and lines:
        lines = lines[1:]
    body = "\n".join(lines)
    try:
        reader = csv.DictReader([header] + body.splitlines())
    except Exception:
        return None
    if not reader.fieldnames:
        return None
    fields = {name.strip().lower(): name for name in reader.fieldnames if name}
    if not all(key in fields for key in ("date", "open", "high", "low", "close")):
        return None
    bars = []
    for row in reader:
        item = _row_to_bar(code, fields, row)
        if item:
            bars.append(item)
    bars.sort(key=lambda item: item["date"])
    if len(bars) < min(last_n, 8) and size > need:
        return None
    bars = _fill_names(bars)
    return bars[-last_n:] if len(bars) > last_n else bars


def load_bars(code: str, last_n: int | None = None) -> list[dict]:
    if last_n and last_n > 0:
        tail = _load_bars_tail(code, last_n)
        if tail is not None:
            return tail
    path = csv_path_for(code)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            return []
        fields = {name.strip().lower(): name for name in reader.fieldnames if name}
        required = ("date", "open", "high", "low", "close")
        if not all(key in fields for key in required):
            return []
        bars: list[dict] = []
        for row in reader:
            try:
                date = parse_date(str(row[fields["date"]]))
                o = float(row[fields["open"]])
                h = float(row[fields["high"]])
                l = float(row[fields["low"]])
                c = float(row[fields["close"]])
            except (KeyError, TypeError, ValueError):
                continue
            volume = 0.0
            if "volume" in fields and row.get(fields["volume"]) not in (None, ""):
                try:
                    volume = float(row[fields["volume"]])
                except ValueError:
                    volume = 0.0
            amount = None
            if "amount" in fields and row.get(fields["amount"]) not in (None, ""):
                try:
                    amount = float(row[fields["amount"]])
                except ValueError:
                    amount = None
            if amount is None:
                amount = c * volume
            stock_name = ""
            if "name" in fields and row.get(fields["name"]) not in (None, ""):
                stock_name = str(row[fields["name"]]).strip()
            bars.append(
                {
                    "code": ts_code(code),
                    "date": date,
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": c,
                    "volume": volume,
                    "amount": amount,
                    "name": stock_name,
                }
            )
    bars.sort(key=lambda item: item["date"])
    last_name = ""
    for row in reversed(bars):
        if row.get("name"):
            last_name = row["name"]
            break
    if last_name:
        for row in bars:
            if not row.get("name"):
                row["name"] = last_name
    if last_n and last_n > 0 and len(bars) > last_n:
        return bars[-last_n:]
    return bars


def attach_indicators(bars: list[dict]) -> list[dict]:
    close = [row["close"] for row in bars]
    high = [row["high"] for row in bars]
    low = [row["low"] for row in bars]
    macd = macd_7428(close)
    osc = kdj(high, low, close)
    ma5 = sma(close, 5)
    ma10 = sma(close, 10)
    ma20 = sma(close, 20)
    out = []
    for i, row in enumerate(bars):
        item = dict(row)
        item["ma5"] = ma5[i]
        item["ma10"] = ma10[i]
        item["ma20"] = ma20[i]
        item["dif"] = macd["dif"][i]
        item["dea"] = macd["dea"][i]
        item["hist"] = macd["hist"][i]
        item["k"] = osc["k"][i]
        item["d"] = osc["d"][i]
        item["j"] = osc["j"][i]
        out.append(item)
    return out


def last_confirmed(bars: list[dict]) -> Optional[dict]:
    return bars[-1] if bars else None


def merge_bars(existing: list[dict], incoming: list[dict]) -> list[dict]:
    """Keep already-confirmed dates; only fill missing dates from incoming."""
    by_date: dict[str, dict] = {}
    for row in existing:
        date = str(row.get("date") or "")[:10]
        if date:
            by_date[date] = dict(row)
    for row in incoming:
        date = str(row.get("date") or "")[:10]
        if not date or date in by_date:
            continue
        by_date[date] = dict(row)
    out = list(by_date.values())
    out.sort(key=lambda item: item.get("date") or "")
    return out


def save_bars_csv(code: str, rows: list[dict], name: str | None = None) -> Path:
    ensure_dirs()
    path = csv_path_for(code)
    fieldnames = ["code", "date", "open", "high", "low", "close", "volume", "amount"]
    if name:
        fieldnames.append("name")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            payload = {key: row.get(key, "") for key in fieldnames}
            payload["code"] = ts_code(code)
            if name:
                payload["name"] = name
            writer.writerow(payload)
    return path
