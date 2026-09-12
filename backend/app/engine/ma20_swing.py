"""RULES3 野人哥 20日线波段。数字只来自 RULES3.MD，不另写阈值。"""
from __future__ import annotations

from ..config import GATES
from ..store import load_quotes, load_universe
from .bars import bar_amount, load_bars, overlay_quote_bar, ts_code
from .indicators import sma
from .pool import is_st_name
from .scanner import FACT_NOTE, dyn_pe_value

YI = 100_000_000.0
POOL_PRICE = 5.0
POOL_AMOUNT_YI = 1.0
POOL_MCAP_YI = 80.0
A_LEN_MIN = 5
A_LEN_MAX = 12
A_LOOKBACK = 20
A_GAIN = 0.12
A_END_NEAR_HIGH = 0.03
C_LEN_MIN = 4
C_LEN_MAX = 8
C_VOL_RATIO = 0.7
C_RETRACE = 0.40
LIMIT_LOOKBACK = 3
FAST_5D = 0.20
MA20_GAP = 0.08
STOP_MA20 = 0.95
TAKE_MA20_GAP = 0.12
TAKE_GAIN = 0.15
TAKE_GIVEBACK = 0.05
TAKE_VOL_MULT = 2.0


def _vol(row: dict) -> float:
    return float(row.get("volume") or 0)


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _limit_pct(code: str) -> float:
    c = ts_code(code)
    if c.startswith(("3", "68")):
        return 0.20
    if c.startswith(("8", "4")):
        return 0.30
    return 0.10


def _is_limit_up(bars: list[dict], i: int, code: str) -> bool:
    if i < 1:
        return False
    prev = bars[i - 1].get("close")
    cur = bars[i].get("close")
    if not prev or not cur:
        return False
    return (cur / prev - 1.0) >= _limit_pct(code) - 0.005


def _ma20_series(bars: list[dict]) -> list[float | None]:
    return sma([b.get("close") for b in bars], 20)


def _vol20_at(bars: list[dict], e: int) -> float | None:
    start = max(0, e - 19)
    xs = [_vol(bars[j]) for j in range(start, e + 1)]
    if len(xs) < 5:
        return None
    return _mean(xs)


def _check_a(bars: list[dict], s: int, e: int) -> dict | None:
    if e - s + 1 < A_LEN_MIN or e - s + 1 > A_LEN_MAX:
        return None
    start_c = bars[s].get("close")
    end_c = bars[e].get("close")
    if not start_c or not end_c:
        return None
    gain = end_c / start_c - 1.0
    if gain < A_GAIN - 1e-12:
        return None
    closes = [bars[j].get("close") for j in range(s, e + 1)]
    if any(c is None for c in closes):
        return None
    hi_c = max(closes)
    if not hi_c or end_c < hi_c * (1.0 - A_END_NEAR_HIGH) - 1e-12:
        return None
    avg20 = _vol20_at(bars, e)
    if avg20 is None:
        return None
    if not any(_vol(bars[j]) >= avg20 - 1e-12 for j in range(s, e + 1)):
        return None
    a_avg = _mean([_vol(bars[j]) for j in range(s, e + 1)])
    return {
        "s": s,
        "e": e,
        "start_c": start_c,
        "end_c": end_c,
        "hi_c": hi_c,
        "gain": gain,
        "a_avg": a_avg,
        "start_date": bars[s].get("date"),
        "end_date": bars[e].get("date"),
        "len": e - s + 1,
    }


def find_a(bars: list[dict], end_at: int | None = None) -> dict | None:
    n = len(bars) if end_at is None else end_at + 1
    if n < A_LEN_MIN + 5:
        return None
    last = n - 1
    win0 = max(0, last - A_LOOKBACK + 1)
    best = None
    for e in range(last, win0 - 1, -1):
        for L in range(A_LEN_MAX, A_LEN_MIN - 1, -1):
            s = e - L + 1
            if s < 0:
                continue
            a = _check_a(bars, s, e)
            if a:
                best = a
                break
        if best:
            break
    return best


def _second_pullback(bars: list[dict], a: dict, c0: int, last: int) -> bool:
    """第一段 C 内已收回 A 结束日收盘，再往下走 = 二次回踩。"""
    end_c = a["end_c"]
    recovered = False
    for i in range(c0, last + 1):
        c = bars[i].get("close")
        if c is None:
            continue
        if recovered and c < end_c - 1e-12:
            return True
        if c >= end_c - 1e-12:
            recovered = True
    return False


def find_setup(bars: list[dict]) -> dict | None:
    n = len(bars)
    if n < 25:
        return None
    a = find_a(bars)
    if not a:
        return None
    c0 = a["e"] + 1
    last = n - 1
    if c0 > last:
        return None
    c_len = last - c0 + 1
    if c_len < C_LEN_MIN or c_len > C_LEN_MAX:
        return None
    last_c = bars[last].get("close")
    if not last_c or last_c >= a["end_c"] - 1e-12:
        return None
    denom = a["end_c"] - a["start_c"]
    if denom <= 0:
        return None
    retrace = (a["end_c"] - last_c) / denom
    if retrace > C_RETRACE + 1e-12:
        return None
    c_avg = _mean([_vol(bars[j]) for j in range(c0, last + 1)])
    if c_avg > a["a_avg"] * C_VOL_RATIO + 1e-12:
        return None
    if _second_pullback(bars, a, c0, last):
        return None
    ma20 = _ma20_series(bars)
    m0, m1 = ma20[last], ma20[last - 1] if last else None
    if m0 is None or m1 is None:
        return None
    return {
        "a": a,
        "c0": c0,
        "c_len": c_len,
        "c_avg": c_avg,
        "retrace": retrace,
        "ma20": m0,
        "ma20_prev": m1,
    }


def pool_fail(meta: dict, last: dict) -> list[str]:
    fail = []
    name = str(meta.get("name") or last.get("name") or "")
    if is_st_name(name) or meta.get("is_st"):
        fail.append("ST")
    close = last.get("close")
    if close is None:
        fail.append("股价证据不足")
    elif close < POOL_PRICE:
        fail.append(f"股价 {close:.2f} < 5 元")
    amt = bar_amount(last)
    amount_yi = None
    if amt:
        amount_yi = amt / YI
    elif meta.get("amount_yi") is not None:
        try:
            amount_yi = float(meta["amount_yi"])
        except (TypeError, ValueError):
            amount_yi = None
    if amount_yi is None:
        fail.append("成交额证据不足")
    elif amount_yi < POOL_AMOUNT_YI:
        fail.append(f"日成交额 {amount_yi:.2f} 亿 < 1 亿")
    mcap = meta.get("float_mcap_yi")
    try:
        mcap = float(mcap) if mcap is not None else None
    except (TypeError, ValueError):
        mcap = None
    if mcap is None or mcap <= 0:
        fail.append("流通市值证据不足")
    elif mcap < POOL_MCAP_YI:
        fail.append(f"流通市值 {mcap:.0f} 亿 < 80 亿")
    pe = dyn_pe_value(meta)
    if pe is None:
        fail.append("动态市盈证据不足")
    elif pe <= 0:
        fail.append(f"动态市盈 {pe:.2f} ≤ 0")
    return fail


def _fast_5d(bars: list[dict]) -> tuple[bool, str]:
    n = len(bars)
    if n < 6:
        return False, "近5日涨幅窗口不足"
    a, b = bars[-6].get("close"), bars[-1].get("close")
    if not a or not b:
        return False, "近5日涨幅证据不足"
    ret = b / a - 1.0
    if ret >= FAST_5D - 1e-12:
        return True, f"近5日涨幅 {ret * 100:.1f}% ≥ 20%"
    return False, f"近5日涨幅 {ret * 100:.1f}% < 20%"


def _recent_limit(bars: list[dict], code: str) -> bool:
    n = len(bars)
    start = max(1, n - LIMIT_LOOKBACK)
    return any(_is_limit_up(bars, i, code) for i in range(start, n))


def evaluate_exit_ma20(bars: list[dict], open_pos: dict | None, zone: dict | None) -> tuple[bool, str, str]:
    """失败优先，止盈必须已浮盈。止损用买入日 20 日线 × 0.95，不用新均线改。"""
    if not bars or not open_pos:
        return False, "", ""
    buy_date = str(open_pos.get("buy_date") or open_pos.get("date") or "")[:10]
    buy_i = 0
    for i, row in enumerate(bars):
        if str(row.get("date") or "")[:10] >= buy_date:
            buy_i = i
            break
    else:
        buy_i = max(0, len(bars) - 1)
    zone = zone or {}
    buy_ma20 = zone.get("buy_ma20")
    if buy_ma20 is None:
        ma = _ma20_series(bars[: buy_i + 1])
        buy_ma20 = ma[buy_i] if ma else None
    if not buy_ma20:
        return False, "", ""
    last = bars[-1]
    close = last.get("close")
    if close is None:
        return False, "", ""
    stop = buy_ma20 * STOP_MA20
    if close < stop - 1e-12:
        return True, "失败", f"收盘 {close:.2f} < 买入日20日线×0.95（{stop:.2f}）"
    if buy_i <= len(bars) - 3:
        prev = bars[-2].get("close")
        if prev is not None and close < buy_ma20 - 1e-12 and prev < buy_ma20 - 1e-12:
            return True, "失败", f"连续2日收盘低于买入日20日线 {buy_ma20:.2f}"
    prior = [bars[j].get("close") for j in range(buy_i, len(bars) - 1) if bars[j].get("close") is not None]
    if prior:
        floor = min(prior)
        vol5 = _mean([_vol(bars[j]) for j in range(max(0, len(bars) - 5), len(bars))])
        if close < floor - 1e-12 and vol5 and _vol(last) >= vol5 - 1e-12:
            return True, "失败", f"收盘再创新低 {close:.2f}，且量 {_vol(last):.0f} ≥ 近5日均量 {vol5:.0f}"
    buy_px = zone.get("buy_price") or bars[buy_i].get("close")
    try:
        buy_px = float(buy_px)
    except (TypeError, ValueError):
        buy_px = bars[buy_i].get("close")
    if not buy_px or close <= buy_px + 1e-12:
        return False, "", ""
    code = ts_code(str(last.get("code") or open_pos.get("code") or ""))
    since = bars[buy_i:]
    closes = [x.get("close") for x in since if x.get("close") is not None]
    peak = max(closes) if closes else close
    vols = [_vol(x) for x in since]
    avg_vol = _mean(vols[:-1]) if len(vols) > 1 else _mean(vols)
    ranked = sorted(vols, reverse=True)
    top2 = close >= peak - 1e-12 and not _is_limit_up(bars, len(bars) - 1, code)
    vol_ok = False
    if vols:
        if ranked[:2] and _vol(last) >= ranked[min(1, len(ranked) - 1)] - 1e-12:
            vol_ok = True
        if avg_vol and _vol(last) >= avg_vol * TAKE_VOL_MULT - 1e-12:
            vol_ok = True
    ma20 = _ma20_series(bars)[-1]
    if top2 and vol_ok and ma20 and close > ma20 * (1.0 + TAKE_MA20_GAP) + 1e-12:
        return True, "获利", f"持仓收盘新高且放量，收盘高于当天20日线12%（{close:.2f} / 均线 {ma20:.2f}）"
    ret = close / buy_px - 1.0
    dd = (peak - close) / peak if peak else 0.0
    if ret >= TAKE_GAIN - 1e-12 and dd >= TAKE_GIVEBACK - 1e-12:
        return True, "获利", f"相对买入价 {ret * 100:.1f}% ≥ 15%，从持仓最高回撤 {dd * 100:.1f}% ≥ 5%"
    return False, "", ""


def classify_ma20(
    meta: dict,
    settings: dict,
    trades: list | None = None,
    quotes: dict | None = None,
    open_pos: dict | None = None,
) -> dict:
    code = ts_code(str(meta.get("code") or ""))
    name = meta.get("name") or code
    base = {
        "code": code,
        "name": name,
        "status": "排除",
        "gate": "排除",
        "summary_bucket": "排除",
        "path": "20日线波段",
        "hit_rules": [],
        "missing_rules": [],
        "reminders": [],
        "veto": [],
        "risk": [],
        "facts": {},
        "fact_note": FACT_NOTE,
        "position_block": "总闸「买入」= 试仓条件齐，不是下单。当日全账户新开 ≤ 1 只。",
        "path_ready": False,
        "data_ok": False,
        "industry": meta.get("industry") or "",
        "index_member": meta.get("index_member") or [],
        "key_kind": "20日线",
    }
    quotes = quotes if quotes is not None else load_quotes()
    bars = overlay_quote_bar(load_bars(code), code, quotes)
    if len(bars) < 25:
        base["missing_rules"].append("数据不足：日线不足以核对 20 日线波段，排除")
        return base
    last = bars[-1]
    base["data_ok"] = True
    base["facts"]["date"] = last.get("date")
    base["facts"]["close"] = last.get("close")
    pe = dyn_pe_value(meta)
    if pe is not None:
        base["facts"]["pe"] = pe
        meta = dict(meta)
        meta["pe"] = pe
    if last.get("close") is not None:
        meta = dict(meta)
        meta["close"] = last.get("close")
    fails = pool_fail(meta, last)
    if fails:
        base["missing_rules"].extend(["池子未过：" + x for x in fails])
        return base
    base["hit_rules"].append("池子：非ST · 价≥5 · 成交额≥1亿 · 流通市值≥80亿 · 动态市盈>0")

    setup = find_setup(bars)
    if not setup:
        a = find_a(bars)
        if not a:
            base["missing_rules"].append("先强：近20日没有 5～12 日、涨幅≥12%、结束日收在段内最高收盘3%以内且有放过量的 A 段")
            return base
        c_len = len(bars) - (a["e"] + 1)
        if c_len < C_LEN_MIN:
            base["missing_rules"].append(f"回踩：A 后只有 {c_len} 日，不足 4 日不算洗完")
        elif c_len > C_LEN_MAX:
            base["missing_rules"].append(f"回踩：A 后已 {c_len} 日，超过 8 日；二次回踩不得新开")
        else:
            base["missing_rules"].append("回踩：第一段 C 的缩量、回撤或二次回踩未过")
        return base
    a = setup["a"]
    base["facts"]["a_start"] = a["start_date"]
    base["facts"]["a_end"] = a["end_date"]
    base["facts"]["a_gain_pct"] = round(a["gain"] * 100, 2)
    base["facts"]["c_len"] = setup["c_len"]
    base["facts"]["c_avg"] = round(setup["c_avg"], 0)
    base["facts"]["c_retrace_pct"] = round(setup["retrace"] * 100, 2)
    base["facts"]["ma20"] = round(setup["ma20"], 3)
    base["hit_rules"].append(
        f"先强 A：{a['start_date']}→{a['end_date']} 共 {a['len']} 日，涨幅 {a['gain'] * 100:.1f}%"
    )
    base["hit_rules"].append(
        f"回踩 C：{setup['c_len']} 日，日均量 {setup['c_avg']:.0f} ≤ A日均×0.7，收盘回撤/A涨幅 {setup['retrace'] * 100:.1f}% ≤ 40%"
    )

    if _recent_limit(bars, code):
        base["missing_rules"].append("近3日有涨停：那是进攻，不是本波段买点")
        return base
    fast, fast_detail = _fast_5d(bars)
    if fast:
        base["missing_rules"].append(fast_detail + "（沿5日线加速，不是回踩）")
        return base
    base["hit_rules"].append("近3日无涨停 · " + fast_detail)

    ma20 = setup["ma20"]
    prev = setup["ma20_prev"]
    close = last.get("close")
    if ma20 < prev - 1e-12:
        base["missing_rules"].append(f"20日线向下（{ma20:.2f} < 前一日 {prev:.2f}）")
        return base
    if close < ma20 - 1e-12:
        base["missing_rules"].append(f"收盘 {close:.2f} 在 20 日线 {ma20:.2f} 下方")
        return base
    gap = (close - ma20) / ma20 if ma20 else 0.0
    if gap > MA20_GAP + 1e-12:
        base["missing_rules"].append(f"收盘距 20 日线 {gap * 100:.1f}% > 8%，不是踩线")
        return base
    base["hit_rules"].append(f"20日线走平或向上，收盘踩线（距线 {gap * 100:.1f}% ≤ 8%）")
    stop = ma20 * STOP_MA20
    base["facts"]["stop_price"] = round(stop, 3)
    base["key_price"] = round(ma20, 3)
    base["stop_price"] = round(stop, 3)

    if open_pos:
        buy_ma20 = open_pos.get("buy_ma20")
        if buy_ma20 is None and open_pos.get("stop_price"):
            try:
                buy_ma20 = float(open_pos["stop_price"]) / STOP_MA20
            except (TypeError, ValueError):
                buy_ma20 = None
        hit, section, detail = evaluate_exit_ma20(
            bars, open_pos, {"buy_ma20": buy_ma20, "buy_price": open_pos.get("buy_price")}
        )
        if hit:
            base["status"] = "卖出"
            base["gate"] = "卖出"
            base["summary_bucket"] = "卖出"
            base["hit_rules"].append(f"卖出已见（{section}）：{detail}")
            return base
        base["status"] = "买入"
        base["gate"] = "买入"
        base["summary_bucket"] = "买入"
        base["path_ready"] = True
        base["hit_rules"].append("持仓未到卖出，仍按买入闸记录")
        return base

    vol_now = _vol(last)
    if vol_now > setup["c_avg"] + 1e-12:
        base["status"] = "买入"
        base["gate"] = "买入"
        base["summary_bucket"] = "买入"
        base["path_ready"] = True
        base["hit_rules"].append(f"买入：收盘站上20日线，当日量 {vol_now:.0f} > C段日均 {setup['c_avg']:.0f}")
        base["hit_rules"].append("止损写死为买入日20日线×0.95。仓位 10%～15%。买入不是下单。")
        base["facts"]["buy_ma20"] = round(ma20, 3)
        return base
    base["status"] = "观察"
    base["gate"] = "观察"
    base["summary_bucket"] = "观察"
    base["hit_rules"].append("观察：靠近20日线且缩量，当天量还没转强")
    base["missing_rules"].append(f"买入：当日量 {vol_now:.0f} 尚未 > C段日均 {setup['c_avg']:.0f}")
    return base


def is_buy_ma20(bars: list[dict], ctx: dict | None = None) -> bool:
    if not bars:
        return False
    setup = find_setup(bars)
    if not setup:
        return False
    last = bars[-1]
    close = last.get("close")
    ma20 = setup["ma20"]
    if close is None or close < ma20 - 1e-12:
        return False
    if (close - ma20) / ma20 > MA20_GAP + 1e-12:
        return False
    if ma20 < setup["ma20_prev"] - 1e-12:
        return False
    code = ts_code(str(last.get("code") or (ctx or {}).get("code") or ""))
    if _recent_limit(bars, code):
        return False
    fast, _ = _fast_5d(bars)
    if fast:
        return False
    return _vol(last) > setup["c_avg"] + 1e-12


def walk_cycles_ma20(bars: list[dict], ctx: dict | None = None) -> tuple[list[dict], dict | None]:
    from .cycles import _cycle_stats

    ctx = dict(ctx or {})
    n = len(bars)
    if n < 30:
        return [], None
    cycles = []
    open_i = None
    zone = None
    last_a_e = -1
    for i in range(25, n):
        sl = bars[: i + 1]
        if open_i is None:
            setup = find_setup(sl)
            if setup and setup["a"]["e"] > last_a_e and is_buy_ma20(sl, ctx):
                open_i = i
                zone = {
                    "buy_ma20": setup["ma20"],
                    "buy_price": sl[-1].get("close"),
                    "a_e": setup["a"]["e"],
                }
            continue
        hit, section, detail = evaluate_exit_ma20(
            sl, {"buy_date": bars[open_i].get("date"), "code": ctx.get("code")}, zone
        )
        if i > open_i and hit:
            cycles.append(_cycle_stats(bars, open_i, i, exit_section=section, exit_detail=detail))
            last_a_e = (zone or {}).get("a_e", open_i)
            open_i = None
            zone = None
    live = None
    if open_i is not None:
        live = _cycle_stats(bars, open_i, n - 1, closed=False)
    return cycles, live


def classify_one_ma20(code: str, settings: dict, trades: list | None = None) -> dict:
    code = ts_code(code)
    uni = {ts_code(str(x.get("code") or "")): x for x in load_universe()}
    quotes = load_quotes()
    q = quotes.get(code) or {}
    meta = dict(uni.get(code) or {})
    meta["code"] = code
    meta["name"] = meta.get("name") or q.get("name") or code
    if q.get("pe") is not None:
        meta["pe"] = q["pe"]
    if q.get("float_mcap_yi") is not None:
        meta["float_mcap_yi"] = q["float_mcap_yi"]
    if q.get("amount_yi") is not None:
        meta["amount_yi"] = q["amount_yi"]
    open_pos = None
    for trade in trades or []:
        if ts_code(str(trade.get("code") or "")) == code and trade.get("direction") in ("开仓", "加仓"):
            open_pos = {"buy_date": trade.get("date"), "buy_price": None, "code": code}
    try:
        from .buy_log import load_buy_log

        for item in load_buy_log("rules3").get("items") or []:
            if item.get("closed"):
                continue
            if ts_code(str(item.get("code") or "")) == code:
                open_pos = item
                break
    except Exception:
        pass
    return classify_ma20(meta, settings, trades, quotes=quotes, open_pos=open_pos)


def scan_ma20(settings: dict, trades: list | None = None) -> list[dict]:
    from .eastmoney import hydrate_universe

    items = hydrate_universe()
    quotes = load_quotes()
    opens = {}
    try:
        from .buy_log import load_buy_log

        for item in load_buy_log("rules3").get("items") or []:
            if item.get("closed"):
                continue
            opens[ts_code(str(item.get("code") or ""))] = item
    except Exception:
        opens = {}
    rows = [
        classify_ma20(
            item,
            settings,
            trades,
            quotes=quotes,
            open_pos=opens.get(ts_code(str(item.get("code") or ""))),
        )
        for item in items
    ]
    order = {name: i for i, name in enumerate(GATES)}
    rows.sort(key=lambda item: (order.get(item["status"], 9), item.get("industry") or "", item["code"]))
    return rows
