"""Build local sample daily bars so the desk can be opened without Tushare.

Files on disk are treated as confirmed closes. This script writes sample CSVs
for first-run UI checks; README labels them as samples, not live quotes.
"""
from __future__ import annotations

import json
import random
from datetime import date, timedelta

from .config import CSV_DIR, DATA_DIR, UNIVERSE_PATH, ensure_dirs
from .engine.bars import load_bars, save_bars_csv
from .engine.watch import evaluate_condition
from .store import load_watches, save_watches

UNIVERSE = [
    {"code": "600519", "name": "贵州茅台", "float_mcap_yi": 18000, "is_st": False, "index_member": ["沪深300", "上证50", "沪股通"], "tags": [], "start": 1480.0},
    {"code": "600036", "name": "招商银行", "float_mcap_yi": 9000, "is_st": False, "index_member": ["沪深300", "上证50", "沪股通"], "tags": [], "start": 36.0},
    {"code": "601318", "name": "中国平安", "float_mcap_yi": 8000, "is_st": False, "index_member": ["沪深300", "上证50", "沪股通"], "tags": [], "start": 48.0},
    {"code": "600900", "name": "长江电力", "float_mcap_yi": 6200, "is_st": False, "index_member": ["沪深300", "上证50", "沪股通"], "tags": [], "start": 27.5},
    {"code": "000858", "name": "五粮液", "float_mcap_yi": 5500, "is_st": False, "index_member": ["沪深300", "沪股通"], "tags": [], "start": 128.0},
    {"code": "000333", "name": "美的集团", "float_mcap_yi": 4800, "is_st": False, "index_member": ["沪深300", "沪股通"], "tags": [], "start": 72.0},
    {"code": "601398", "name": "工商银行", "float_mcap_yi": 17000, "is_st": False, "index_member": ["沪深300", "上证50", "沪股通"], "tags": [], "start": 5.80},
    {"code": "600276", "name": "恒瑞医药", "float_mcap_yi": 3200, "is_st": False, "index_member": ["沪深300", "沪股通"], "tags": [], "start": 44.0},
    {"code": "600030", "name": "中信证券", "float_mcap_yi": 3100, "is_st": False, "index_member": ["沪深300", "上证50"], "tags": [], "start": 26.0},
    {"code": "300348", "name": "长亮科技", "float_mcap_yi": 80, "is_st": False, "index_member": [], "tags": ["小盘题材"], "start": 14.2},
    {"code": "600010", "name": "包钢股份", "float_mcap_yi": 700, "is_st": False, "index_member": [], "tags": [], "start": 1.72},
    {"code": "600745", "name": "闻泰科技", "float_mcap_yi": 420, "is_st": False, "index_member": [], "tags": ["连板"], "start": 32.0},
    {"code": "600081", "name": "东风科技", "float_mcap_yi": 40, "is_st": True, "index_member": [], "tags": [], "start": 8.4},
]


def trading_days(end: date, count: int) -> list[date]:
    days = []
    cursor = end
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    days.reverse()
    return days


def _ohlc(close: float, prev: float, rng: random.Random) -> tuple[float, float, float, float]:
    high = max(close, prev) * (1 + rng.uniform(0.002, 0.016))
    low = min(close, prev) * (1 - rng.uniform(0.002, 0.016))
    open_px = min(max(prev * (1 + rng.uniform(-0.01, 0.01)), low), high)
    close = min(max(close, low), high)
    return round(open_px, 2), round(high, 2), round(low, 2), round(close, 2)


def walk(start: float, days: list[date], rng: random.Random, drift: float, vol: float, min_px: float) -> list[dict]:
    price = start
    prev = start
    rows = []
    for day in days:
        price = max(min_px, price * (1 + rng.gauss(drift, vol)))
        o, h, l, c = _ohlc(price, prev, rng)
        price = c
        volume = rng.uniform(8e6, 4e7)
        amount = c * volume
        if min_px >= 5 and amount < 6e8:
            volume = 8e8 / max(c, 0.01)
            amount = c * volume
        rows.append(
            {
                "date": day.isoformat(),
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": round(volume, 0),
                "amount": round(amount, 2),
            }
        )
        prev = c
    return rows


def _touch_hl(row: dict) -> None:
    row["high"] = max(row["high"], row["open"], row["close"])
    row["low"] = min(row["low"], row["open"], row["close"])


def ensure_ma5_reclaim(code: str, name: str) -> bool:
    rows = load_bars(code)
    if len(rows) < 8:
        return False
    # Last 6 closes: four flat, one dip below MA5, one reclaim.
    base = rows[-6]["close"]
    factors = (1.00, 1.00, 1.00, 1.00, 0.96, 1.02)
    for offset, factor in enumerate(factors):
        row = rows[-6 + offset]
        row["close"] = round(base * factor, 2)
        row["open"] = round(base * (1.0 if offset < 4 else factor), 2)
        _touch_hl(row)
    save_bars_csv(code, rows, name=name)
    return bool(evaluate_condition(code, "ma5_reclaim")["triggered"])


def ensure_macd_green_shrink(code: str, name: str) -> bool:
    rows = load_bars(code)
    if len(rows) < 30:
        return False
    # Full-path series: slow rise, short pullback, then flatten.
    # Partial-window edits cannot overpower EMA(28) memory.
    price = 36.0
    bounce_n = 2
    drop_n = 10
    up_n = len(rows) - drop_n - bounce_n
    closes = []
    for _ in range(up_n):
        price *= 1.0015
        closes.append(price)
    for _ in range(drop_n):
        price *= 0.995
        closes.append(price)
    for _ in range(bounce_n):
        price *= 1.001
        closes.append(price)
    for row, close in zip(rows, closes):
        row["close"] = round(close, 2)
        row["open"] = round(close * 0.998, 2)
        _touch_hl(row)
        if (row["close"] * row["volume"]) < 6e8:
            row["volume"] = round(8e8 / max(row["close"], 0.01), 0)
            row["amount"] = round(row["close"] * row["volume"], 2)
    save_bars_csv(code, rows, name=name)
    return bool(evaluate_condition(code, "macd_green_shrink")["triggered"])


def seed(force: bool = False) -> dict:
    ensure_dirs()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CSV_DIR.mkdir(parents=True, exist_ok=True)
    if force or not UNIVERSE_PATH.exists():
        UNIVERSE_PATH.write_text(
            json.dumps([{k: v for k, v in item.items() if k != "start"} for item in UNIVERSE], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    days = trading_days(date(2026, 8, 28), 160)
    written = []
    generated = []
    for meta in UNIVERSE:
        path = CSV_DIR / f"{meta['code']}.csv"
        if path.exists() and not force:
            written.append(meta["code"])
            continue
        rng = random.Random(int(meta["code"]))
        min_px = 0.8 if meta["start"] < 5 else 5.0
        drift = -0.0005 if meta["code"] in ("600519", "600036") else 0.00015
        rows = walk(meta["start"], days, rng, drift=drift, vol=0.011, min_px=min_px)
        if meta["code"] == "600010":
            for row in rows:
                row["close"] = round(min(row["close"], 1.88), 2)
                _touch_hl(row)
        save_bars_csv(meta["code"], rows, name=meta["name"])
        written.append(meta["code"])
        generated.append(meta["code"])

    ma5_ok = None
    macd_ok = None
    if force or "600519" in generated:
        ma5_ok = ensure_ma5_reclaim("600519", "贵州茅台")
    if force or "600036" in generated:
        macd_ok = ensure_macd_green_shrink("600036", "招商银行")

    if not load_watches():
        save_watches(
            [
                {
                    "id": "w-600519-ma5",
                    "code": "600519",
                    "name": "贵州茅台",
                    "condition_id": "ma5_reclaim",
                    "condition_text": "日线收盘重新站上5日均线",
                    "created_at": "2026-08-28 09:00:00",
                    "viewed": False,
                    "monitoring": True,
                    "triggered": False,
                    "judgement": None,
                },
                {
                    "id": "w-600036-macd",
                    "code": "600036",
                    "name": "招商银行",
                    "condition_id": "macd_green_shrink",
                    "condition_text": "MACD绿柱缩短不创新低",
                    "created_at": "2026-08-28 09:00:00",
                    "viewed": False,
                    "monitoring": True,
                    "triggered": False,
                    "judgement": None,
                },
            ]
        )

    return {
        "universe": len(UNIVERSE),
        "csv": written,
        "ma5_reclaim": ma5_ok,
        "macd_green_shrink": macd_ok,
        "watches": len(load_watches()),
    }


if __name__ == "__main__":
    print(json.dumps(seed(force=True), ensure_ascii=False))
