"""Slot isolation + CSV schema guard for snapshot archive."""
from __future__ import annotations

import inspect
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.engine.bars import merge_bars, save_bars_csv  # noqa: E402
from app.engine.clock import should_write_csv  # noqa: E402
from app.engine.eastmoney import _em_pool, fetch_limit_pool  # noqa: E402
from app.engine.snapshots import (  # noqa: E402
    compute_stats,
    load_snapshot,
    write_events_file,
    write_snapshot_file,
)
from app.engine.scheduler import _loop  # noqa: E402
from app.engine import live as live_mod  # noqa: E402
from app.engine import scanner as scanner_mod  # noqa: E402


SH = ZoneInfo("Asia/Shanghai")


def _payload(day: str, slot: str, close: float, extra: int = 2) -> dict:
    quotes = {
        "600519": {
            "code": "600519",
            "name": "贵州茅台",
            "open": 1400.0,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "pct": 1.0,
            "volume": 1,
            "amount": 1,
            "turnover": 1.0,
            "float_mcap_yi": 100.0,
            "limit_up": 1540.0,
            "limit_down": 1260.0,
        }
    }
    for i in range(extra):
        code = f"{i:06d}"
        quotes[code] = {
            "code": code,
            "name": code,
            "open": 10,
            "high": 10,
            "low": 10,
            "close": 10,
            "pct": 0,
            "volume": 0,
            "amount": 0,
            "turnover": 0,
            "float_mcap_yi": 1,
            "limit_up": 11,
            "limit_down": 9,
        }
    return {
        "trade_date": day,
        "slot": slot,
        "captured_at": f"{day} {slot}:00",
        "market_closed": slot >= "15:00",
        "quote_n": len(quotes),
        "index": {
            "sh000001": {"open": 3000, "high": 3010, "low": 2990, "close": 3005, "pct": 0.1},
            "sz399006": {"open": 2000, "high": 2010, "low": 1990, "close": 2005, "pct": 0.2},
            "sh000300": {"open": 4000, "high": 4010, "low": 3990, "close": 4005, "pct": 0.3},
        },
        "stats": {
            "up_n": 1,
            "down_n": 0,
            "limit_up_n": 0,
            "limit_down_n": 0,
            "fail_n": 0,
            "seal_rate": None,
            "max_board": 0,
        },
        "quotes": quotes,
    }


def main() -> int:
    day = "2026-09-14"
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "snapshots"
        evroot = Path(tmp) / "events"
        write_snapshot_file(_payload(day, "09:30", 10.0), root=root)
        write_snapshot_file(_payload(day, "13:30", 11.0), root=root)
        write_snapshot_file(_payload(day, "15:30", 12.0), root=root)
        files = sorted(p.name for p in (root / day).glob("*.json"))
        assert files == ["0930.json", "1330.json", "1530.json"], files

        write_snapshot_file(_payload(day, "15:30", 13.0), root=root)
        files = sorted(p.name for p in (root / day).glob("*.json"))
        assert files == ["0930.json", "1330.json", "1530.json"], files

        s930 = load_snapshot(day, "09:30", root=root)
        s1330 = load_snapshot(day, "13:30", root=root)
        s1530 = load_snapshot(day, "15:30", root=root)
        assert s930["quotes"]["600519"]["close"] == 10.0
        assert s1330["quotes"]["600519"]["close"] == 11.0
        assert s1530["quotes"]["600519"]["close"] == 13.0
        missing = load_snapshot(day, "10:00", root=root)
        assert missing is None, "missing slot must not fall back to a later slot"
        assert s1330["quotes"]["600519"]["close"] != s1530["quotes"]["600519"]["close"]

        write_events_file(
            {
                "trade_date": day,
                "slot": "13:30",
                "limit_up": [{"code": "600519", "name": "贵州茅台", "fbt": 34200, "fbt_hms": "09:30:00", "lbc": 1, "industry": "白酒"}],
                "fail": [],
                "limit_down": [],
            },
            root=evroot,
        )
        write_events_file(
            {
                "trade_date": day,
                "slot": "15:30",
                "limit_up": [{"code": "600519", "name": "贵州茅台", "fbt": 34200, "fbt_hms": "09:30:00", "lbc": 2, "industry": "白酒"}],
                "fail": [{"code": "000001", "name": "平安", "fbt": 36000, "lbc": 1, "industry": "银行"}],
                "limit_down": [],
            },
            root=evroot,
        )
        assert (evroot / f"{day}.json").exists()
        # snapshots of 13:30 still on disk after events 终版
        assert load_snapshot(day, "13:30", root=root)["quotes"]["600519"]["close"] == 11.0

    src = inspect.getsource(save_bars_csv)
    assert '"time"' not in src
    assert 'fieldnames = ["code", "date", "open", "high", "low", "close", "volume", "amount"]' in src
    merge_src = inspect.getsource(merge_bars)
    assert "by_date" in merge_src

    csv_path = ROOT / "data" / "csv" / "600519.csv"
    header = csv_path.read_text(encoding="utf-8").splitlines()[0]
    assert header.startswith("code,date,open,high,low,close,volume,amount")
    assert "time" not in header.split(",")

    t1330 = datetime(2026, 9, 14, 13, 30, tzinfo=SH)
    t1530 = datetime(2026, 9, 14, 15, 30, tzinfo=SH)
    times = ("09:30", "13:30", "15:30")
    assert should_write_csv(t1330, times) is False
    assert should_write_csv(t1530, times) is True

    pool_src = inspect.getsource(_em_pool)
    assert '"fbt"' in pool_src
    assert inspect.getsource(fetch_limit_pool)

    live_src = inspect.getsource(live_mod.sync_quotes)
    assert "save_bars_csv" not in live_src
    assert "Never writes data/csv" in (live_mod.sync_quotes.__doc__ or "")

    loop_src = inspect.getsource(_loop)
    assert "archive_slot" in loop_src
    assert "should_write_csv" in loop_src

    scan_src = Path(scanner_mod.__file__).read_text(encoding="utf-8")
    assert "snapshots" not in scan_src
    assert "load_quotes" in scan_src
    assert "load_bars" in scan_src

    quotes = {}
    for i in range(200):
        code = f"{i:06d}"
        quotes[code] = {
            "code": code,
            "name": "测试",
            "pct": 1.0 if i < 120 else -1.0,
            "close": 11.0 if i < 5 else 10.0,
            "preclose": 10.0,
            "high": 11.0 if i < 8 else 10.0,
        }
    zt = [{"code": "000001", "lbc": 4, "name": "A"}, {"code": "000002", "lbc": 2, "name": "B"}]
    zb = [{"code": "000003", "lbc": 1}]
    dt = [{"code": "000004", "lbc": 1}]
    stats = compute_stats(quotes, zt=zt, zb=zb, dt=dt)
    assert stats["up_n"] == 120, stats
    assert stats["down_n"] == 80, stats
    assert stats["limit_up_n"] == 2
    assert stats["fail_n"] == 1
    assert stats["limit_down_n"] == 1
    assert stats["max_board"] == 4
    assert stats["seal_rate"] == round(2 / 3 * 100.0, 1)

    subset = {"600519": quotes["000001"]}
    stats_full = compute_stats(quotes, zt=zt, zb=zb, dt=dt)
    stats_sub = compute_stats(subset, zt=zt, zb=zb, dt=dt)
    assert stats_full["up_n"] != stats_sub["up_n"]
    # archive uses full map for stats even if stored quotes are subset
    assert stats_full["up_n"] == 120

    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
