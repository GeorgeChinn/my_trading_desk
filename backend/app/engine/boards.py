"""申万二级日统计：涨幅、涨停数。给 RULES2 主线与情绪模块用，不改 RULES.md。"""
from __future__ import annotations

from datetime import datetime

from ..config import BOARD_DAILY_PATH, CSV_DIR, DATA_DIR, HS300_PATH
from ..store import read_json, write_json
from .bars import load_bars, ts_code
from .pool import is_st_name

INDUSTRY_MAP_PATH = DATA_DIR / "industry_map.json"


def _limit_pct(code: str) -> float:
    c = ts_code(code)
    if c.startswith(("3", "68")):
        return 0.20
    if c.startswith(("8", "4")):
        return 0.30
    return 0.10


def is_limit_up(prev_close, close, code: str) -> bool:
    if not prev_close or not close:
        return False
    return (close / prev_close - 1.0) >= _limit_pct(code) - 0.005


def load_industry_blob() -> dict:
    data = read_json(INDUSTRY_MAP_PATH, {})
    return data if isinstance(data, dict) else {}


def sw_maps() -> tuple[dict[str, str], dict[str, str]]:
    blob = load_industry_blob()
    codes = blob.get("codes") if isinstance(blob.get("codes"), dict) else blob
    sw1 = blob.get("sw1") if isinstance(blob.get("sw1"), dict) else {}
    sw2 = blob.get("sw2") if isinstance(blob.get("sw2"), dict) else {}
    if not sw1 and isinstance(codes, dict):
        sw1 = {str(k): str(v) for k, v in codes.items() if v}
    sw1 = {ts_code(k): str(v) for k, v in (sw1 or {}).items() if v}
    sw2 = {ts_code(k): str(v) for k, v in (sw2 or {}).items() if v}
    return sw1, sw2


def industry_of(code: str) -> str | None:
    sw1, sw2 = sw_maps()
    c = ts_code(code)
    return sw2.get(c) or sw1.get(c)


def load_hs300() -> dict[str, float]:
    data = read_json(HS300_PATH, {})
    rows = data.get("bars") if isinstance(data, dict) else None
    out = {}
    for row in rows or []:
        d = str((row or {}).get("date") or "")[:10]
        try:
            px = float(row.get("close"))
        except (TypeError, ValueError):
            continue
        if d and px:
            out[d] = px
    return out


def refresh_hs300(limit: int = 250) -> dict[str, float]:
    from .eastmoney import fetch_index_kline

    rows = fetch_index_kline("sh000300", limit=limit) or []
    write_json(
        HS300_PATH,
        {
            "symbol": "sh000300",
            "name": "沪深300",
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "bars": rows,
        },
    )
    return {str(r["date"])[:10]: float(r["close"]) for r in rows if r.get("date") and r.get("close")}


def _hs300_ret(closes: dict[str, float], d0: str, d1: str) -> float | None:
    dates = sorted(d for d in closes if d0 <= d <= d1)
    if len(dates) < 2:
        a, b = closes.get(d0), closes.get(d1)
        if a and b:
            return (b / a - 1.0) * 100.0
        return None
    a, b = closes.get(dates[0]), closes.get(dates[-1])
    if not a or not b:
        return None
    return (b / a - 1.0) * 100.0


def _hs300_ret_nd(closes: dict[str, float], asof: str, n: int) -> float | None:
    dates = [d for d in sorted(closes) if d <= asof]
    if len(dates) < n + 1:
        return None
    win = dates[-(n + 1) :]
    a, b = closes.get(win[0]), closes.get(win[-1])
    if not a or not b:
        return None
    return (b / a - 1.0) * 100.0


def load_board_daily() -> dict:
    data = read_json(BOARD_DAILY_PATH, {})
    return data if isinstance(data, dict) else {}


def build_board_daily(asof: str | None = None, last_n: int = 80, force: bool = False) -> dict:
    """按申万二级（缺则一级）汇总每日涨跌与涨停。缓存到 board_daily.json。"""
    from .clock import asof_date

    asof = asof or asof_date()
    cached = load_board_daily()
    if (
        not force
        and cached.get("asof") == asof
        and isinstance(cached.get("boards"), dict)
        and len(cached.get("boards") or {}) >= 10
    ):
        return cached

    sw1, sw2 = sw_maps()
    hs300 = load_hs300()
    if len(hs300) < 10:
        try:
            hs300 = refresh_hs300()
        except Exception:
            hs300 = hs300 or {}

    boards: dict[str, dict[str, dict]] = {}
    names: dict[str, str] = {}
    for path in CSV_DIR.glob("*.csv"):
        code = path.stem
        industry = sw2.get(code) or sw1.get(code)
        if not industry:
            continue
        bars = load_bars(code, last_n=last_n)
        if len(bars) < 3:
            continue
        name = (bars[-1].get("name") or "").strip()
        if name:
            names[code] = name
        if is_st_name(name):
            continue
        bucket = boards.setdefault(industry, {})
        for i in range(1, len(bars)):
            prev, cur = bars[i - 1], bars[i]
            d = str(cur.get("date") or "")[:10]
            pc, cc = prev.get("close"), cur.get("close")
            if not d or not pc or not cc:
                continue
            rec = bucket.get(d)
            if rec is None:
                rec = {"n": 0, "up": 0, "down": 0, "limit_ups": 0, "ret_sum": 0.0}
                bucket[d] = rec
            ret = (cc / pc - 1.0) * 100.0
            rec["n"] += 1
            rec["ret_sum"] += ret
            if ret > 0:
                rec["up"] += 1
            elif ret < 0:
                rec["down"] += 1
            if is_limit_up(pc, cc, code):
                rec["limit_ups"] += 1

    out_boards = {}
    for name, by_date in boards.items():
        dates = sorted(by_date)
        series = []
        for d in dates:
            rec = by_date[d]
            n = rec["n"] or 1
            series.append(
                {
                    "date": d,
                    "n": rec["n"],
                    "up": rec["up"],
                    "down": rec["down"],
                    "up_pct": round(rec["up"] / n * 100.0, 2),
                    "ret_1d": round(rec["ret_sum"] / n, 3),
                    "limit_ups": rec["limit_ups"],
                }
            )
        out_boards[name] = {"dates": dates, "by_date": {row["date"]: row for row in series}}

    payload = {
        "asof": asof,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "hs300": hs300,
        "boards": out_boards,
        "sw2": bool(sw2),
        "board_count": len(out_boards),
        "stock_names": names,
    }
    write_json(BOARD_DAILY_PATH, payload)
    return payload


def _board(payload: dict, industry: str | None) -> dict | None:
    if not industry:
        return None
    boards = payload.get("boards") or {}
    if industry in boards:
        return boards[industry]
    want = str(industry).replace(" ", "")
    for name, rec in boards.items():
        if str(name).replace(" ", "") == want:
            return rec
    return None


def board_ret(payload: dict, industry: str | None, d0: str, d1: str) -> float | None:
    rec = _board(payload, industry)
    if not rec:
        return None
    by_date = rec.get("by_date") or {}
    dates = [d for d in rec.get("dates") or [] if d0 < d <= d1]
    if not dates:
        dates = [d for d in rec.get("dates") or [] if d0 <= d <= d1]
    if not dates:
        return None
    acc = 1.0
    n = 0
    for d in dates:
        row = by_date.get(d) or {}
        r = row.get("ret_1d")
        if r is None:
            continue
        acc *= 1.0 + float(r) / 100.0
        n += 1
    if not n:
        return None
    return (acc - 1.0) * 100.0


def board_ret_nd(payload: dict, industry: str | None, asof: str, n: int = 3) -> float | None:
    rec = _board(payload, industry)
    if not rec:
        return None
    dates = [d for d in rec.get("dates") or [] if d <= asof]
    if len(dates) < n + 1:
        return None
    return board_ret(payload, industry, dates[-(n + 1)], dates[-1])


def board_limit_sum(payload: dict, industry: str | None, d0: str, d1: str) -> int | None:
    rec = _board(payload, industry)
    if not rec:
        return None
    by_date = rec.get("by_date") or {}
    total = 0
    seen = False
    for d in rec.get("dates") or []:
        if d < d0 or d > d1:
            continue
        row = by_date.get(d) or {}
        total += int(row.get("limit_ups") or 0)
        seen = True
    return total if seen else None


def board_limit_nd(payload: dict, industry: str | None, asof: str, n: int = 3) -> int | None:
    rec = _board(payload, industry)
    if not rec:
        return None
    dates = [d for d in rec.get("dates") or [] if d <= asof]
    if len(dates) < n:
        return None
    win = dates[-n:]
    return board_limit_sum(payload, industry, win[0], win[-1])


def hs300_ret(payload: dict, d0: str, d1: str) -> float | None:
    return _hs300_ret(payload.get("hs300") or {}, d0, d1)


def hs300_ret_nd(payload: dict, asof: str, n: int = 3) -> float | None:
    return _hs300_ret_nd(payload.get("hs300") or {}, asof, n)


def board_snapshot(payload: dict, industry: str | None, asof: str) -> dict:
    rec = _board(payload, industry)
    last = None
    if rec:
        dates = [d for d in rec.get("dates") or [] if d <= asof]
        if dates:
            last = (rec.get("by_date") or {}).get(dates[-1])
    return {
        "industry": industry,
        "ret_3d": board_ret_nd(payload, industry, asof, 3),
        "market_3d": hs300_ret_nd(payload, asof, 3),
        "limit_3d": board_limit_nd(payload, industry, asof, 3),
        "last": last,
    }


def mainline_check(payload: dict, industry: str | None, bars: list[dict], strong_start: int, strong_end: int) -> tuple[bool | None, str]:
    """RULES2 主线：先强段内 SW2 ≥ 沪深300+3pct 且累计涨停≥6；买入日近3日≥沪深300且至少1只涨停。"""
    if not industry:
        return None, "主线：无申万二级/一级归属，证据不足"
    if strong_start < 0 or strong_end >= len(bars) or strong_start >= strong_end:
        return None, "主线：先强段日期不足"
    d0 = str(bars[strong_start].get("date") or "")[:10]
    d1 = str(bars[strong_end].get("date") or "")[:10]
    asof = str(bars[-1].get("date") or "")[:10]
    if not d0 or not d1 or not asof:
        return None, "主线：先强段日期不足"
    b_ret = board_ret(payload, industry, d0, d1)
    m_ret = hs300_ret(payload, d0, d1)
    limits = board_limit_sum(payload, industry, d0, d1)
    if b_ret is None or m_ret is None:
        return None, f"主线：先强段内 {industry} / 沪深300 涨幅证据不足"
    if limits is None:
        return None, f"主线：先强段内 {industry} 涨停证据不足"
    if b_ret < m_ret + 3:
        return (
            False,
            f"主线：先强段内 {industry} {b_ret:.2f}% < 沪深300 {m_ret:.2f}% + 3pct",
        )
    if limits < 6:
        return False, f"主线：先强段内 {industry} 累计涨停 {limits} < 6"
    b3 = board_ret_nd(payload, industry, asof, 3)
    m3 = hs300_ret_nd(payload, asof, 3)
    lim3 = board_limit_nd(payload, industry, asof, 3)
    if b3 is None or m3 is None:
        return None, f"主线：买入日 {industry} 近3日相对沪深300证据不足"
    if b3 < m3:
        return False, f"主线：买入日 {industry} 近3日 {b3:.2f}% < 沪深300 {m3:.2f}%"
    if lim3 is None:
        return None, f"主线：买入日 {industry} 近3日涨停证据不足"
    if lim3 < 1:
        return False, f"主线：买入日近3日 {industry} 无涨停（不得用其他票涨停代替）"
    return (
        True,
        (
            f"主线：先强段 {industry} {b_ret:.2f}% ≥ 沪深300 {m_ret:.2f}%+3pct，累计涨停 {limits}；"
            f"买入日近3日 {b3:.2f}% ≥ 沪深300 {m3:.2f}%，涨停 {lim3}"
        ),
    )


def board_funnel_today(payload: dict, asof: str | None = None) -> list[dict]:
    from .clock import asof_date

    asof = asof or payload.get("asof") or asof_date()
    m3 = hs300_ret_nd(payload, asof, 3)
    out = []
    for name, rec in (payload.get("boards") or {}).items():
        r3 = board_ret_nd(payload, name, asof, 3)
        lim3 = board_limit_nd(payload, name, asof, 3)
        last = None
        dates = [d for d in rec.get("dates") or [] if d <= asof]
        if dates:
            last = (rec.get("by_date") or {}).get(dates[-1])
        passed = r3 is not None and m3 is not None and r3 >= m3 and (lim3 or 0) >= 1
        if r3 is None or m3 is None:
            reason = "近3日涨幅证据不足"
        elif r3 < m3:
            reason = f"近3日 {r3:.2f}% < 沪深300 {m3:.2f}%"
        elif (lim3 or 0) < 1:
            reason = "近3日无涨停"
            passed = False
        else:
            reason = f"近3日 {r3:.2f}% ≥ 沪深300 {m3:.2f}%，涨停 {lim3}"
        out.append(
            {
                "name": name,
                "n": (last or {}).get("n") or 0,
                "ret_3d": None if r3 is None else round(r3, 2),
                "market_ret_3d": None if m3 is None else round(m3, 2),
                "vs_market": None if r3 is None or m3 is None else round(r3 - m3, 2),
                "limit_3d": lim3,
                "up_pct": (last or {}).get("up_pct"),
                "pass": passed,
                "reason": reason,
            }
        )
    out.sort(key=lambda x: (0 if x["pass"] else 1, -(x["ret_3d"] if x["ret_3d"] is not None else -999)))
    return out
