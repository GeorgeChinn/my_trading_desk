"""RULES5 主题 SOS 补涨。数字和句子只来自 RULES5.MD，不另写门槛。

主题 = 申万二级（缺则一级）。不与 RULES1–4 混池。
13:30 / 14:30 / 15:00 有快照用快照；缺档字段空着，回测按收盘价继续。禁止用更晚档填更早档。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from ..config import CSV_DIR, GATES_SOS, INDEX_DAILY_PATH, MARKET_AMOUNT_PATH
from ..store import load_quotes, load_universe, read_json, write_json
from .bars import bar_amount, load_bars, overlay_quote_bar, peek_last_bar, ts_code
from .boards import industry_of, is_limit_up, load_board_daily
from .clock import now_sh, session_trading_date
from .indicators import sma
from .pool import is_st_name
from .scanner import FACT_NOTE

L = 40
LISTED_DAYS = 60
PRICE_MIN = 3.0
MCAP_LO = 20.0
MCAP_HI = 800.0
MA20_BAND = 0.99
DD60 = 0.35
RUN20 = 0.40
TURN3 = 20.0
TURN1 = 25.0
TURN_LIMIT = 18.0
POOL_LIVE_N = 15
OPEN_MAX = 3
BUY_A_VR = 1.5
BUY_B_VR = 1.2
BUY_B_LO = 0.03
BUY_B_HI = 0.07
FLOOR = 0.97
FAIL_PCT = 0.95
WIN_PCT = 0.12
GIVEBACK = 0.05
AMT_RATIO = 0.85
ZT_OK = 40
ZT_WEAK = 25
SEAL_OK = 60.0
SEAL_1330 = 55.0
HEIGHT_MAX = 7
DOWN_MAX = 0.80
EXCESS = 1.5
TH_ZT = 3
TH3_SUM = 5
LEADERS = 2
LEAD_PCT = 0.05
TOP_SHARE = 0.15
SIZE_A_OK = "12%–18%"
SIZE_A_WEAK = "6%–9%"
SIZE_B = "8%–12%"
SLOT_LIVE = "13:30"
SLOT_BUY_END = "14:30"
SLOT_FINAL = "15:00"

_SNAP: dict[tuple[str, str], dict | None] = {}
_EVENTS: dict[str, dict | None] = {}
_AMT: dict[str, float] = {}
_INDEX: dict[str, dict[str, float]] = {}


def _num(value):
    if value is None or value == "":
        return None
    try:
        val = float(value)
        if val != val:
            return None
        return val
    except (TypeError, ValueError):
        return None


def _px(row: dict | None, key: str = "close"):
    if not row:
        return None
    return _num(row.get(key))


def _vol(row: dict | None) -> float:
    return float(row.get("volume") or 0) if row else 0.0


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _limit_pct(code: str) -> float:
    c = ts_code(code)
    if c.startswith(("3", "68")):
        return 0.20
    if c.startswith(("8", "4")):
        return 0.30
    return 0.10


def _is_bj(code: str) -> bool:
    c = ts_code(code)
    return c.startswith(("8", "4")) and not c.startswith("68")


def _yi_zi(row: dict | None) -> bool:
    if not row:
        return False
    o, h, l, c = row.get("open"), row.get("high"), row.get("low"), row.get("close")
    if None in (o, h, l, c):
        return False
    return abs(h - l) <= 1e-9 and abs(c - o) <= 1e-9


def _is_limit_bar(bars: list[dict], i: int, code: str) -> bool:
    if i < 1:
        return False
    return is_limit_up(bars[i - 1].get("close"), bars[i].get("close"), code)


def _touch_limit(bars: list[dict], i: int, code: str) -> bool:
    if i < 1:
        return False
    prev = _px(bars[i - 1], "close")
    high = _px(bars[i], "high")
    if not prev or not high:
        return False
    return (high / prev - 1.0) >= _limit_pct(code) - 0.005


def _streak(bars: list[dict], i: int, code: str) -> int:
    n = 0
    j = i
    while j >= 1 and _is_limit_bar(bars, j, code):
        n += 1
        j -= 1
    return n


def _session_minutes(slot: str) -> int:
    try:
        hour, minute = int(slot[:2]), int(slot[3:5])
    except (TypeError, ValueError):
        return 240
    t = hour * 60 + minute
    open_ = 9 * 60 + 30
    lunch_s = 11 * 60 + 30
    lunch_e = 13 * 60
    close = 15 * 60
    if t <= open_:
        return 0
    if t <= lunch_s:
        return t - open_
    if t < lunch_e:
        return 120
    if t >= close:
        return 240
    return 120 + (t - lunch_e)


def _in_buy_window(when: datetime | None = None) -> bool:
    when = when or now_sh()
    if when.weekday() >= 5:
        return False
    stamp = (when.hour, when.minute)
    return (13, 30) <= stamp <= (14, 30)


def _after_buy_window(when: datetime | None = None) -> bool:
    when = when or now_sh()
    if when.weekday() >= 5:
        return True
    return (when.hour, when.minute) > (14, 30)


def _cached_snap(date: str, slot: str) -> dict | None:
    day = str(date or "")[:10]
    stamp = str(slot or "")[:5]
    key = (day, stamp)
    if key in _SNAP:
        return _SNAP[key]
    from .snapshots import load_snapshot

    item = load_snapshot(day, stamp)
    _SNAP[key] = item
    return item


def _cached_events(date: str) -> dict | None:
    day = str(date or "")[:10]
    if day in _EVENTS:
        return _EVENTS[day]
    from .snapshots import load_events

    item = load_events(day)
    _EVENTS[day] = item
    return item


def slot_quote(date: str, slot: str, code: str) -> dict | None:
    """Exact slot only. Missing → None, never a later slot."""
    snap = _cached_snap(date, slot)
    if not snap:
        return None
    quotes = snap.get("quotes") if isinstance(snap, dict) else None
    if not isinstance(quotes, dict):
        return None
    rec = quotes.get(ts_code(code))
    return rec if isinstance(rec, dict) else None


def slot_stats(date: str, slot: str) -> dict:
    snap = _cached_snap(date, slot)
    stats = (snap or {}).get("stats") if isinstance(snap, dict) else None
    return dict(stats) if isinstance(stats, dict) else {}


def slot_index(date: str, slot: str) -> dict:
    snap = _cached_snap(date, slot)
    idx = (snap or {}).get("index") if isinstance(snap, dict) else None
    return dict(idx) if isinstance(idx, dict) else {}


def _vwap(row: dict | None):
    if not row:
        return None
    amt = _num(row.get("amount"))
    vol = _num(row.get("volume"))
    if not amt or not vol or vol <= 0:
        return None
    px = amt / vol
    if px > 10000:
        px = amt / (vol * 100.0)
    return px


def _turnover(row: dict | None, mcap_yi=None):
    if not row:
        return None
    t = _num(row.get("turnover"))
    if t is not None:
        return t
    amt = _num(row.get("amount"))
    mcap = _num(mcap_yi if mcap_yi is not None else row.get("float_mcap_yi"))
    if amt and mcap and mcap > 0:
        return amt / (mcap * 100_000_000.0) * 100.0
    return None


def ensure_index_daily(force: bool = False, fetch: bool = True) -> dict[str, dict[str, float]]:
    global _INDEX
    if _INDEX and not force:
        return _INDEX
    stored = read_json(INDEX_DAILY_PATH, {}) if INDEX_DAILY_PATH.exists() else {}
    out: dict[str, dict[str, float]] = {}
    for sym in ("sh000001", "sz399006"):
        blob = stored.get(sym) if isinstance(stored, dict) else None
        rows = (blob or {}).get("bars") if isinstance(blob, dict) else None
        series = {}
        for row in rows or []:
            d = str((row or {}).get("date") or "")[:10]
            px = _num((row or {}).get("close"))
            if d and px:
                series[d] = px
        out[sym] = series
    need = any(len(out.get(sym) or {}) < 30 for sym in ("sh000001", "sz399006"))
    if fetch and (force or need):
        try:
            from .eastmoney import fetch_index_kline

            payload = {"updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
            for sym in ("sh000001", "sz399006"):
                rows = fetch_index_kline(sym, limit=400) or []
                payload[sym] = {"bars": rows}
                series = {}
                for row in rows:
                    d = str(row.get("date") or "")[:10]
                    px = _num(row.get("close"))
                    if d and px:
                        series[d] = px
                if series:
                    out[sym] = series
            write_json(INDEX_DAILY_PATH, payload)
        except Exception:
            pass
    _INDEX = out
    return _INDEX


def _index_ma20(sym: str, date: str) -> tuple:
    series = (ensure_index_daily(fetch=False).get(sym) or {})
    dates = [d for d in sorted(series) if d <= date]
    if len(dates) < 20:
        return None, None
    win = dates[-20:]
    close = series.get(dates[-1])
    ma = _mean([series[d] for d in win])
    return close, ma


def e1_ok(date: str) -> tuple[bool, str]:
    sh_c, sh_ma = _index_ma20("sh000001", date)
    cy_c, cy_ma = _index_ma20("sz399006", date)
    sh_ok = bool(sh_c and sh_ma and sh_c >= sh_ma - 1e-12)
    cy_ok = bool(cy_c and cy_ma and cy_c >= cy_ma - 1e-12)
    if sh_ok or cy_ok:
        bits = []
        if sh_ok:
            bits.append(f"上证 {sh_c:.2f} ≥ MA20 {sh_ma:.2f}")
        if cy_ok:
            bits.append(f"创业板 {cy_c:.2f} ≥ MA20 {cy_ma:.2f}")
        return True, "；".join(bits)
    if sh_c is None and cy_c is None:
        return False, "E1 指数日线证据不足"
    return False, "E1：上证与创业板都破 MA20"


def _quotes_amount(quotes: dict | None) -> float | None:
    total = 0.0
    n = 0
    for rec in (quotes or {}).values():
        if not isinstance(rec, dict):
            continue
        amt = _num(rec.get("amount"))
        if amt and amt > 0:
            total += amt
            n += 1
    return total if n >= 200 else None


def _load_amount_cache() -> dict[str, float]:
    data = read_json(MARKET_AMOUNT_PATH, {}) if MARKET_AMOUNT_PATH.exists() else {}
    by_date = data.get("by_date") if isinstance(data, dict) else None
    if not isinstance(by_date, dict):
        return {}
    out = {}
    for day, raw in by_date.items():
        amt = _num(raw)
        if amt:
            out[str(day)[:10]] = amt
    return out


def _save_amount_cache(amap: dict[str, float]) -> None:
    write_json(
        MARKET_AMOUNT_PATH,
        {
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "by_date": amap,
        },
    )


def _amount_map(last_n: int = 16, rebuild: bool = False, quotes: dict | None = None, asof: str = "") -> dict[str, float]:
    """Two-market amount by date. Disk cache first; never re-scan all csv on a single-stock classify."""
    global _AMT
    if not _AMT:
        _AMT.update(_load_amount_cache())
    if quotes and asof:
        live = _quotes_amount(quotes)
        if live:
            _AMT[asof] = live
    if _AMT and not rebuild:
        return _AMT
    if not rebuild:
        return _AMT
    acc: dict[str, float] = {}
    for path in CSV_DIR.glob("*.csv"):
        for row in load_bars(path.stem, last_n=last_n):
            amt = bar_amount(row)
            d = str(row.get("date") or "")[:10]
            if d and amt:
                acc[d] = acc.get(d, 0.0) + float(amt)
    if acc:
        _AMT.update(acc)
        _save_amount_cache(dict(_AMT))
    return _AMT


def _trading_dates(asof: str, n: int) -> list[str]:
    amap = _amount_map()
    dates = [d for d in sorted(amap) if d <= asof]
    if len(dates) < n:
        board = load_board_daily()
        sample = next(iter((board.get("boards") or {}).values()), {}) if board else {}
        dates = [d for d in (sample.get("dates") or []) if d <= asof]
    return dates[-n:]


def market_stats_from_close(date: str) -> dict:
    board = load_board_daily()
    up = down = zt = tot = 0
    for rec in (board.get("boards") or {}).values():
        row = (rec.get("by_date") or {}).get(date) or {}
        up += int(row.get("up") or 0)
        down += int(row.get("down") or 0)
        zt += int(row.get("limit_ups") or 0)
        tot += int(row.get("n") or 0)
    events = _cached_events(date) or {}
    zt_pool = events.get("limit_up") or []
    zb_pool = events.get("fail") or []
    dt_pool = events.get("limit_down") or []
    if zt_pool:
        zt = len(zt_pool)
    fail_n = len(zb_pool) if zb_pool else None
    height = 0
    for rec in zt_pool:
        try:
            height = max(height, int(rec.get("lbc") or 0))
        except (TypeError, ValueError):
            continue
    touched = (zt + fail_n) if fail_n is not None else None
    seal = round(zt / touched * 100.0, 1) if touched else None
    amap = _amount_map()
    today_amt = amap.get(date)
    prev = [amap[d] for d in _trading_dates(date, 6) if d < date and amap.get(d)][-5:]
    avg5 = _mean(prev) if len(prev) == 5 else None
    return {
        "up_n": up,
        "down_n": down,
        "total": tot,
        "limit_up_n": zt,
        "fail_n": fail_n,
        "limit_down_n": len(dt_pool) if dt_pool else None,
        "seal_rate": seal,
        "max_board": height or None,
        "amount": today_amt,
        "amount_avg5": avg5,
        "source": "close",
    }


def market_stats_at(date: str, slot: str | None) -> dict:
    """Slot stats if that file exists; else close-based counts. Never fills a later slot."""
    close_stats = market_stats_from_close(date)
    if not slot:
        return close_stats
    snap_s = slot_stats(date, slot)
    if not snap_s:
        close_stats["slot"] = slot
        close_stats["slot_found"] = False
        return close_stats
    out = dict(close_stats)
    out["slot"] = slot
    out["slot_found"] = True
    out["source"] = slot
    for key in ("up_n", "down_n", "limit_up_n", "limit_down_n", "fail_n", "seal_rate", "max_board"):
        if snap_s.get(key) is not None:
            out[key] = snap_s.get(key)
    return out


def classify_env(stats: dict, date: str, draft_1330: bool = False) -> dict:
    e1, e1_why = e1_ok(date)
    zt = stats.get("limit_up_n")
    seal = stats.get("seal_rate")
    height = stats.get("max_board")
    down_n = stats.get("down_n")
    up_n = stats.get("up_n")
    tot = stats.get("total") or ((up_n or 0) + (down_n or 0))
    down_pct = (down_n / tot) if tot and down_n is not None else None
    amt = stats.get("amount")
    avg5 = stats.get("amount_avg5")
    e2 = False
    e2_why = "E2 证据不足"
    if draft_1330:
        ok_zt = zt is not None and zt >= ZT_WEAK
        ok_seal = seal is not None and seal >= SEAL_1330
        ok_h = height is None or height <= HEIGHT_MAX
        e2 = bool(ok_zt and ok_seal and ok_h)
        e2_why = (
            f"13:30 草稿：涨停 {zt} 封板率 {seal} 最高连板 {height}"
            if e2
            else f"13:30 草稿环境未过：涨停 {zt} / 封板率 {seal} / 高度 {height}"
        )
    else:
        parts = []
        ok = True
        if zt is None:
            ok = False
            parts.append("涨停家数空")
        elif zt < ZT_OK:
            ok = False
            parts.append(f"涨停 {zt} < {ZT_OK}")
        else:
            parts.append(f"涨停 {zt}")
        if seal is None:
            ok = False
            parts.append("封板率空")
        elif seal < SEAL_OK:
            ok = False
            parts.append(f"封板率 {seal}% < {SEAL_OK}%")
        else:
            parts.append(f"封板率 {seal}%")
        if height is None:
            parts.append("连板高度空")
        elif height > HEIGHT_MAX:
            ok = False
            parts.append(f"最高连板 {height} > {HEIGHT_MAX}")
        else:
            parts.append(f"最高连板 {height}")
        e2 = ok
        e2_why = "E2：" + "，".join(parts)
    e3 = down_pct is not None and down_pct <= DOWN_MAX
    e3_why = (
        f"E3：下跌占比 {down_pct * 100:.1f}%"
        if down_pct is not None
        else "E3：下跌家数空"
    )
    e4 = bool(amt and avg5 and amt >= avg5 * AMT_RATIO)
    e4_why = (
        f"E4：成交额 {amt / 1e8:.0f} 亿 / 5日均 {avg5 / 1e8:.0f} 亿"
        if amt and avg5
        else "E4：两市成交额空"
    )
    if draft_1330:
        e3_use = None
        e4_use = None
        passed_tail = int(bool(e2))
        label = "ENV_OK" if e1 and e2 else ("ENV_OFF" if not e1 else "ENV_WEAK")
        if e1 and zt is not None and ZT_WEAK <= zt < ZT_OK:
            label = "ENV_WEAK"
        return {
            "label": label if e1 else "ENV_OFF",
            "e1": e1,
            "e2": e2,
            "e3": e3_use,
            "e4": e4_use,
            "why": [e1_why, e2_why, "13:30 不卡死全日下跌家数、全日成交额"],
            "zt": zt,
            "draft": True,
        }
    tail = [e2, e3, e4]
    n_tail = sum(1 for x in tail if x)
    weak_zt = zt is not None and ZT_WEAK <= zt < ZT_OK
    if not e1:
        label = "ENV_OFF"
    elif n_tail >= 2 and not weak_zt:
        label = "ENV_OK"
    elif n_tail >= 1 or weak_zt:
        label = "ENV_WEAK"
    else:
        label = "ENV_OFF"
    return {
        "label": label,
        "e1": e1,
        "e2": e2,
        "e3": e3,
        "e4": e4,
        "why": [e1_why, e2_why, e3_why, e4_why],
        "zt": zt,
        "draft": False,
        "down_pct": None if down_pct is None else round(down_pct * 100.0, 1),
        "n_tail": n_tail,
    }


def _board_row(industry: str | None, date: str) -> dict:
    if not industry:
        return {}
    payload = load_board_daily()
    rec = (payload.get("boards") or {}).get(industry) or {}
    return dict((rec.get("by_date") or {}).get(date) or {})


def _is_limit_quote(code: str, rec: dict) -> bool:
    close = _num(rec.get("close"))
    lu = _num(rec.get("limit_up"))
    if close and lu and close >= lu - 0.02:
        return True
    pct = _num(rec.get("pct"))
    band = _limit_pct(code) * 100.0
    return pct is not None and pct >= band - 0.5


def _theme_zt(industry: str | None, date: str, slot: str | None = None) -> int | None:
    if slot:
        snap = _cached_snap(date, slot)
        if not snap:
            return None
        n = 0
        for code, rec in (snap.get("quotes") or {}).items():
            if not isinstance(rec, dict) or industry_of(code) != industry:
                continue
            if _is_limit_quote(code, rec):
                n += 1
        return n
    events = _cached_events(date) or {}
    pool = events.get("limit_up") if isinstance(events, dict) else None
    if pool:
        return sum(1 for rec in pool if industry_of(str(rec.get("code") or "")) == industry)
    row = _board_row(industry, date)
    if not row:
        return None
    return int(row.get("limit_ups") or 0)


def _theme_ret(industry: str | None, date: str) -> float | None:
    row = _board_row(industry, date)
    if not row:
        return None
    return _num(row.get("ret_1d"))


def _sh_ret(date: str) -> float | None:
    series = ensure_index_daily().get("sh000001") or {}
    dates = [d for d in sorted(series) if d <= date]
    if len(dates) < 2:
        return None
    a, b = series.get(dates[-2]), series.get(dates[-1])
    if not a or not b:
        return None
    return (b / a - 1.0) * 100.0


def _rank_theme(date: str, industry: str | None) -> tuple:
    ret = _theme_ret(industry, date)
    payload = load_board_daily()
    rets = []
    for name, rec in (payload.get("boards") or {}).items():
        row = (rec.get("by_date") or {}).get(date) or {}
        r = _num(row.get("ret_1d"))
        if r is not None:
            rets.append((name, r))
    rets.sort(key=lambda x: x[1], reverse=True)
    if not rets or ret is None:
        return None, ret, None
    rank = next((i + 1 for i, (n, _) in enumerate(rets) if n == industry), None)
    top_n = max(1, int(len(rets) * TOP_SHARE + 0.999))
    return rank, ret, rank is not None and rank <= top_n


def _climax_zero(counts: list[int]) -> bool:
    if len(counts) < 2:
        return False
    for i in range(len(counts) - 1):
        if counts[i] >= TH3_SUM and counts[i + 1] == 0:
            return True
    return False


def theme_state(industry: str | None, date: str, slot: str | None = None) -> dict:
    if not industry:
        return {"live": False, "dead": True, "why": "无主题", "industry": ""}
    zt = _theme_zt(industry, date, slot=slot)
    ret = _theme_ret(industry, date)
    rank, _, in_top = _rank_theme(date, industry)
    sh = _sh_ret(date)
    excess = (ret - sh) if ret is not None and sh is not None else None
    dates = _trading_dates(date, 4)
    counts = []
    for d in dates:
        n = _theme_zt(industry, d)
        counts.append(0 if n is None else n)
    th3_sum = sum(counts[-3:]) if len(counts) >= 3 else sum(counts)
    th1 = zt is not None and zt >= TH_ZT
    th2 = bool(in_top) or (excess is not None and excess >= EXCESS)
    th3 = th3_sum >= TH3_SUM and not _climax_zero(counts[-3:] if len(counts) >= 3 else counts)
    live = bool(th1 and (th2 or th3))
    green = ret is not None and ret < 0
    dead = (zt is not None and zt < TH_ZT and green) or (zt is not None and zt < TH_ZT and ret is not None and ret < 0)
    why = (
        f"{industry} 涨停 {zt} · 涨幅 {ret} 排名 {rank} · 超额 {None if excess is None else round(excess, 2)} · 近3日涨停 {th3_sum}"
    )
    return {
        "live": live,
        "dead": bool(dead and not live),
        "th1": th1,
        "th2": th2,
        "th3": th3,
        "zt": zt,
        "ret": ret,
        "rank": rank,
        "industry": industry,
        "why": why,
        "slot": slot,
        "slot_found": bool(slot and _cached_snap(date, slot)),
    }


def pick_mainline(date: str, slot: str | None = None) -> dict | None:
    payload = load_board_daily()
    best = None
    for name in (payload.get("boards") or {}):
        st = theme_state(name, date, slot=slot)
        if not st.get("live"):
            continue
        key = (st.get("zt") or 0, st.get("ret") or -999)
        if best is None or key > best[0]:
            best = (key, st)
    return None if best is None else best[1]


def _hhv(bars: list[dict], i: int, n: int):
    lo = max(0, i - n + 1)
    highs = [_px(bars[j], "high") for j in range(lo, i + 1)]
    highs = [x for x in highs if x]
    return max(highs) if highs else None


def _ret_n(bars: list[dict], i: int, n: int):
    if i < n:
        return None
    a, b = _px(bars[i - n], "close"), _px(bars[i], "close")
    if not a or not b:
        return None
    return b / a - 1.0


def _ma20(bars: list[dict], i: int):
    if i + 1 < 20:
        return None
    xs = [_px(bars[j], "close") for j in range(i - 19, i + 1)]
    if any(x is None for x in xs):
        return None
    return _mean(xs)


def _listed_ok(bars: list[dict], asof: str) -> bool:
    first = str(bars[0].get("date") or "")[:10]
    if not first:
        return False
    try:
        d0 = datetime.strptime(first, "%Y-%m-%d").date()
        d1 = datetime.strptime(asof, "%Y-%m-%d").date()
    except ValueError:
        return False
    return (d1 - d0).days >= LISTED_DAYS


def pool_fail(
    bars: list[dict],
    i: int,
    code: str,
    name: str,
    meta: dict,
    px_row: dict | None,
    slot_found: bool,
) -> list[str]:
    fail = []
    if is_st_name(name) or meta.get("is_st"):
        fail.append("ST")
    if _is_bj(code):
        fail.append("北证")
    if i + 1 < L:
        fail.append(f"日线不足 {L} 根")
        return fail
    sl = bars[: i + 1]
    asof = str(bars[i].get("date") or "")[:10]
    if not _listed_ok(sl, asof):
        fail.append("上市不足 60 日")
    if not any(_vol(x) > 0 for x in sl[-L:]):
        fail.append("近 40 根无成交量")
    row = px_row or bars[i]
    close = _px(row, "close")
    if close is None or close < PRICE_MIN:
        fail.append("价 < 3 元" if close is not None else "现价空")
    mcap = _num(meta.get("float_mcap_yi") or (px_row or {}).get("float_mcap_yi"))
    if mcap is None:
        fail.append("流通市值证据不足")
    elif mcap < MCAP_LO or mcap > MCAP_HI:
        fail.append(f"流通市值 {mcap:.0f} 亿不在 20–800")
    ma = _ma20(bars, i)
    if close is not None and ma is not None and close < ma * MA20_BAND - 1e-12:
        fail.append(f"现价未站上 MA20×0.99（{ma * MA20_BAND:.2f}）")
    elif close is not None and ma is None:
        fail.append("MA20 不足")
    hh = _hhv(bars, i, 60)
    dd = ((hh - close) / hh) if hh and close else None
    r20 = _ret_n(bars, i, 20)
    a = dd is not None and dd <= DD60
    b = r20 is not None and r20 < RUN20
    if dd is None and r20 is None:
        fail.append("60日高回撤 / 20日涨幅证据不足")
    elif not a and not b:
        fail.append("距近 60 日高回撤 > 35% 且近 20 日涨幅 ≥ 40%")
    turns = []
    for j in range(max(0, i - 2), i + 1):
        t = _turnover(bars[j] if j != i else row, mcap)
        if t is not None:
            turns.append(t)
    if turns and _mean(turns) > TURN3:
        fail.append(f"近 3 日换手均值 {_mean(turns):.1f}% > 20%")
    t1 = _turnover(row, mcap)
    if t1 is not None and t1 > TURN1:
        fail.append(f"单日换手 {t1:.1f}% > 25%")
    streak = _streak(bars, i, code)
    if streak >= 3:
        fail.append("已 3 连板及以上")
    if streak >= 2:
        fail.append("龙头 2 板不进新开")
    if _is_limit_bar(bars, i, code) and t1 is not None and t1 > TURN_LIMIT:
        fail.append("当日已涨停且换手 > 18%，次日不得新开")
    if not slot_found and px_row is None:
        pass
    return fail


def _kick_final(bars: list[dict], i: int, code: str, theme: dict, px_row: dict | None, slot_found: bool) -> str:
    row = px_row or bars[i]
    mcap = _num((px_row or {}).get("float_mcap_yi"))
    t1 = _turnover(row, mcap)
    if t1 is not None and t1 > 20:
        return "尾盘换手 > 20%，踢出终版"
    if theme.get("ret") is not None and theme.get("ret") < 0:
        return "所属主题尾盘翻绿，踢出终版"
    if not slot_found:
        return ""
    vwap = _vwap(px_row)
    close = _px(row, "close")
    open_px = _px(row, "open")
    high = _px(row, "high")
    low = _px(row, "low")
    if vwap is None or close is None or open_px is None:
        return ""
    body_lo = min(open_px, close)
    body_hi = max(open_px, close)
    lower_third = body_hi != body_lo and close <= body_lo + (body_hi - body_lo) / 3.0 + 1e-12
    vol_up = False
    if i >= 1:
        vol_up = _vol(row) > _vol(bars[i - 1])
    if vol_up and close < vwap - 1e-12 and lower_third:
        return "13:30 后放量跌破分时均价并收在实体下 1/3，踢出终版"
    return ""


def other_rules_holds() -> set[str]:
    codes: set[str] = set()
    try:
        from .buy_log import load_buy_log

        for rid in ("rules", "rules2", "rules3", "rules4"):
            for item in load_buy_log(rid).get("items") or []:
                if item.get("closed"):
                    continue
                c = ts_code(str(item.get("code") or ""))
                if c:
                    codes.add(c)
    except Exception:
        pass
    return codes


def _price_view(bars: list[dict], i: int, code: str, slot: str | None) -> tuple[dict, bool, str]:
    close_row = bars[i]
    date = str(close_row.get("date") or "")[:10]
    if slot:
        q = slot_quote(date, slot, code)
        if q:
            return q, True, slot
        return close_row, False, "close"
    return close_row, False, "close"


def buy_minutes(slot_found: str) -> int:
    if slot_found == SLOT_LIVE:
        return _session_minutes(SLOT_LIVE)
    if slot_found == SLOT_BUY_END:
        return _session_minutes(SLOT_BUY_END)
    return 240


def _vol_ratio(row: dict, prev_amt, minutes: int) -> float | None:
    amt = _num(row.get("amount"))
    if amt is None:
        amt = bar_amount(row)
    if not amt or not prev_amt or prev_amt <= 0 or minutes <= 0:
        return None
    folded = amt / (minutes / 240.0)
    return folded / prev_amt


def _prev_amount(bars: list[dict], i: int):
    if i < 1:
        return None
    return bar_amount(bars[i - 1])


def evaluate_buy_a(bars: list[dict], i: int, code: str, row: dict, minutes: int, theme: dict, slot_found: bool) -> tuple[bool, list[str], list[str]]:
    hit, miss = [], []
    px = _px(row, "close")
    prev_high = _px(bars[i - 1], "high") if i >= 1 else None
    hh10 = _hhv(bars, i - 1, 10) if i >= 1 else None
    brk = False
    if px is not None and prev_high and px > prev_high + 1e-12:
        brk = True
        hit.append(f"现价突破昨高 {prev_high:.2f}")
    elif px is not None and hh10 and px > hh10 + 1e-12:
        brk = True
        hit.append(f"现价突破近 10 日高 {hh10:.2f}")
    else:
        miss.append("现价未突破昨高或近 10 日高")
    vr = _vol_ratio(row, _prev_amount(bars, i), minutes)
    if vr is not None and vr >= BUY_A_VR:
        hit.append(f"折算量比 {vr:.2f} ≥ 1.5")
    elif vr is None:
        miss.append("折算量比证据不足")
    else:
        miss.append(f"折算量比 {vr:.2f} < 1.5")
    vwap = _vwap(row)
    if vwap is None:
        if not slot_found:
            hit.append("分时均价空（缺档，不卡）")
        else:
            miss.append("分时均价空")
    elif px is not None and px > vwap + 1e-12:
        hit.append(f"现价 > 分时均价 {vwap:.2f}")
    else:
        miss.append("现价未站上分时均价")
    open_px = _px(row, "open")
    pre = _px(bars[i - 1], "close") if i >= 1 else None
    floor_base = None
    if open_px is not None and pre is not None:
        floor_base = min(open_px, pre)
    elif open_px is not None:
        floor_base = open_px
    elif pre is not None:
        floor_base = pre
    if px is not None and floor_base:
        if px >= floor_base * FLOOR - 1e-12:
            hit.append(f"未跌破开盘与昨收较低者的 97%（{floor_base * FLOOR:.2f}）")
        else:
            miss.append("跌破开盘与昨收较低者的 97%")
    else:
        miss.append("开盘/昨收证据不足")
    if theme.get("live"):
        hit.append("所属主题 13:30 仍活" if theme.get("slot") == SLOT_LIVE else "所属主题仍活")
    else:
        miss.append("所属主题未活")
    streak = _streak(bars, i - 1, code) if i >= 1 else 0
    if streak >= 3:
        miss.append("3 连板不得新开")
    else:
        hit.append("不是 3 连板")
    t = _turnover(row)
    minutes = minutes or 240
    t_fold = t / (minutes / 240.0) if t is not None and minutes else t
    if t_fold is None and not slot_found:
        hit.append("折算换手空（缺档，不卡）")
    elif t_fold is not None and t_fold <= TURN_LIMIT:
        hit.append(f"实时折算换手 {t_fold:.1f}% ≤ 18%")
    else:
        miss.append("实时折算换手 > 18% 或证据不足")
    if _yi_zi(row) and _touch_limit(bars, i, code):
        miss.append("一字封死买不到 = 本轮作废")
    return (not miss), hit, miss


def evaluate_buy_b(bars: list[dict], i: int, code: str, row: dict, minutes: int, theme: dict, leaders_n: int, env_label: str) -> tuple[bool, list[str], list[str]]:
    hit, miss = [], []
    if env_label != "ENV_OK":
        miss.append("BUY_B 仅 ENV_OK，ENV_WEAK 关闭")
        return False, hit, miss
    if leaders_n < LEADERS:
        miss.append(f"同主题领先股不足 {LEADERS} 只")
    else:
        hit.append(f"同主题已有 {leaders_n} 只领先股")
    px = _px(row, "close")
    pre = _px(bars[i - 1], "close") if i >= 1 else None
    pct = ((px / pre) - 1.0) if px and pre else None
    if pct is None:
        miss.append("当日涨幅证据不足")
    elif BUY_B_LO - 1e-12 <= pct <= BUY_B_HI + 1e-12:
        hit.append(f"当日涨幅 {pct * 100:.2f}% 在 3%–7%")
    else:
        miss.append(f"当日涨幅不在 3%–7%（{None if pct is None else round(pct * 100, 2)}%）")
    if i >= 1 and _is_limit_bar(bars, i, code):
        miss.append("本票已涨停，不得 BUY_B")
    vr = _vol_ratio(row, _prev_amount(bars, i), minutes)
    if vr is not None and vr >= BUY_B_VR:
        hit.append(f"量比 {vr:.2f} ≥ 1.2")
    elif vr is None:
        miss.append("量比证据不足")
    else:
        miss.append(f"量比 {vr:.2f} < 1.2")
    ma = _ma20(bars, i)
    close = _px(row, "close")
    if close is not None and ma is not None and close >= ma - 1e-12:
        hit.append("收盘结构未破 MA20")
    else:
        miss.append("收盘结构已破 MA20 或均线不足")
    if not theme.get("live"):
        miss.append("所属主题未活")
    if _yi_zi(row) and _touch_limit(bars, i, code):
        miss.append("一字封死买不到 = 本轮作废")
    return (not miss), hit, miss


def evaluate_exit_sos(bars: list[dict], open_pos: dict | None, zone: dict | None = None) -> tuple[bool, str, str]:
    if not bars or not open_pos:
        return False, "", ""
    buy_date = str(open_pos.get("buy_date") or open_pos.get("date") or "")[:10]
    buy_i = 0
    for i, row in enumerate(bars):
        if str(row.get("date") or "")[:10] >= buy_date:
            buy_i = i
            break
    last_i = len(bars) - 1
    last = bars[last_i]
    close = _px(last, "close")
    buy_px = _num(open_pos.get("buy_price")) or _px(bars[buy_i], "close")
    code = ts_code(str(open_pos.get("code") or last.get("code") or ""))
    if close is None or buy_px is None:
        return False, "", ""
    industry = (zone or {}).get("industry") or industry_of(code)
    asof = str(last.get("date") or "")[:10]
    if close <= buy_px * FAIL_PCT + 1e-12:
        return True, "失败", f"最新价 {close:.2f} ≤ 买入价×0.95（{buy_px * FAIL_PCT:.2f}）"
    buy_ma20 = _num((zone or {}).get("buy_ma20")) or _ma20(bars, buy_i)
    avg5 = None
    if last_i >= 5:
        avg5 = _mean([_vol(bars[j]) for j in range(last_i - 5, last_i)])
    if buy_ma20 and close < buy_ma20 - 1e-12 and avg5 and _vol(last) >= avg5 - 1e-12:
        return True, "失败", f"收盘跌破买入日 MA20 {buy_ma20:.2f}，且量 ≥ 近 5 日均量"
    held = last_i - buy_i
    if held >= 3:
        above = False
        for j in range(buy_i + 1, last_i + 1):
            c = _px(bars[j], "close")
            if c is not None and c >= buy_px - 1e-12:
                above = True
                break
        if not above:
            return True, "失败", "买入后 3 个交易日从未收在成本上"
    dates = _trading_dates(asof, 3)
    if industry and len(dates) >= 2:
        z1 = _theme_zt(industry, dates[-1])
        z0 = _theme_zt(industry, dates[-2])
        if z1 is not None and z0 is not None and z1 < TH_ZT and z0 < TH_ZT:
            return True, "失败", f"所属主题涨停数连续 2 日 < 3（{z0}→{z1}）"
    peak = max((_px(x, "high") or _px(x, "close") or 0) for x in bars[buy_i : last_i + 1])
    ret = close / buy_px - 1.0
    if ret >= WIN_PCT - 1e-12 and peak and (peak - close) / peak >= GIVEBACK - 1e-12:
        return True, "获利", f"相对买入价 {ret * 100:.1f}% ≥ 12%，且从持仓最高回撤 ≥ 5%"
    if last_i >= buy_i + 1 and _is_limit_bar(bars, last_i - 1, code) and not _is_limit_bar(bars, last_i, code):
        vols = [_vol(x) for x in bars[buy_i : last_i + 1]]
        ranked = sorted(vols, reverse=True)
        if vols and vols[-1] in ranked[:2]:
            ev = _cached_events(asof) or {}
            still = {ts_code(str(x.get("code") or "")) for x in (ev.get("limit_up") or [])}
            seal_gone = code not in still
            if ev.get("limit_up") is None:
                seal_gone = True
            if seal_gone:
                return True, "获利", "收盘涨停次日开板，量列买入以来前 2 名，封单消失"
    streak_now = _streak(bars, last_i, code)
    streak_prev = _streak(bars, last_i - 1, code) if last_i >= 1 else 0
    if streak_prev >= 3 and streak_now < 3:
        return True, "获利", "变成 3 连板后开板，本规则清"
    return False, "", ""


def _leaders_n(date: str, industry: str, quotes: dict, bars_by_code=None) -> int:
    n = 0
    events = _cached_events(date) or {}
    for rec in events.get("limit_up") or []:
        c = ts_code(str(rec.get("code") or ""))
        if industry_of(c) == industry:
            n += 1
    if n >= LEADERS:
        return n
    extra = 0
    for code, q in (quotes or {}).items():
        if industry_of(code) != industry:
            continue
        pct = _num(q.get("pct"))
        if pct is not None and pct >= LEAD_PCT * 100:
            extra += 1
    return max(n, extra)


class SosScan:
    funnel = []
    market = None
    sos = None


def _base_row(code: str, name: str) -> dict:
    return {
        "code": code,
        "name": name,
        "status": "排除",
        "gate": "排除",
        "summary_bucket": "排除",
        "path": "主题SOS补涨",
        "hit_rules": [],
        "missing_rules": [],
        "reminders": [],
        "veto": [],
        "risk": [],
        "facts": {},
        "fact_note": FACT_NOTE,
        "position_block": "总闸：排除 → 观察 → 试仓 → 持有 → 卖出。买入 = 试仓条件齐，不是下单。当日本规则新开 ≤ 3 只。",
        "path_ready": False,
        "data_ok": False,
        "key_kind": "买入价",
    }


def classify_sos(
    meta: dict,
    settings: dict,
    trades: list | None = None,
    quotes: dict | None = None,
    open_pos: dict | None = None,
    ctx: dict | None = None,
) -> dict:
    code = ts_code(str(meta.get("code") or ""))
    name = meta.get("name") or code
    base = _base_row(code, name)
    quotes = quotes if quotes is not None else load_quotes()
    bars = overlay_quote_bar(load_bars(code), code, quotes)
    ctx = ctx or {}
    if is_st_name(name) or meta.get("is_st"):
        base["missing_rules"].append("池子：ST / *ST")
        return base
    if _is_bj(code):
        base["missing_rules"].append("池子：北证")
        return base
    if len(bars) < L:
        base["missing_rules"].append(f"池子：日线不足 {L} 根")
        return base
    last_i = len(bars) - 1
    last = bars[last_i]
    asof = str(last.get("date") or "")[:10]
    base["data_ok"] = True
    base["facts"]["date"] = asof
    base["facts"]["close"] = last.get("close")
    base["industry"] = industry_of(code) or meta.get("industry") or ""
    env = ctx.get("env") or classify_env(market_stats_at(asof, None), asof)
    theme = ctx.get("theme_by_ind", {}).get(base["industry"]) or theme_state(base["industry"], asof)
    mainline = ctx.get("mainline") or {}
    base["facts"]["env"] = env.get("label")
    base["facts"]["theme"] = base["industry"]
    base["facts"]["theme_live"] = bool(theme.get("live"))
    base["facts"]["slot_1330"] = bool(_cached_snap(asof, SLOT_LIVE))
    base["facts"]["slot_1430"] = bool(_cached_snap(asof, SLOT_BUY_END))
    base["facts"]["slot_1500"] = bool(_cached_snap(asof, SLOT_FINAL))

    if open_pos:
        zone = {"industry": base["industry"], "buy_ma20": open_pos.get("buy_ma20")}
        hit, section, detail = evaluate_exit_sos(bars, open_pos, zone)
        if hit:
            base["status"] = "卖出"
            base["gate"] = "卖出"
            base["summary_bucket"] = "卖出"
            base["hit_rules"].append(f"卖出已见（{section}）：{detail}")
            return base
        base["status"] = "持有"
        base["gate"] = "持有"
        base["summary_bucket"] = "持有"
        base["path_ready"] = True
        buy_px = _num(open_pos.get("buy_price"))
        buy_low = _num(open_pos.get("buy_low")) or _px(bars[0], "low")
        buy_date = str(open_pos.get("buy_date") or "")[:10]
        buy_i = 0
        for i, row in enumerate(bars):
            if str(row.get("date") or "")[:10] >= buy_date:
                buy_i = i
                buy_low = _px(row, "low")
                break
        close = _px(last, "close")
        held = last_i - buy_i
        if (
            buy_px
            and close
            and close > buy_px
            and theme.get("live")
            and env.get("label") != "ENV_OFF"
            and buy_low
            and (_px(last, "low") or close) >= buy_low - 1e-12
            and held >= 1
        ):
            if now_sh().strftime("%H:%M") < SLOT_LIVE:
                base["hit_rules"].append("持有：浮盈且主题仍活、未破买入日低点，可在竞价或 13:30 前加一次，加到计划仓位的 70%。不再第三次加。")
            else:
                base["hit_rules"].append("持有：主题死或环境改 ENV_OFF 前可管理仓位。13:30 后不加仓。")
        else:
            if env.get("label") == "ENV_OFF" or theme.get("dead"):
                base["hit_rules"].append("持有：主题死或环境 ENV_OFF，停止加仓。已有仓按卖出规则管。")
        base["hit_rules"].append("持有：未到卖出。试仓不是成交指令。")
        return base

    if env.get("label") == "ENV_OFF":
        base["missing_rules"].append("否决：环境 ENV_OFF")
        base["veto"].append("环境 ENV_OFF")
        return base
    if mainline and base["industry"] != mainline.get("industry"):
        base["missing_rules"].append(f"只做一条主线。主线 {mainline.get('industry') or '无'}，本票 {base['industry'] or '无主题'} 排除")
        return base
    if theme.get("dead") or not theme.get("live"):
        base["missing_rules"].append("否决：主题已死" if theme.get("dead") else "主题未活")
        if theme.get("dead"):
            base["veto"].append("主题已死")
        return base
    if code in (ctx.get("other_holds") or set()):
        base["missing_rules"].append("否决：与 RULES1–4 已持仓同一只票，不得本规则再开一笔")
        base["veto"].append("与 RULES1–4 已持仓同一只票")
        return base

    t1_i = last_i - 1 if last_i >= 1 else last_i
    t1_date = str(bars[t1_i].get("date") or "")[:10]
    px_final, found_final, _src = _price_view(bars, t1_i, code, SLOT_FINAL)
    fails = pool_fail(bars, t1_i, code, name, meta, px_final if found_final else None, found_final)
    kick = _kick_final(bars, t1_i, code, theme_state(base["industry"], t1_date), px_final if found_final else bars[t1_i], found_final)
    in_final = not fails and not kick
    if kick:
        fails.append(kick)
    if not in_final:
        base["missing_rules"].extend(["池子：" + x for x in fails] or ["不在 T-1 观察池"])
        return base
    if _streak(bars, t1_i, code) >= 2:
        base["missing_rules"].append("龙头 2 板可进持仓，不进新开")
        return base

    ev = _cached_events(asof) or {}
    fail_codes = {ts_code(str(x.get("code") or "")) for x in (ev.get("fail") or [])}
    zt_codes = {ts_code(str(x.get("code") or "")) for x in (ev.get("limit_up") or [])}
    if code in fail_codes and code not in zt_codes and (base["facts"]["slot_1330"] or base["facts"]["slot_1430"]):
        base["missing_rules"].append("否决：炸板后 30 分钟不回封")
        base["veto"].append("炸板后 30 分钟不回封")
        return base
    r5 = _ret_n(bars, last_i, 5)
    if r5 is not None and r5 >= 0.40 and _touch_limit(bars, last_i, code):
        base["missing_rules"].append("否决：高位接力（近 5 日涨幅 ≥ 40% 且当日再冲板）")
        base["veto"].append("高位接力")
        return base

    in_window = bool(ctx.get("in_window"))
    if ctx.get("in_window") is None:
        in_window = _in_buy_window()
    after = _after_buy_window() if ctx.get("after_window") is None else bool(ctx.get("after_window"))
    base["status"] = "观察"
    base["gate"] = "观察"
    base["summary_bucket"] = "观察"
    base["hit_rules"].append("已在 POOL_FINAL，主题仍活，环境不是 OFF。13:30 前即使分时翻红，只许观察。")
    if after and not in_window:
        base["missing_rules"].append("T 日 14:30 后禁止新开")
        return base
    if not in_window:
        base["missing_rules"].append("T 日 13:30–14:30 才允许 BUY_A / BUY_B")
        return base

    px_row, slot_found, src = _price_view(bars, last_i, code, SLOT_LIVE)
    if not slot_found:
        px_row, slot_found, src = _price_view(bars, last_i, code, SLOT_BUY_END)
    if not slot_found:
        px_row, slot_found, src = bars[last_i], False, "close"
        base["facts"]["slot_fallback"] = "close"
    minutes = buy_minutes(src)
    theme_now = theme_state(base["industry"], asof, slot=SLOT_LIVE if src == SLOT_LIVE else None)
    if src != SLOT_LIVE:
        theme_now = theme
    leaders = int(ctx.get("leaders") or 0)
    ok_a, hit_a, miss_a = evaluate_buy_a(bars, last_i, code, px_row, minutes, theme_now, slot_found)
    ok_b, hit_b, miss_b = evaluate_buy_b(bars, last_i, code, px_row, minutes, theme_now, leaders, env.get("label") or "")
    if ok_a:
        if env.get("label") == "ENV_WEAK" and ctx.get("buy_a_weak_full"):
            base["missing_rules"].append("ENV_WEAK 只许半仓 BUY_A，名额已满")
            return base
        base["status"] = "试仓"
        base["gate"] = "试仓"
        base["summary_bucket"] = "试仓"
        base["path_ready"] = True
        base["facts"]["buy_kind"] = "BUY_A"
        base["facts"]["buy_ma20"] = _ma20(bars, last_i)
        base["key_price"] = _px(px_row, "close")
        base["hit_rules"].extend(hit_a)
        size = SIZE_A_WEAK if env.get("label") == "ENV_WEAK" else SIZE_A_OK
        base["hit_rules"].append(f"BUY_A（SOS / 首板或突破）。仓位 {size}。试仓不是成交指令。")
        return base
    if ok_b:
        base["status"] = "试仓"
        base["gate"] = "试仓"
        base["summary_bucket"] = "试仓"
        base["path_ready"] = True
        base["facts"]["buy_kind"] = "BUY_B"
        base["facts"]["buy_ma20"] = _ma20(bars, last_i)
        base["key_price"] = _px(px_row, "close")
        base["hit_rules"].extend(hit_b)
        base["hit_rules"].append(f"BUY_B（补涨）。仓位 {SIZE_B}。试仓不是成交指令。")
        return base
    base["missing_rules"].extend(miss_a[:3])
    if env.get("label") == "ENV_OK":
        base["missing_rules"].extend(miss_b[:2])
    return base


def _build_ctx(quotes: dict, asof: str, live: bool = True) -> dict:
    ensure_index_daily()
    _amount_map()
    stats_close = market_stats_at(asof, None)
    stats_1330 = market_stats_at(asof, SLOT_LIVE)
    env = classify_env(stats_close, asof, draft_1330=False)
    env_1330 = classify_env(stats_1330, asof, draft_1330=True)
    mainline = pick_mainline(asof, slot=SLOT_LIVE if _cached_snap(asof, SLOT_LIVE) else None)
    if mainline is None:
        mainline = pick_mainline(asof, slot=None)
    theme_by_ind = {}
    if mainline:
        theme_by_ind[mainline["industry"]] = mainline
    leaders = 0
    if mainline:
        leaders = _leaders_n(asof, mainline["industry"], quotes)
    in_window = _in_buy_window() if live else True
    after = _after_buy_window() if live else False
    ctx = {
        "asof": asof,
        "env": env,
        "env_1330": env_1330,
        "mainline": mainline,
        "theme_by_ind": theme_by_ind,
        "leaders": leaders,
        "other_holds": other_rules_holds(),
        "in_window": in_window,
        "after_window": after,
        "stats": stats_close,
        "stats_1330": stats_1330,
    }
    SosScan.sos = {
        "env": env.get("label"),
        "env_1330": env_1330.get("label"),
        "env_why": env.get("why"),
        "mainline": (mainline or {}).get("industry") or "",
        "mainline_why": (mainline or {}).get("why") or "没有活主线",
        "leaders": leaders,
        "slot_1330": bool(_cached_snap(asof, SLOT_LIVE)),
        "slot_1430": bool(_cached_snap(asof, SLOT_BUY_END)),
        "slot_1500": bool(_cached_snap(asof, SLOT_FINAL)),
        "in_window": in_window,
        "note": "缺档字段空着，不拿更晚的快照填更早的档。回测缺档按收盘价继续。",
    }
    SosScan.market = {"env": env.get("label"), "mainline": (mainline or {}).get("industry")}
    SosScan.funnel = [mainline] if mainline else []
    return ctx


def classify_one_sos(code: str, settings: dict, trades: list | None = None) -> dict:
    code = ts_code(code)
    uni = {ts_code(str(x.get("code") or "")): x for x in load_universe()}
    quotes = load_quotes()
    q = quotes.get(code) or {}
    meta = dict(uni.get(code) or {})
    meta["code"] = code
    meta["name"] = meta.get("name") or q.get("name") or code
    open_pos = None
    try:
        from .buy_log import load_buy_log

        for item in load_buy_log("rules5").get("items") or []:
            if item.get("closed"):
                continue
            if ts_code(str(item.get("code") or "")) == code:
                open_pos = item
                break
    except Exception:
        pass
    asof = session_trading_date().isoformat()
    ctx = _build_ctx(quotes, asof, live=True)
    return classify_sos(meta, settings, trades, quotes=quotes, open_pos=open_pos, ctx=ctx)


def scan_sos(settings: dict, trades: list | None = None) -> list[dict]:
    from .eastmoney import hydrate_universe

    items = hydrate_universe()
    quotes = load_quotes()
    asof = session_trading_date().isoformat()
    ctx = _build_ctx(quotes, asof, live=True)
    opens = {}
    try:
        from .buy_log import load_buy_log

        for item in load_buy_log("rules5").get("items") or []:
            if item.get("closed"):
                continue
            opens[ts_code(str(item.get("code") or ""))] = item
    except Exception:
        opens = {}
    rows = [
        classify_sos(
            item,
            settings,
            trades,
            quotes=quotes,
            open_pos=opens.get(ts_code(str(item.get("code") or ""))),
            ctx=ctx,
        )
        for item in items
    ]
    trials = [r for r in rows if r.get("status") == "试仓"]
    trials.sort(key=lambda r: (0 if (r.get("facts") or {}).get("buy_kind") == "BUY_A" else 1, r.get("code")))
    keep = {r["code"] for r in trials[:OPEN_MAX]}
    extra = 0
    for row in rows:
        if row.get("status") == "试仓" and row["code"] not in keep:
            extra += 1
            row["status"] = "观察"
            row["gate"] = "观察"
            row["summary_bucket"] = "观察"
            row["path_ready"] = False
            row["missing_rules"] = list(row.get("missing_rules") or []) + ["当日全账户本规则新开 ≤ 3 只"]
    if SosScan.sos is not None:
        SosScan.sos["trial_capped"] = extra
        SosScan.sos["trial_n"] = len(keep)
    order = {name: i for i, name in enumerate(GATES_SOS)}
    rows.sort(key=lambda item: (order.get(item["status"], 9), item.get("industry") or "", item["code"]))
    return rows


def list_sos_cycle_universe() -> list[dict]:
    uni = {ts_code(str(x.get("code") or "")): x for x in load_universe()}
    out = []
    for path in CSV_DIR.glob("*.csv"):
        code = ts_code(path.stem)
        if not code or _is_bj(code):
            continue
        last = peek_last_bar(code)
        if not last or last.get("close") is None:
            continue
        name = last.get("name") or (uni.get(code) or {}).get("name") or code
        if is_st_name(name):
            continue
        out.append({"code": code, "name": name})
    return out


def is_buy_sos(bars: list[dict], ctx: dict | None = None) -> bool:
    """Backtest: 13:30/14:30 snapshot if present, else close. Never reads a later slot."""
    if not bars or len(bars) < L + 2:
        return False
    ctx = ctx or {}
    code = ts_code(str(bars[-1].get("code") or ctx.get("code") or ""))
    i = len(bars) - 1
    t1 = i - 1
    asof = str(bars[i].get("date") or "")[:10]
    t1_date = str(bars[t1].get("date") or "")[:10]
    industry = industry_of(code) or str(ctx.get("industry") or "")
    env = classify_env(market_stats_at(asof, None), asof)
    if env.get("label") == "ENV_OFF":
        return False
    theme_t1 = theme_state(industry, t1_date)
    if not theme_t1.get("live"):
        return False
    px_final, found_final, _ = _price_view(bars, t1, code, SLOT_FINAL)
    if pool_fail(bars, t1, code, str(ctx.get("name") or code), ctx, px_final if found_final else None, found_final):
        return False
    kick = _kick_final(bars, t1, code, theme_t1, px_final if found_final else bars[t1], found_final)
    if kick:
        return False
    if _streak(bars, t1, code) >= 2:
        return False
    theme_t = theme_state(industry, asof, slot=SLOT_LIVE if _cached_snap(asof, SLOT_LIVE) else None)
    if theme_t.get("dead") or not theme_t.get("live"):
        return False
    px_row, slot_found, src = _price_view(bars, i, code, SLOT_LIVE)
    if not slot_found:
        px_row, slot_found, src = _price_view(bars, i, code, SLOT_BUY_END)
    if not slot_found:
        px_row, slot_found, src = bars[i], False, "close"
    minutes = buy_minutes(src)
    leaders = _leaders_n(asof, industry, {code: px_row})
    ok_a, _, _ = evaluate_buy_a(bars, i, code, px_row, minutes, theme_t, slot_found)
    if ok_a:
        return True
    if env.get("label") != "ENV_OK":
        return False
    ok_b, _, _ = evaluate_buy_b(bars, i, code, px_row, minutes, theme_t, leaders, env.get("label"))
    return ok_b


def walk_cycles_sos(bars: list[dict], ctx: dict | None = None) -> tuple[list[dict], dict | None]:
    from .cycles import _cycle_stats

    ctx = dict(ctx or {})
    n = len(bars)
    if n < L + 5:
        return [], None
    code = ts_code(str(ctx.get("code") or bars[-1].get("code") or ""))
    ctx["code"] = code
    ctx["industry"] = industry_of(code) or ""
    cycles = []
    open_i = None
    zone = None
    for i in range(L + 1, n):
        sl = bars[: i + 1]
        if open_i is None:
            if is_buy_sos(sl, ctx):
                open_i = i
                zone = {
                    "industry": ctx.get("industry"),
                    "buy_ma20": _ma20(sl, i),
                    "slot_1330": bool(_cached_snap(str(bars[i].get("date") or "")[:10], SLOT_LIVE)),
                    "slot_1430": bool(_cached_snap(str(bars[i].get("date") or "")[:10], SLOT_BUY_END)),
                }
            continue
        pos = {
            "buy_date": bars[open_i].get("date"),
            "code": code,
            "buy_price": bars[open_i].get("close"),
        }
        hit, section, detail = evaluate_exit_sos(sl, pos, zone)
        if i > open_i and hit:
            cycles.append(_cycle_stats(bars, open_i, i, exit_section=section, exit_detail=detail))
            open_i = None
            zone = None
    live = None
    if open_i is not None:
        live = _cycle_stats(bars, open_i, n - 1, closed=False)
    return cycles, live
