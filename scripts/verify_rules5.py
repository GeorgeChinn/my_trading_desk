"""RULES5: missing slot stays empty; close fallback continues; no later-slot leak."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.engine.rulesets import ENGINE_SOS, get_ruleset  # noqa: E402
from app.engine.theme_sos import (  # noqa: E402
    SLOT_BUY_END,
    SLOT_FINAL,
    SLOT_LIVE,
    _SNAP,
    _price_view,
    slot_quote,
)


def _bar(date, close, high=None, low=None, open_=None, volume=1000, amount=1e8):
    return {
        "date": date,
        "open": open_ if open_ is not None else close,
        "high": high if high is not None else close,
        "low": low if low is not None else close,
        "close": close,
        "volume": volume,
        "amount": amount,
        "code": "600000",
    }


def main() -> int:
    rs = get_ruleset("rules5")
    assert rs, "RULES5.MD not discovered"
    assert rs["engine"] == ENGINE_SOS, rs["engine"]
    assert rs["engine_ok"]
    assert "SOS" in rs["title"] or "补涨" in rs["title"]

    _SNAP.clear()
    _SNAP[("2026-09-14", "15:30")] = {
        "quotes": {"600000": {"code": "600000", "close": 15.3, "amount": 9e8}}
    }
    _SNAP[("2026-09-14", "13:30")] = None
    assert slot_quote("2026-09-14", "13:30", "600000") is None
    assert slot_quote("2026-09-14", "15:30", "600000")["close"] == 15.3
    assert slot_quote("2026-09-14", SLOT_LIVE, "600000") is None
    assert slot_quote("2026-09-14", SLOT_FINAL, "600000") is None

    bars = [_bar("2026-09-14", 10.0)]
    row, found, src = _price_view(bars, 0, "600000", SLOT_LIVE)
    assert found is False and src == "close"
    assert row["close"] == 10.0

    _SNAP[("2026-09-14", SLOT_LIVE)] = {
        "quotes": {"600000": {"code": "600000", "close": 11.0, "amount": 2e8, "volume": 2000}}
    }
    row, found, src = _price_view(bars, 0, "600000", SLOT_LIVE)
    assert found is True and src == SLOT_LIVE
    assert row["close"] == 11.0
    late, _, _ = _price_view(bars, 0, "600000", SLOT_LIVE)
    assert late["close"] != 15.3

    row14, found14, src14 = _price_view(bars, 0, "600000", SLOT_BUY_END)
    assert found14 is False and src14 == "close" and row14["close"] == 10.0

    from app.engine.theme_sos import buy_minutes

    assert buy_minutes("close") == 240
    assert buy_minutes(SLOT_LIVE) == 150
    assert buy_minutes(SLOT_BUY_END) == 210

    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
