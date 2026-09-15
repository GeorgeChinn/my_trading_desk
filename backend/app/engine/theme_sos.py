"""RULES5 利弗莫尔领先股 × 威科夫 SOS/LPS。数字和句子只来自 RULES5.MD。

可买前缀 000/001/002/003/600/601/603/605；排除 300/688/北证/ST。
T-1 硬条件入池，T 日在池内判 BUY_A / BUY_B。禁止双均线一票否决。
13:30 / 14:30 / 15:00 有快照用快照；缺档字段空着，回测按收盘价继续。禁止用更晚档填更早档。
回测不扫全市场 ~5000；按交易日只对可买范围内 T-1 过硬条件的票判信号。
"""
from __future__ import annotations

from datetime import datetime

from ..config import CSV_DIR, GATES_SOS, INDEX_DAILY_PATH, MARKET_AMOUNT_PATH
from ..store import load_quotes, load_universe, read_json, write_json
from .bars import bar_amount, load_bars, overlay_quote_bar, ts_code
from .boards import industry_of, is_limit_up, load_board_daily
from .clock import now_sh, session_trading_date
from .pool import is_st_name
from .scanner import FACT_NOTE

L = 40
LISTED_DAYS = 60
PRICE_MIN = 3.0
MCAP_LO = 20.0
MCAP_HI = 800.0
MA20_BAND = 0.97
DD60 = 0.40
TURN3 = 25.0
TURN_CLIMAX = 30.0
TURN_LIMIT = 18.0
POOL_MAX = 20
THEME_MAX = 3
A_PCT_MIN = 6.0
A_VOL_RATIO = 1.2
B_VOL_RATIO_MAX = 0.85
B_TURN_RATIO = 0.85
B_TURN_ABS = 5.0
B1_DD20_LO = 0.03
B1_DD20_HI = 0.18
B_PCT_LO = -3.0
B_PCT_HI = 3.0
B2_DD20_MAX = 0.02
B2_PCT_LO = 0.5
B2_PCT_HI = 4.0
B_BREAK_VOL = 1.3
STOP_MA = 0.97
GIVEBACK = 0.08
ZT_SOS_MIN = 40
SEAL_SOS_MIN = 0.60
CYB_SOS_PCT = 2.0
UP_SHARE_OFF = 0.35
DT_OFF_MIN = 15
IDX_BREAK = 0.98
ZT_WEAK = 50
SEAL_WEAK = 0.55
TH1_MIN = 3
TH3_MIN = 5
STREAK_POOL_BAN = 3
SIZE_A = "10%"
SIZE_A_WEAK = "5%"
SIZE_B = "7%"
BUYABLE_PREFIX = ("000", "001", "002", "003", "600", "601", "603", "605")
SLOT_LIVE = "13:30"
SLOT_BUY_END = "14:30"
SLOT_FINAL = "15:00"
CALIB_9_7 = (
    ("603042", "华脉科技", "BUY_A"),
    ("000759", "中百集团", "BUY_A"),
    ("603186", "华正新材", "BUY_A"),
    ("000523", "红棉股份", "BUY_A"),
    ("002790", "瑞尔特", "BUY_B1"),
    ("002349", "精华制药", "BUY_B1"),
    ("605198", "安德利", "BUY_B1"),
    ("605077", "华康股份", "BUY_B2"),
)

_SNAP: dict[tuple[str, str], dict | None] = {}
_EVENTS: dict[str, dict | None] = {}
_AMT: dict[str, float] = {}
_INDEX: dict[str, dict[str, float]] = {}
_BOARD: dict | None = None
_IND: dict[str, str] = {}
_RANKS: dict[str, dict] = {}


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
    xs = [x for x in xs if x is not None]
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


def buyable(code: str, name: str = "", meta: dict | None = None) -> bool:
    c = ts_code(code)
    if len(c) < 6 or not c.startswith(BUYABLE_PREFIX):
        return False
    if c.startswith(("300", "688")):
        return False
    if _is_bj(c):
        return False
    if is_st_name(name) or (meta or {}).get("is_st"):
        return False
    return True


def _yi_zi(row: dict | None) -> bool:
    if not row:
        return False
    o, h, l, c = row.get("open"), row.get("high"), row.get("low"), row.get("close")
    if None in (o, h, l, c):
        return False
    return abs(h - l) <= 1e-9 and abs(c - o) <= 1e-9


def _is_limit_bar(bars: list[dict], i: int, code: str, row: dict | None = None) -> bool:
    if i < 1:
        return False
    close = _px(row or bars[i], "close")
    return is_limit_up(bars[i - 1].get("close"), close, code)


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
    if amt is None:
        amt = bar_amount(row)
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


def _index_pct(sym: str, date: str):
    series = (ensure_index_daily(fetch=False).get(sym) or {})
    dates = [d for d in sorted(series) if d <= date]
    if len(dates) < 2 or dates[-1] != date:
        return None
    prev, last = series.get(dates[-2]), series.get(dates[-1])
    if not prev or not last:
        return None
    return (last / prev - 1.0) * 100.0


def _seal_frac(seal) -> float | None:
    if seal is None:
        return None
    val = float(seal)
    if val > 1.5:
        return val / 100.0
    return val


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
    if _AMT and (not rebuild or len(_AMT) >= 5):
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
    sh_c, sh_ma = _index_ma20("sh000001", date)
    cy_c, cy_ma = _index_ma20("sz399006", date)
    e_index = bool(
        (sh_c is not None and sh_ma is not None and sh_c >= sh_ma - 1e-12)
        or (cy_c is not None and cy_ma is not None and cy_c >= cy_ma - 1e-12)
    )
    zt = stats.get("limit_up_n")
    seal_f = _seal_frac(stats.get("seal_rate"))
    up_n = stats.get("up_n")
    down_n = stats.get("down_n")
    dt = stats.get("limit_down_n") or 0
    cy_pct = _index_pct("sz399006", date)
    up_gt_down = up_n is not None and down_n is not None and up_n > down_n
    e_sos = bool(
        (zt is not None and seal_f is not None and zt >= ZT_SOS_MIN and seal_f >= SEAL_SOS_MIN and up_gt_down)
        or (cy_pct is not None and cy_pct >= CYB_SOS_PCT and up_gt_down)
    )
    tot = (up_n or 0) + (down_n or 0)
    up_share = (up_n / tot) if tot and up_n is not None else None
    e_off = bool(
        up_share is not None
        and up_share < UP_SHARE_OFF
        and dt >= DT_OFF_MIN
        and sh_c is not None
        and sh_ma is not None
        and sh_c < sh_ma * IDX_BREAK
        and cy_c is not None
        and cy_ma is not None
        and cy_c < cy_ma * IDX_BREAK
    )
    why = [
        f"E_index={'是' if e_index else '否'} 上证 {sh_c}/{sh_ma} 创业板 {cy_c}/{cy_ma}",
        f"E_sos={'是' if e_sos else '否'} 涨停 {zt} 封板 {None if seal_f is None else round(seal_f * 100, 1)}% 创业板 {None if cy_pct is None else round(cy_pct, 2)}%",
        f"ENV_OFF闸={'是' if e_off else '否'}",
    ]
    if e_off or not (e_index or e_sos):
        label = "ENV_OFF"
    elif zt is not None and (zt < ZT_WEAK or (seal_f is not None and seal_f < SEAL_WEAK)):
        label = "ENV_WEAK"
    else:
        label = "ENV_OK"
    return {
        "label": label,
        "e_index": e_index,
        "e_sos": e_sos,
        "e1": e_index,
        "e2": e_sos,
        "why": why,
        "zt": zt,
        "draft": draft_1330,
        "cy_pct": None if cy_pct is None else round(cy_pct, 2),
        "seal": None if seal_f is None else round(seal_f * 100.0, 1),
    }


def _board_blob() -> dict:
    global _BOARD
    if _BOARD is None:
        _BOARD = load_board_daily() or {}
    return _BOARD


def _industry(code: str) -> str:
    c = ts_code(code)
    if not _IND:
        from .boards import load_industry_blob, sw_maps

        sw1, sw2 = sw_maps()
        blob = load_industry_blob()
        codes = blob.get("codes") if isinstance(blob.get("codes"), dict) else {}
        for src in (codes, sw1, sw2):
            for k, v in (src or {}).items():
                if v:
                    _IND[ts_code(str(k))] = str(v)
        for k, v in (sw2 or {}).items():
            if v:
                _IND[ts_code(str(k))] = str(v)
    return _IND.get(c) or industry_of(c) or ""


def _board_row(industry: str | None, date: str) -> dict:
    if not industry:
        return {}
    rec = (_board_blob().get("boards") or {}).get(industry) or {}
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
            if not isinstance(rec, dict) or _industry(code) != industry:
                continue
            if _is_limit_quote(code, rec):
                n += 1
        return n
    events = _cached_events(date) or {}
    pool = events.get("limit_up") if isinstance(events, dict) else None
    if pool:
        return sum(1 for rec in pool if _industry(str(rec.get("code") or "")) == industry)
    row = _board_row(industry, date)
    if not row:
        return None
    return int(row.get("limit_ups") or 0)


def _theme_ret(industry: str | None, date: str) -> float | None:
    row = _board_row(industry, date)
    if not row:
        return None
    return _num(row.get("ret_1d"))


def _rank_map(date: str) -> dict[str, tuple]:
    if date in _RANKS:
        return _RANKS[date]
    rets = []
    for name, rec in (_board_blob().get("boards") or {}).items():
        row = (rec.get("by_date") or {}).get(date) or {}
        r = _num(row.get("ret_1d"))
        if r is None:
            continue
        rets.append((r, name))
    rets.sort(reverse=True)
    n = len(rets) or 1
    out = {}
    for i, (r, name) in enumerate(rets, start=1):
        out[name] = (i, r, i / n <= 0.15)
    _RANKS[date] = out
    return out


def theme_state(industry: str | None, date: str, slot: str | None = None) -> dict:
    if not industry:
        return {"live": False, "dead": True, "why": "无主题", "industry": ""}
    zt = _theme_zt(industry, date, slot=slot)
    ret = _theme_ret(industry, date)
    dates = _trading_dates(date, 4)
    counts = []
    for d in dates:
        n = _theme_zt(industry, d)
        counts.append(0 if n is None else n)
    th3_sum = sum(counts[-3:]) if counts else 0
    th1 = zt is not None and zt >= TH1_MIN
    th3 = th3_sum >= TH3_MIN
    live = bool(th1 or th3)
    dead = (not live) and zt is not None and zt < TH1_MIN and ret is not None and ret < 0
    why = f"{industry} 涨停 {zt} · 近3日涨停 {th3_sum} · 当天可点火"
    return {
        "live": live,
        "dead": bool(dead),
        "th1": th1,
        "th3": th3,
        "zt": zt,
        "ret": ret,
        "industry": industry,
        "why": why,
        "slot": slot,
        "slot_found": bool(slot and _cached_snap(date, slot)),
    }


def pick_themes(date: str, slot: str | None = None, n: int = THEME_MAX) -> list[dict]:
    ranks = _rank_map(date)
    live = []
    for name in ranks:
        st = theme_state(name, date, slot=slot)
        if not st.get("live"):
            continue
        live.append(st)
    live.sort(key=lambda s: (s.get("zt") or 0, s.get("ret") or -999), reverse=True)
    return live[:n]


def pick_mainline(date: str, slot: str | None = None) -> dict | None:
    themes = pick_themes(date, slot=slot, n=1)
    return themes[0] if themes else None


def _hhv(bars: list[dict], i: int, n: int):
    lo = max(0, i - n + 1)
    highs = [_px(bars[j], "high") for j in range(lo, i + 1)]
    highs = [x for x in highs if x]
    return max(highs) if highs else None


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


def _vol_ratio_5d(bars: list[dict], i: int, row: dict | None = None, minutes: int = 240):
    if i < 5:
        return None
    prev = [_vol(bars[j]) for j in range(i - 5, i)]
    avg = _mean([v for v in prev if v > 0])
    today = _vol(row or bars[i])
    if not avg or avg <= 0 or today <= 0:
        return None
    if minutes and 0 < minutes < 240:
        today = today / (minutes / 240.0)
    return today / avg


def _mean_turn(bars: list[dict], lo: int, hi: int, mcap) -> float | None:
    xs = []
    for j in range(max(0, lo), hi + 1):
        t = _turnover(bars[j], mcap)
        if t is not None:
            xs.append(t)
    return _mean(xs) if xs else None


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
    if not buyable(code, name, meta):
        if is_st_name(name) or (meta or {}).get("is_st"):
            fail.append("ST")
        elif ts_code(code).startswith("300"):
            fail.append("创业板")
        elif ts_code(code).startswith("688"):
            fail.append("科创板")
        elif _is_bj(code):
            fail.append("北证")
        else:
            fail.append("不在可买前缀")
        return fail
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
        fail.append(f"现价未站上 MA20×0.97（{ma * MA20_BAND:.2f}）")
    elif close is not None and ma is None:
        fail.append("MA20 不足")
    hh = _hhv(bars, i, 60)
    dd = ((hh - close) / hh) if hh and close else None
    if dd is None:
        fail.append("60日高回撤证据不足")
    elif dd > DD60 + 1e-12:
        fail.append(f"距近 60 日高回撤 {dd * 100:.1f}% > 40%")
    t3 = _mean_turn(bars, i - 2, i, mcap)
    if t3 is not None and t3 > TURN3:
        fail.append(f"近 3 日换手均值 {t3:.1f}% > 25%")
    streak = _streak(bars, i, code)
    if streak >= STREAK_POOL_BAN:
        fail.append("已 3 连板及以上")
    t1 = _turnover(row, mcap)
    if _is_limit_bar(bars, i, code, row) and t1 is not None and t1 > TURN_LIMIT:
        fail.append("当日已涨停且换手 > 18%，次日不得新开")
    return fail


def loc_upper_third(row: dict) -> bool:
    close, high, low = _px(row, "close"), _px(row, "high"), _px(row, "low")
    if close is None or high is None or low is None:
        return False
    return close >= high - (high - low) / 3.0 - 1e-12


def _pct(bars: list[dict], i: int, row: dict | None = None):
    close = _px(row or bars[i], "close")
    pre = _px(bars[i - 1], "close") if i >= 1 else None
    if close is None or not pre:
        return None
    return (close / pre - 1.0) * 100.0


def vol_ok_A(lu: bool, vr) -> bool:
    return bool(lu or (vr is not None and vr >= A_VOL_RATIO - 1e-12))


def vol_ok_B(vr, turn, turn5) -> tuple[bool, str]:
    if vr is not None and vr <= B_VOL_RATIO_MAX + 1e-12:
        return True, f"量比 {vr:.2f} ≤ 0.85"
    if turn is not None and turn5 and turn <= turn5 * B_TURN_RATIO + 1e-12:
        return True, f"换手 {turn:.2f}% ≤ 5日均 {turn5:.2f}% × 0.85"
    if turn is not None and turn <= B_TURN_ABS + 1e-12:
        return True, f"换手 {turn:.2f}% ≤ 5%"
    return False, "量能未缩（量比>0.85 且换手未≤5日均×0.85 且换手>5%）"


def evaluate_buy_a(
    bars: list[dict],
    i: int,
    code: str,
    row: dict,
    minutes: int,
    theme: dict,
    slot_found: bool,
) -> tuple[bool, list[str], list[str]]:
    hit, miss = [], []
    pct = _pct(bars, i, row)
    lu = _is_limit_bar(bars, i, code, row)
    if (pct is not None and pct >= A_PCT_MIN - 1e-12) or lu:
        hit.append("涨停" if lu else f"涨幅 {pct:.2f}% ≥ 6%")
    else:
        miss.append(f"涨幅未达 6% 且未涨停（{None if pct is None else round(pct, 2)}%）")
    vr = _vol_ratio_5d(bars, i, row, minutes=minutes or 240)
    if vol_ok_A(lu, vr):
        if lu:
            hit.append(f"涨停视为量能满足（量比 {None if vr is None else round(vr, 2)}）")
        else:
            hit.append(f"量比 {vr:.2f} ≥ 1.2")
    else:
        miss.append(f"量比 {None if vr is None else round(vr, 2)} < 1.2 且未涨停")
    if loc_upper_third(row):
        hit.append("收盘在当日振幅上 1/3")
    else:
        miss.append("收盘不在当日振幅上 1/3")
    self_sos = ((pct is not None and pct >= A_PCT_MIN - 1e-12) or lu) and vol_ok_A(lu, vr)
    if theme.get("live") or self_sos:
        hit.append("主题活" if theme.get("live") else "个股自身 SOS（当天可点火）")
    else:
        miss.append("主题未活且自身 SOS 不足")
    return (not miss), hit, miss


def evaluate_buy_b(
    bars: list[dict],
    i: int,
    code: str,
    row: dict,
    minutes: int,
    theme: dict,
    leaders_n: int,
    env_label: str,
    mcap=None,
) -> tuple[bool, list[str], list[str], str]:
    hit, miss = [], []
    kind = ""
    close = _px(row, "close")
    ma = _ma20(bars, i)
    if close is None or ma is None or close < ma - 1e-12:
        miss.append("收盘未站上 MA20")
        return False, hit, miss, kind
    hit.append("收盘 ≥ MA20")
    vr = _vol_ratio_5d(bars, i, row, minutes=minutes or 240)
    turn = _turnover(row, mcap)
    turn5 = _mean_turn(bars, i - 5, i - 1, mcap)
    ok_v, why_v = vol_ok_B(vr, turn, turn5)
    if ok_v:
        hit.append(why_v)
    else:
        miss.append(why_v)
    if close < ma * MA20_BAND - 1e-12 and vr is not None and vr >= B_BREAK_VOL - 1e-12:
        miss.append("回踩失败：收盘 < MA20×0.97 且量比 ≥ 1.3")
        return False, hit, miss, kind
    hh20 = _hhv(bars, i, 20)
    dd20 = ((hh20 - close) / hh20) if hh20 and close else None
    pct = _pct(bars, i, row)
    b1 = (
        dd20 is not None
        and B1_DD20_LO - 1e-12 <= dd20 <= B1_DD20_HI + 1e-12
        and pct is not None
        and B_PCT_LO - 1e-12 <= pct <= B_PCT_HI + 1e-12
    )
    b2 = (
        dd20 is not None
        and dd20 <= B2_DD20_MAX + 1e-12
        and pct is not None
        and B2_PCT_LO - 1e-12 <= pct <= B2_PCT_HI + 1e-12
    )
    if b1:
        kind = "BUY_B1"
        hit.append(f"B1 回踩 dd20 {dd20 * 100:.1f}% 涨跌 {pct:.2f}%")
    elif b2:
        kind = "BUY_B2"
        hit.append(f"B2 爬升 dd20 {dd20 * 100:.2f}% 涨幅 {pct:.2f}%")
    else:
        miss.append(
            f"非 B1/B2（dd20={None if dd20 is None else round(dd20 * 100, 2)}% pct={None if pct is None else round(pct, 2)}%）"
        )
    return (not miss), hit, miss, kind


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
    industry = (zone or {}).get("industry") or _industry(code)
    asof = str(last.get("date") or "")[:10]
    ma = _ma20(bars, last_i)
    vr = _vol_ratio_5d(bars, last_i, last, minutes=240)
    if ma and close < ma * STOP_MA - 1e-12 and vr is not None and vr >= B_BREAK_VOL - 1e-12:
        return True, "失败", f"收盘 {close:.2f} < MA20×0.97 且量比 {vr:.2f} ≥ 1.3"
    peak = max((_px(x, "high") or _px(x, "close") or 0) for x in bars[buy_i : last_i + 1])
    if peak and (peak - close) / peak >= GIVEBACK - 1e-12:
        return True, "失败", f"从持仓最高回撤 {(peak - close) / peak * 100:.1f}% ≥ 8%"
    if industry:
        dates = _trading_dates(asof, 12)
        zts = [_theme_zt(industry, d) for d in dates]
        zts = [0 if z is None else z for z in zts]
        peak_z = max(zts) if zts else 0
        today_z = zts[-1] if zts else 0
        yin = _px(last, "close") is not None and _px(last, "open") is not None and _px(last, "close") < _px(last, "open")
        if peak_z >= 4 and today_z <= peak_z / 2 + 1e-12 and vr is not None and vr >= 1.5 and yin:
            return True, "失败", f"主题涨停从峰值 {peak_z} 腰斩到 {today_z}，量比 {vr:.2f} 收阴"
    env = classify_env(market_stats_at(asof, None), asof)
    if env.get("label") == "ENV_OFF":
        kind = str((zone or {}).get("buy_kind") or open_pos.get("buy_kind") or "")
        if kind.startswith("BUY_B"):
            return True, "失败", "ENV_OFF 当日 B 类全出"
        if kind.startswith("BUY_A") and not _is_limit_bar(bars, last_i, code):
            return True, "失败", "ENV_OFF 当日 A 类未封死减半/出"
    return False, "", ""


def _pct_vs(px, pre):
    if px is None or not pre:
        return None
    return round((px / pre - 1.0) * 100.0, 2)


def _slot_line(code: str, date: str, slot: str, title: str, role: str, pre) -> dict:
    q = slot_quote(date, slot, code)
    if not q:
        return {
            "title": title,
            "role": role,
            "date": date,
            "slot": slot,
            "found": False,
            "source": "",
            "price": None,
            "pct": None,
            "open": None,
            "high": None,
            "low": None,
        }
    px = _px(q, "close")
    pct = _num(q.get("pct"))
    if pct is None:
        pct = _pct_vs(px, pre)
    else:
        pct = round(pct, 2)
    return {
        "title": title,
        "role": role,
        "date": date,
        "slot": slot,
        "found": True,
        "source": slot,
        "price": px,
        "pct": pct,
        "open": _px(q, "open"),
        "high": _px(q, "high"),
        "low": _px(q, "low"),
    }


def _close_line(row: dict | None, date: str, title: str, role: str, pre) -> dict:
    px = _px(row, "close")
    return {
        "title": title,
        "role": role,
        "date": date,
        "slot": "close",
        "found": px is not None,
        "source": "close",
        "price": px,
        "pct": _pct_vs(px, pre),
        "open": _px(row, "open"),
        "high": _px(row, "high"),
        "low": _px(row, "low"),
    }


def build_timeline(code: str, bars: list[dict], t_i: int, t1_i: int) -> list[dict]:
    """T 时序价格/涨幅。缺档为空，禁止用更晚档填更早档。收盘另列，供缺档回测续走。"""
    t_date = str(bars[t_i].get("date") or "")[:10]
    t1_date = str(bars[t1_i].get("date") or "")[:10]
    pre_t1 = _px(bars[t1_i - 1], "close") if t1_i >= 1 else None
    pre_t = _px(bars[t1_i], "close")
    return [
        _slot_line(code, t1_date, SLOT_LIVE, "T-1 13:30", "POOL_LIVE 草稿", pre_t1),
        _slot_line(code, t1_date, SLOT_FINAL, "T-1 15:00", "POOL_FINAL", pre_t1),
        _close_line(bars[t1_i], t1_date, "T-1 收盘", "缺档时回测用此收盘", pre_t1),
        _slot_line(code, t_date, SLOT_LIVE, "T 13:30", "刷新主题 / 试仓窗开", pre_t),
        _slot_line(code, t_date, SLOT_BUY_END, "T 14:30", "试仓窗关，此后禁止新开", pre_t),
        _slot_line(code, t_date, SLOT_FINAL, "T 15:00", "收盘档", pre_t),
        _close_line(bars[t_i], t_date, "T 收盘", "缺档时回测用此收盘", pre_t),
    ]


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
        "path": "RULES5 SOS/LPS",
        "hit_rules": [],
        "missing_rules": [],
        "reminders": [],
        "veto": [],
        "risk": [],
        "facts": {},
        "fact_note": FACT_NOTE,
        "position_block": "总闸：排除 → 观察 → 试仓 → 持有 → 卖出。买入 = 试仓条件齐，不是下单。",
        "path_ready": False,
        "data_ok": False,
        "key_kind": "买入价",
    }


def _pool_rank_key(bars: list[dict], i: int, mcap) -> tuple:
    close = _px(bars[i], "close")
    ma = _ma20(bars, i)
    dist = abs(close / ma - 1.0) if close and ma else 9.0
    hh = _hhv(bars, i, 60)
    dd = ((hh - close) / hh) if hh and close else 1.0
    vr = _vol_ratio_5d(bars, i, bars[i], minutes=240)
    return (dist, dd, 9.0 if vr is None else vr)


def signal_on_bar(
    bars: list[dict],
    i: int,
    code: str,
    name: str,
    meta: dict,
    theme: dict | None = None,
    env_label: str = "ENV_OK",
    minutes: int = 240,
    row: dict | None = None,
    slot_found: bool = False,
) -> tuple[str | None, list[str], list[str]]:
    """T 日信号。调用方保证 T-1 已过池、ENV 不是 OFF。"""
    if i < 1:
        return None, [], ["日线不足"]
    row = row or bars[i]
    industry = _industry(code) or (meta or {}).get("industry") or ""
    theme = theme or theme_state(industry, str(bars[i].get("date") or "")[:10])
    mcap = _num((meta or {}).get("float_mcap_yi") or row.get("float_mcap_yi"))
    turn = _turnover(row, mcap)
    if turn is not None and turn > TURN_CLIMAX:
        return None, [], [f"当日换手 {turn:.1f}% > 30% 高潮板"]
    ok_a, hit_a, miss_a = evaluate_buy_a(bars, i, code, row, minutes, theme, slot_found)
    if ok_a:
        return "BUY_A", hit_a, []
    ok_b, hit_b, miss_b, kind = evaluate_buy_b(bars, i, code, row, minutes, theme, 0, env_label, mcap=mcap)
    if ok_b:
        return kind or "BUY_B1", hit_b, []
    return None, [], (miss_a[:3] + miss_b[:2])


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
    if not buyable(code, name, meta):
        if is_st_name(name) or meta.get("is_st"):
            base["missing_rules"].append("池子：ST / *ST")
        elif code.startswith("300"):
            base["missing_rules"].append("池子：创业板")
        elif code.startswith("688"):
            base["missing_rules"].append("池子：科创板")
        elif _is_bj(code):
            base["missing_rules"].append("池子：北证")
        else:
            base["missing_rules"].append("池子：不在可买前缀 000/001/002/003/600/601/603/605")
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
    t1_i = last_i - 1 if last_i >= 1 else last_i
    t1_date = str(bars[t1_i].get("date") or "")[:10]
    pre_t = _px(bars[t1_i], "close")
    base["facts"]["t_date"] = asof
    base["facts"]["t1_date"] = t1_date
    base["facts"]["preclose"] = pre_t
    base["facts"]["pct"] = _pct_vs(_px(last, "close"), pre_t)
    base["facts"]["ma20"] = _ma20(bars, last_i)
    base["facts"]["timeline"] = build_timeline(code, bars, last_i, t1_i)
    base["industry"] = _industry(code) or meta.get("industry") or ""
    env = ctx.get("env") or classify_env(market_stats_at(asof, None), asof)
    theme = ctx.get("theme_by_ind", {}).get(base["industry"]) or theme_state(base["industry"], asof)
    base["facts"]["env"] = env.get("label")
    base["facts"]["theme"] = base["industry"]
    base["facts"]["theme_live"] = bool(theme.get("live"))
    base["facts"]["slot_1330"] = bool(_cached_snap(asof, SLOT_LIVE))
    base["facts"]["slot_1430"] = bool(_cached_snap(asof, SLOT_BUY_END))
    base["facts"]["slot_1500"] = bool(_cached_snap(asof, SLOT_FINAL))

    if open_pos:
        zone = {
            "industry": base["industry"],
            "buy_ma20": open_pos.get("buy_ma20"),
            "buy_kind": open_pos.get("buy_kind"),
        }
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
        if env.get("label") == "ENV_OFF":
            base["hit_rules"].append("持有：ENV_OFF，停止加仓。已有仓按卖出规则管。")
        else:
            base["hit_rules"].append("持有：未到卖出。次日高开不封死、回踩分时均价不破昨收 → 可加 1 次。")
        return base

    if env.get("label") == "ENV_OFF":
        base["missing_rules"].append("否决：环境 ENV_OFF")
        base["veto"].append("环境 ENV_OFF")
        return base

    px_final, found_final, _src = _price_view(bars, t1_i, code, SLOT_FINAL)
    fails = pool_fail(bars, t1_i, code, name, meta, px_final if found_final else None, found_final)
    if fails:
        base["missing_rules"].extend(["池子：" + x for x in fails])
        return base

    in_window = bool(ctx.get("in_window")) if ctx.get("in_window") is not None else _in_buy_window()
    after = _after_buy_window() if ctx.get("after_window") is None else bool(ctx.get("after_window"))
    backtest = bool(ctx.get("backtest"))
    base["status"] = "观察"
    base["gate"] = "观察"
    base["summary_bucket"] = "观察"
    base["hit_rules"].append("已在 T-1 过硬条件，ENV 不是 OFF。当天可点火，不要求主题昨天已活。")
    if after and not in_window and not backtest:
        base["missing_rules"].append("T 日 14:30 后禁止新开")
        return base
    if not in_window and not backtest:
        base["missing_rules"].append("T 日 13:30–14:30 才允许新开成交；信号可先观察")
        return base

    px_row, slot_found, src = _price_view(bars, last_i, code, SLOT_LIVE)
    if not slot_found:
        px_row, slot_found, src = _price_view(bars, last_i, code, SLOT_BUY_END)
    if not slot_found:
        px_row, slot_found, src = bars[last_i], False, "close"
        base["facts"]["slot_fallback"] = "close"
    minutes = buy_minutes(src)
    kind, hit, miss = signal_on_bar(
        bars,
        last_i,
        code,
        name,
        meta,
        theme=theme,
        env_label=env.get("label") or "",
        minutes=minutes,
        row=px_row,
        slot_found=slot_found,
    )
    if kind:
        sealed = _yi_zi(px_row) and _is_limit_bar(bars, last_i, code, px_row)
        base["status"] = "试仓"
        base["gate"] = "试仓"
        base["summary_bucket"] = "试仓"
        base["path_ready"] = True
        base["facts"]["buy_kind"] = kind
        base["facts"]["buy_ma20"] = _ma20(bars, last_i)
        base["facts"]["unfilled"] = bool(kind == "BUY_A" and sealed)
        base["key_price"] = _px(px_row, "close")
        base["hit_rules"].extend(hit)
        size = SIZE_A_WEAK if env.get("label") == "ENV_WEAK" and kind == "BUY_A" else (SIZE_A if kind == "BUY_A" else SIZE_B)
        extra = "封死 → TRIGGER_UNFILLED，禁止打板。" if sealed and kind == "BUY_A" else "试仓不是成交指令。"
        base["hit_rules"].append(f"{kind}。仓位 {size}。{extra}")
        return base
    base["missing_rules"].extend(miss[:4])
    return base


def _build_ctx(quotes: dict, asof: str, live: bool = True, heavy: bool = False, backtest: bool = False) -> dict:
    ensure_index_daily(fetch=heavy)
    _amount_map(rebuild=heavy, quotes=quotes, asof=asof)
    stats_close = market_stats_at(asof, None)
    stats_1330 = market_stats_at(asof, SLOT_LIVE)
    env = classify_env(stats_close, asof, draft_1330=False)
    env_1330 = classify_env(stats_1330, asof, draft_1330=True)
    themes = pick_themes(asof, slot=SLOT_LIVE if _cached_snap(asof, SLOT_LIVE) else None, n=THEME_MAX)
    if not themes:
        themes = pick_themes(asof, slot=None, n=THEME_MAX)
    theme_by_ind = {t["industry"]: t for t in themes if t.get("industry")}
    in_window = True if backtest else (_in_buy_window() if live else True)
    after = False if backtest else (_after_buy_window() if live else False)
    ctx = {
        "asof": asof,
        "env": env,
        "env_1330": env_1330,
        "mainline": themes[0] if themes else None,
        "themes": themes,
        "theme_by_ind": theme_by_ind,
        "in_window": in_window,
        "after_window": after,
        "backtest": backtest,
        "stats": stats_close,
        "stats_1330": stats_1330,
    }
    SosScan.sos = {
        "env": env.get("label"),
        "env_1330": env_1330.get("label"),
        "env_why": env.get("why"),
        "mainline": " / ".join(t.get("industry") or "" for t in themes) or "",
        "mainline_why": "最多 3 条活主题，当天可点火" if themes else "没有活主题（个股自身 SOS 仍可 A）",
        "themes": [t.get("industry") for t in themes],
        "slot_1330": bool(_cached_snap(asof, SLOT_LIVE)),
        "slot_1430": bool(_cached_snap(asof, SLOT_BUY_END)),
        "slot_1500": bool(_cached_snap(asof, SLOT_FINAL)),
        "in_window": in_window,
        "note": "不扫 300/688/北证/ST。T-1 硬条件入池后才判 A/B。禁止双均线一票否决。缺档不拿更晚快照回填。",
        "t_date": asof,
    }
    SosScan.market = {"env": env.get("label"), "mainline": SosScan.sos["mainline"]}
    SosScan.funnel = themes
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
    ctx = _build_ctx(quotes, asof, live=True, heavy=False)
    return classify_sos(meta, settings, trades, quotes=quotes, open_pos=open_pos, ctx=ctx)


def scan_sos(settings: dict, trades: list | None = None) -> list[dict]:
    items = load_universe()
    if len(items) < 200:
        from .eastmoney import hydrate_universe

        items = hydrate_universe()
    quotes = load_quotes()
    asof = session_trading_date().isoformat()
    ctx = _build_ctx(quotes, asof, live=True, heavy=True)
    opens = {}
    try:
        from .buy_log import load_buy_log

        for item in load_buy_log("rules5").get("items") or []:
            if item.get("closed"):
                continue
            opens[ts_code(str(item.get("code") or ""))] = item
    except Exception:
        opens = {}
    rows = []
    pool_ranked = []
    for item in items:
        code = ts_code(str(item.get("code") or ""))
        if not code:
            continue
        name = str(item.get("name") or (quotes.get(code) or {}).get("name") or code)
        open_pos = opens.get(code)
        if open_pos:
            rows.append(classify_sos(item, settings, trades, quotes=quotes, open_pos=open_pos, ctx=ctx))
            continue
        if not buyable(code, name, item):
            continue
        bars = overlay_quote_bar(load_bars(code, last_n=120), code, quotes)
        if len(bars) < L + 1:
            continue
        last_i = len(bars) - 1
        t1_i = last_i - 1
        fails = pool_fail(bars, t1_i, code, name, item, bars[t1_i], False)
        if fails:
            continue
        row = classify_sos(item, settings, trades, quotes=quotes, open_pos=None, ctx=ctx)
        if row.get("status") == "试仓":
            rows.append(row)
        else:
            pool_ranked.append((_pool_rank_key(bars, t1_i, item.get("float_mcap_yi")), row))
    pool_ranked.sort(key=lambda x: x[0])
    watch = [row for _, row in pool_ranked[:POOL_MAX]]
    rows.extend(watch)
    trial_n = sum(1 for r in rows if r.get("status") == "试仓")
    if SosScan.sos is not None:
        SosScan.sos["trial_n"] = trial_n
        SosScan.sos["pool_n"] = len(watch)
        SosScan.sos["eligible_n"] = len(pool_ranked) + trial_n
    order = {name: i for i, name in enumerate(GATES_SOS)}
    rows.sort(key=lambda item: (order.get(item["status"], 9), item.get("industry") or "", item["code"]))
    return rows


def list_sos_cycle_universe() -> list[dict]:
    """回测名单 = 扫描留下的观察/试仓/持有/卖出 + 校准 8 只。禁止 glob 全 A。"""
    from ..config import SCAN_CACHE_DIR

    blob = read_json(SCAN_CACHE_DIR / "rules5.json", {})
    out = []
    seen = set()
    for row in blob.get("rows") or []:
        if not isinstance(row, dict):
            continue
        st = row.get("status") or row.get("gate") or "排除"
        if st == "排除":
            continue
        code = ts_code(str(row.get("code") or ""))
        if not code or code in seen or not buyable(code, str(row.get("name") or "")):
            continue
        seen.add(code)
        out.append({"code": code, "name": row.get("name") or code})
    for code, name, _kind in CALIB_9_7:
        if code in seen:
            continue
        seen.add(code)
        out.append({"code": code, "name": name})
    return out


def is_buy_sos(bars: list[dict], ctx: dict | None = None) -> bool:
    """Backtest: T-1 过硬条件且 T 日 A/B。用收盘；有 13:30/14:30 快照则用快照，绝不读更晚档。"""
    if not bars or len(bars) < L + 2:
        return False
    ctx = ctx or {}
    code = ts_code(str(bars[-1].get("code") or ctx.get("code") or ""))
    name = str(ctx.get("name") or "")
    if not buyable(code, name, ctx):
        return False
    i = len(bars) - 1
    t1 = i - 1
    asof = str(bars[i].get("date") or "")[:10]
    env = classify_env(market_stats_at(asof, None), asof)
    if env.get("label") == "ENV_OFF":
        return False
    if pool_fail(bars, t1, code, name, ctx, bars[t1], False):
        return False
    industry = _industry(code) or str(ctx.get("industry") or "")
    theme = theme_state(industry, asof)
    px_row, slot_found, src = _price_view(bars, i, code, SLOT_LIVE)
    if not slot_found:
        px_row, slot_found, src = _price_view(bars, i, code, SLOT_BUY_END)
    if not slot_found:
        px_row, slot_found, src = bars[i], False, "close"
    kind, _, _ = signal_on_bar(
        bars,
        i,
        code,
        name,
        ctx,
        theme=theme,
        env_label=env.get("label") or "",
        minutes=buy_minutes(src),
        row=px_row,
        slot_found=slot_found,
    )
    return bool(kind)


def walk_cycles_sos(bars: list[dict], ctx: dict | None = None) -> tuple[list[dict], dict | None]:
    from .cycles import _cycle_stats

    ctx = dict(ctx or {})
    n = len(bars)
    if n < L + 5:
        return [], None
    code = ts_code(str(ctx.get("code") or bars[-1].get("code") or ""))
    ctx["code"] = code
    ctx["industry"] = _industry(code) or ""
    if not buyable(code, str(ctx.get("name") or ""), ctx):
        return [], None
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
                    "buy_kind": "BUY_A",
                }
            continue
        pos = {
            "buy_date": bars[open_i].get("date"),
            "code": code,
            "buy_price": bars[open_i].get("close"),
            "buy_kind": (zone or {}).get("buy_kind"),
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


def calib_signals(asof: str = "2026-09-07") -> dict[str, str | None]:
    """正推 T 日信号。校准用，不扫全市场。"""
    uni = {ts_code(str(x.get("code") or "")): x for x in load_universe()}
    out: dict[str, str | None] = {}
    ensure_index_daily(fetch=False)
    env = classify_env(market_stats_at(asof, None), asof)
    for code, name, _expect in CALIB_9_7:
        meta = uni.get(code) or {"code": code, "name": name, "float_mcap_yi": None}
        meta.setdefault("name", name)
        bars = load_bars(code)
        idx = None
        for i, row in enumerate(bars):
            if str(row.get("date") or "")[:10] == asof:
                idx = i
                break
        if idx is None or idx < 1:
            out[code] = None
            continue
        sl = bars[: idx + 1]
        t1 = idx - 1
        if env.get("label") == "ENV_OFF" or pool_fail(sl, t1, code, name, meta, sl[t1], False):
            out[code] = None
            continue
        industry = _industry(code) or meta.get("industry") or ""
        kind, _, _ = signal_on_bar(
            sl, idx, code, name, meta, theme=theme_state(industry, asof), env_label=env.get("label") or ""
        )
        out[code] = kind
    return out
