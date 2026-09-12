"""RULES4 Test+Repair（TRS-v1）。数字只来自 RULES4.MD 表。不与金叉/低吸/20日线混闸。"""
from __future__ import annotations

from ..config import CSV_DIR, GATES_TRS
from ..store import load_quotes, load_universe
from .bars import bar_amount, load_bars, overlay_quote_bar, peek_last_bar, ts_code
from .indicators import sma
from .pool import is_st_name
from .scanner import FACT_NOTE

L = 40
D1 = 0.07
D2 = 0.12
R1 = 0.03
R1V = 1.3
TWIN_MIN = 3
TWIN_MAX = 15
R2 = 0.03
STOP = 0.97
HOLD_MIN = 3
HOLD_MAX = 10
ADD_MIN = 1
ADD_MAX = 5
SIZE_LO = 0.30
SIZE_HI = 0.40


def _vol(row: dict) -> float:
    return float(row.get("volume") or 0)


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _yang(row: dict) -> bool:
    o, c = row.get("open"), row.get("close")
    return o is not None and c is not None and c > o


def _ret(bars: list[dict], i: int) -> float | None:
    if i < 1:
        return None
    a, b = bars[i - 1].get("close"), bars[i].get("close")
    if not a or not b:
        return None
    return b / a - 1.0


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


def _is_limit_down(bars: list[dict], i: int, code: str) -> bool:
    if i < 1:
        return False
    prev = bars[i - 1].get("close")
    cur = bars[i].get("close")
    if not prev or not cur:
        return False
    return (cur / prev - 1.0) <= -(_limit_pct(code) - 0.005)


def _yi_zi(row: dict) -> bool:
    o, h, l, c = row.get("open"), row.get("high"), row.get("low"), row.get("close")
    if None in (o, h, l, c):
        return False
    return abs(h - l) <= 1e-9 and abs(c - o) <= 1e-9


def _vol_ma5(bars: list[dict], i: int, code: str) -> float | None:
    """前5日均量。涨停天量不进均量，否则急杀前一天涨停会把 Repair 量条件卡死。"""
    got: list[float] = []
    j = i - 1
    while j >= 1 and len(got) < 5:
        if not _is_limit_up(bars, j, code):
            got.append(_vol(bars[j]))
        j -= 1
    if len(got) < 3:
        got = [_vol(bars[k]) for k in range(max(0, i - 5), i)]
    if not got:
        return None
    return _mean(got)


def _r1(bars: list[dict], i: int) -> bool:
    ret = _ret(bars, i)
    return bool(ret is not None and ret >= R1 - 1e-12 and _yang(bars[i]))


def _r1v(bars: list[dict], i: int, code: str) -> bool:
    avg = _vol_ma5(bars, i, code)
    if avg is None:
        return False
    return _vol(bars[i]) >= avg * R1V - 1e-12


def _find_shock(bars: list[dict], end: int, code: str) -> dict | None:
    start = max(1, end - L + 1)
    best = None
    win_hi = None
    for i in range(start, end + 1):
        h = bars[i].get("high")
        if h is not None:
            win_hi = h if win_hi is None else max(win_hi, h)
        ret = _ret(bars, i)
        d1 = bool(ret is not None and ret <= -D1 + 1e-12)
        close = bars[i].get("close")
        d2 = False
        if win_hi and close:
            d2 = (win_hi - close) / win_hi >= D2 - 1e-12
        if d1 or d2:
            best = {
                "idx": i,
                "date": bars[i].get("date"),
                "low": bars[i].get("low"),
                "d1": d1,
                "d2": d2,
            }
    return best


def _first_repair(bars: list[dict], shock_i: int, end: int, code: str) -> dict | None:
    for i in range(shock_i + 1, end + 1):
        if _r1(bars, i) and _r1v(bars, i, code):
            return {
                "idx": i,
                "date": bars[i].get("date"),
                "low": bars[i].get("low"),
                "close": bars[i].get("close"),
                "vol": _vol(bars[i]),
            }
    return None


def _platform_low(bars: list[dict], r1: int) -> float | None:
    lo = None
    for j in range(max(0, r1 - 5), r1):
        x = bars[j].get("low")
        if x is None:
            continue
        lo = x if lo is None else min(lo, x)
    return lo


def find_setup(bars: list[dict], code: str = "") -> dict | None:
    n = len(bars)
    if n < L:
        return None
    last = n - 1
    code = ts_code(code or str(bars[last].get("code") or ""))
    start = max(1, last - L + 1)
    best = None
    for r1i in range(last, start, -1):
        if not (_r1(bars, r1i) and _r1v(bars, r1i, code)):
            continue
        shock = _find_shock(bars, r1i - 1, code) if r1i > 1 else None
        if not shock or shock["idx"] >= r1i:
            continue
        r1 = {
            "idx": r1i,
            "date": bars[r1i].get("date"),
            "low": bars[r1i].get("low"),
            "close": bars[r1i].get("close"),
            "vol": _vol(bars[r1i]),
        }
        twin_lo = r1i + TWIN_MIN
        twin_hi = min(last, r1i + TWIN_MAX)
        if twin_lo > last:
            continue
        post_hi = r1["close"] or 0
        for j in range(r1i, twin_lo):
            c = bars[j].get("close")
            if c is not None:
                post_hi = max(post_hi, c)
        test_end = None
        for t in range(twin_lo, twin_hi + 1):
            close = bars[t].get("close")
            low = bars[t].get("low")
            if close is None or low is None:
                continue
            if _r1(bars, t):
                continue
            pulled = close < post_hi - 1e-12 or low < post_hi - 1e-12
            recovered = close >= (r1["low"] or close) - 1e-12
            if pulled and recovered:
                test_end = t
        if test_end is None:
            continue
        lows = [bars[j].get("low") for j in range(r1i + 1, test_end + 1) if bars[j].get("low") is not None]
        caps = [bars[j].get("close") for j in range(r1i + 1, test_end + 1) if bars[j].get("close") is not None]
        if not lows:
            continue
        support = r1["low"] or shock.get("low") or _platform_low(bars, r1i)
        if support is None:
            continue
        test_vols = [_vol(bars[j]) for j in range(r1i + 1, test_end + 1)]
        best = {
            "shock": shock,
            "r1": r1,
            "test_end": test_end,
            "test_date": bars[test_end].get("date"),
            "test_low": min(lows),
            "test_cap": max(caps) if caps else r1["close"],
            "support": support,
            "tvol_ok": bool(test_vols) and _mean(test_vols) < r1["vol"],
            "test_vol_avg": _mean(test_vols),
        }
        break
    return best


def _is_repair2(bars: list[dict], i: int, setup: dict) -> tuple[bool, list[str], list[str]]:
    hit, miss = [], []
    last = bars[i]
    close = last.get("close")
    support = setup["support"]
    cap = setup["test_cap"]
    yang = _yang(last)
    above_cap = close is not None and close >= cap - 1e-12
    above_sup_yang = close is not None and close > support and yang
    if above_cap or above_sup_yang:
        hit.append("收盘站上 Test 区间上沿（回踩段最高收盘，或支撑价上方且收阳）")
    else:
        miss.append("试仓：收盘未站上 Test 区间上沿")
    ret = _ret(bars, i)
    if ret is not None and ret >= R2 - 1e-12 and yang:
        hit.append(f"R2：收盘涨幅 {ret * 100:.1f}% ≥ 3% 且收阳")
    else:
        miss.append("试仓：未命中 R2（收盘涨幅≥3%且收阳）")
    avg = setup.get("test_vol_avg") or 0.0
    if _vol(last) > avg + 1e-12:
        hit.append(f"R2V：量 {_vol(last):.0f} > Test 段日均 {avg:.0f}")
    else:
        miss.append("试仓：未命中 R2V（量 > Test 段日均量）")
    if close is not None and close >= support - 1e-12:
        hit.append(f"收盘未跌破支撑价 {support:.2f}")
    else:
        miss.append(f"试仓：收盘跌破支撑价 {support:.2f}")
    ok = not miss
    return ok, hit, miss


def evaluate_exit_trs(bars: list[dict], open_pos: dict | None, zone: dict | None) -> tuple[bool, str, str]:
    if not bars or not open_pos:
        return False, "", ""
    zone = zone or {}
    buy_date = str(open_pos.get("buy_date") or open_pos.get("date") or "")[:10]
    buy_i = 0
    for i, row in enumerate(bars):
        if str(row.get("date") or "")[:10] >= buy_date:
            buy_i = i
            break
    last = bars[-1]
    code = ts_code(str(last.get("code") or open_pos.get("code") or ""))
    close = last.get("close")
    low = last.get("low")
    if close is None:
        return False, "", ""
    test_low = zone.get("test_low")
    support = zone.get("support")
    stop = None
    if test_low is not None:
        stop = float(test_low) * STOP
        if close < float(test_low) - 1e-12:
            return True, "失败", f"收盘跌破 Test 低点 {float(test_low):.2f}"
        px = close if low is None else min(close, float(low))
        if px <= stop + 1e-12:
            return True, "止损", f"最新价 {px:.2f} ≤ Test 低点×0.97（{stop:.2f}）"
    if support is not None and close < float(support) - 1e-12:
        return True, "失败", f"收盘跌破支撑价 {float(support):.2f} 且未收回"
    if buy_i == len(bars) - 2:
        trial = bars[buy_i]
        nxt = bars[buy_i + 1]
        if _yang(trial) and not _yang(nxt):
            t_o, t_c = trial.get("open"), trial.get("close")
            n_o, n_c = nxt.get("open"), nxt.get("close")
            if None not in (t_o, t_c, n_o, n_c) and n_o >= t_c - 1e-12 and n_c <= t_o + 1e-12:
                return True, "失败", "试仓阳线被次日阴线实体吃掉"
    count = 0
    open_board = None
    for j in range(buy_i, len(bars)):
        if _is_limit_up(bars, j, code):
            count += 1
            continue
        if count >= 2:
            open_board = j
            break
        count = 0
    last_i = len(bars) - 1
    if open_board is not None and last_i > open_board:
        return True, "失败", "连板后开板之后继续走弱，退出"
    held = len(bars) - 1 - buy_i
    if held >= HOLD_MAX:
        mid = lambda r: ((r.get("high") or 0) + (r.get("low") or 0) + (r.get("close") or 0)) / 3.0
        early = [mid(x) for x in bars[buy_i : buy_i + 3] if x.get("close") is not None]
        late = [mid(x) for x in bars[-3:] if x.get("close") is not None]
        if early and late and _mean(late) <= _mean(early) + 1e-12:
            return True, "失败", f"已到 Hold 上限 {HOLD_MAX} 日仍横着，重心不再抬"
    return False, "", ""


def classify_trs(
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
        "path": "Test+Repair",
        "hit_rules": [],
        "missing_rules": [],
        "reminders": [],
        "veto": [],
        "risk": [],
        "facts": {},
        "fact_note": FACT_NOTE,
        "position_block": "总闸：排除 → 观察 → 试仓 → 持有 → 退出。试仓不是成交指令。当日本规则新开 ≤ 1 只。",
        "path_ready": False,
        "data_ok": False,
        "industry": meta.get("industry") or "",
        "key_kind": "支撑价",
    }
    quotes = quotes if quotes is not None else load_quotes()
    bars = overlay_quote_bar(load_bars(code), code, quotes)
    for row in bars:
        if bar_amount(row) is None and row.get("volume") and row.get("close"):
            row["amount"] = float(row["volume"]) * float(row["close"])
    if is_st_name(name) or meta.get("is_st"):
        base["missing_rules"].append("池子：ST / *ST")
        return base
    if len(bars) < L:
        base["missing_rules"].append(f"池子：日线不足 L={L} 根")
        return base
    if not any(_vol(x) > 0 for x in bars[-L:]):
        base["missing_rules"].append("池子：近 L 日无成交量")
        return base
    last = bars[-1]
    base["data_ok"] = True
    base["facts"]["date"] = last.get("date")
    base["facts"]["close"] = last.get("close")

    if open_pos:
        zone = {
            "test_low": open_pos.get("test_low") or open_pos.get("di_low"),
            "support": open_pos.get("support") or open_pos.get("stop_price"),
        }
        hit, section, detail = evaluate_exit_trs(bars, open_pos, zone)
        if hit:
            base["status"] = "退出"
            base["gate"] = "退出"
            base["summary_bucket"] = "退出"
            base["hit_rules"].append(f"退出已见（{section}）：{detail}")
            return base
        base["status"] = "持有"
        base["gate"] = "持有"
        base["summary_bucket"] = "持有"
        base["path_ready"] = True
        buy_i = 0
        buy_date = str(open_pos.get("buy_date") or "")[:10]
        for i, row in enumerate(bars):
            if str(row.get("date") or "")[:10] >= buy_date:
                buy_i = i
                break
        held = len(bars) - 1 - buy_i
        ma5 = sma([x.get("close") for x in bars], 5)
        trial_low = bars[buy_i].get("low")
        m5 = ma5[-1]
        close = last.get("close")
        if ADD_MIN <= held <= ADD_MAX and m5 and close and trial_low:
            if close <= m5 + 1e-12 and (last.get("low") or close) >= trial_low - 1e-12:
                base["hit_rules"].append("持有：回踩5日线且不破试仓阳线低点，可加到计划仓位的 70%。不再第三次加仓。")
        buy_px = open_pos.get("buy_price")
        test_low = zone.get("test_low")
        try:
            if buy_px and test_low:
                r = float(buy_px) - float(test_low) * STOP
                if r > 0 and close and close >= float(buy_px) + r - 1e-12:
                    base["hit_rules"].append("浮盈到 1R，止损抬到成本")
        except (TypeError, ValueError):
            pass
        if held >= 2:
            limits = sum(1 for j in range(buy_i, len(bars) - 1) if _is_limit_up(bars, j, code))
            if limits >= 2 and not _is_limit_up(bars, len(bars) - 1, code):
                prev_vols = [_vol(x) for x in bars[buy_i:-1]]
                if prev_vols and _vol(last) > max(prev_vols) + 1e-12:
                    base["hit_rules"].append("连板后开板且换手高于试仓以来，优先减半或清掉")
        base["hit_rules"].append("持有：未到退出。试仓仓位 30%～40% 计划仓。")
        return base

    setup = find_setup(bars, code)
    if not setup:
        shock = _find_shock(bars, len(bars) - 1, code)
        if not shock:
            base["missing_rules"].append("观察：近 L 日未命中 D1（单日收跌≥7%）或 D2（近高回撤≥12%）")
            return base
        r1 = _first_repair(bars, shock["idx"], len(bars) - 1, code)
        if not r1:
            base["missing_rules"].append("否决：没有第一次 Repair，直接抄地")
            return base
        base["missing_rules"].append("观察：第一次 Repair 后 Twin（3～15日）内没有合格回踩 Test")
        return base

    r1 = setup["r1"]
    base["facts"]["support"] = round(float(setup["support"]), 3)
    base["facts"]["test_low"] = round(float(setup["test_low"]), 3)
    base["facts"]["first_repair_date"] = r1["date"]
    base["facts"]["first_repair_low"] = r1["low"]
    base["facts"]["first_repair_close"] = r1["close"]
    base["facts"]["test_date"] = setup["test_date"]
    base["facts"]["stop_price"] = round(float(setup["test_low"]) * STOP, 3)
    base["key_price"] = round(float(setup["support"]), 3)
    base["stop_price"] = round(float(setup["test_low"]) * STOP, 3)
    base["hit_rules"].append(
        f"观察四数：支撑价 {setup['support']:.2f} · Test低点 {setup['test_low']:.2f} · 第一次Repair {r1['date']} · Test {setup['test_date']}"
    )
    if setup.get("tvol_ok"):
        base["hit_rules"].append("Test 缩量（佳）：回踩日均量 < 第一次 Repair 当日量")
    last_i = len(bars) - 1
    after_test = last_i > setup["test_end"]
    if after_test:
        ok, hits, miss = _is_repair2(bars, last_i, setup)
        if ok:
            exec_note = ""
            if _yi_zi(last) or _is_limit_up(bars, last_i, code):
                exec_note = "执行差：板上/一字，试仓窗口差，不改用打板逻辑补进。"
            base["status"] = "试仓"
            base["gate"] = "试仓"
            base["summary_bucket"] = "试仓"
            base["path_ready"] = True
            base["hit_rules"].extend(hits)
            if exec_note:
                base["hit_rules"].append(exec_note)
            base["hit_rules"].append("试仓仓位 30%～40% 计划仓。观察池不得一次打满。试仓不是成交指令。")
            base["facts"]["support"] = round(float(setup["support"]), 3)
            base["facts"]["test_low"] = round(float(setup["test_low"]), 3)
            return base
        base["status"] = "观察"
        base["gate"] = "观察"
        base["summary_bucket"] = "观察"
        base["missing_rules"].extend(miss)
        return base
    base["status"] = "观察"
    base["gate"] = "观察"
    base["summary_bucket"] = "观察"
    base["missing_rules"].append("试仓：已在观察池，当日 R2/R2V 未齐")
    return base


def is_buy_trs(bars: list[dict], ctx: dict | None = None) -> bool:
    if not bars:
        return False
    code = ts_code(str(bars[-1].get("code") or (ctx or {}).get("code") or ""))
    setup = find_setup(bars, code)
    if not setup:
        return False
    last_i = len(bars) - 1
    if last_i <= setup["test_end"]:
        return False
    ok, _, _ = _is_repair2(bars, last_i, setup)
    return ok


def walk_cycles_trs(bars: list[dict], ctx: dict | None = None) -> tuple[list[dict], dict | None]:
    from .cycles import _cycle_stats

    ctx = dict(ctx or {})
    n = len(bars)
    if n < L + 5:
        return [], None
    code = ts_code(str(ctx.get("code") or bars[-1].get("code") or ""))
    cycles = []
    open_i = None
    zone = None
    last_r1 = -1
    for i in range(L, n):
        sl = bars[: i + 1]
        if open_i is None:
            if is_buy_trs(sl, ctx):
                setup = find_setup(sl, code)
                if setup and setup["r1"]["idx"] > last_r1:
                    open_i = i
                    zone = {"test_low": setup["test_low"], "support": setup["support"], "r1": setup["r1"]["idx"]}
            continue
        hit, section, detail = evaluate_exit_trs(
            sl, {"buy_date": bars[open_i].get("date"), "code": code}, zone
        )
        if i > open_i and hit:
            cycles.append(_cycle_stats(bars, open_i, i, exit_section=section, exit_detail=detail))
            last_r1 = (zone or {}).get("r1", open_i)
            open_i = None
            zone = None
    live = None
    if open_i is not None:
        live = _cycle_stats(bars, open_i, n - 1, closed=False)
    return cycles, live


def classify_one_trs(code: str, settings: dict, trades: list | None = None) -> dict:
    code = ts_code(code)
    uni = {ts_code(str(x.get("code") or "")): x for x in load_universe()}
    quotes = load_quotes()
    q = quotes.get(code) or {}
    meta = dict(uni.get(code) or {})
    meta["code"] = code
    meta["name"] = meta.get("name") or q.get("name") or code
    open_pos = None
    for trade in trades or []:
        if ts_code(str(trade.get("code") or "")) == code and trade.get("direction") in ("开仓", "加仓"):
            open_pos = {"buy_date": trade.get("date"), "buy_price": None, "code": code}
    try:
        from .buy_log import load_buy_log

        for item in load_buy_log("rules4").get("items") or []:
            if item.get("closed"):
                continue
            if ts_code(str(item.get("code") or "")) == code:
                open_pos = item
                break
    except Exception:
        pass
    return classify_trs(meta, settings, trades, quotes=quotes, open_pos=open_pos)


def list_trs_cycle_universe() -> list[dict]:
    uni = {ts_code(str(x.get("code") or "")): x for x in load_universe()}
    out = []
    for path in CSV_DIR.glob("*.csv"):
        code = ts_code(path.stem)
        if not code:
            continue
        last = peek_last_bar(code)
        if not last or last.get("close") is None:
            continue
        name = last.get("name") or (uni.get(code) or {}).get("name") or code
        out.append({"code": code, "name": name})
    return out


def scan_trs(settings: dict, trades: list | None = None) -> list[dict]:
    from .eastmoney import hydrate_universe

    items = hydrate_universe()
    quotes = load_quotes()
    opens = {}
    try:
        from .buy_log import load_buy_log

        for item in load_buy_log("rules4").get("items") or []:
            if item.get("closed"):
                continue
            opens[ts_code(str(item.get("code") or ""))] = item
    except Exception:
        opens = {}
    rows = [
        classify_trs(
            item,
            settings,
            trades,
            quotes=quotes,
            open_pos=opens.get(ts_code(str(item.get("code") or ""))),
        )
        for item in items
    ]
    order = {name: i for i, name in enumerate(GATES_TRS)}
    rows.sort(key=lambda item: (order.get(item["status"], 9), item.get("industry") or "", item["code"]))
    return rows
