"""RULES4 Test+Repair。数字和句子只来自 RULES4.MD，不另写门槛。"""
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
LEAVE = 0.03
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


def _close_ge_open(row: dict) -> bool:
    o, c = row.get("open"), row.get("close")
    return o is not None and c is not None and c >= o - 1e-12


def _ret(bars: list[dict], i: int) -> float | None:
    if i < 1:
        return None
    a, b = bars[i - 1].get("close"), bars[i].get("close")
    if a is None or b is None or not a:
        return None
    return b / a - 1.0


def _px(row: dict | None, key: str = "close"):
    if not row:
        return None
    try:
        val = row.get(key)
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


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


def _yi_zi(row: dict) -> bool:
    o, h, l, c = row.get("open"), row.get("high"), row.get("low"), row.get("close")
    if None in (o, h, l, c):
        return False
    return abs(h - l) <= 1e-9 and abs(c - o) <= 1e-9


def _vol_ma5(bars: list[dict], i: int) -> float | None:
    """前 5 日均量。RULES4 未写剔除涨停。"""
    if i < 5:
        return None
    got = [_vol(bars[j]) for j in range(i - 5, i)]
    if len(got) < 5:
        return None
    return _mean(got)


def _repair_day(bars: list[dict], i: int) -> bool:
    ret = _ret(bars, i)
    if ret is None or ret < R1 - 1e-12 or not _yang(bars[i]):
        return False
    avg = _vol_ma5(bars, i)
    if avg is None or avg <= 0:
        return False
    return _vol(bars[i]) >= avg * R1V - 1e-12


def _find_shock(bars: list[dict], start: int, end: int) -> dict | None:
    """近窗里：单日最新价跌幅 ≥ 7%，或距近高回撤 ≥ 12%。取最近一次。"""
    if end < start:
        return None
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


def _chased_boards(bars: list[dict], r1i: int, last: int, code: str) -> bool:
    """修复后没有回踩，直接追连板。"""
    streak = 0
    for j in range(r1i + 1, last + 1):
        if _is_limit_up(bars, j, code):
            streak += 1
            if streak >= 2:
                return True
        else:
            streak = 0
    return False


def _typical(row: dict) -> float | None:
    c = _px(row, "close")
    if c is None:
        return None
    h = _px(row, "high")
    lo = _px(row, "low")
    return ((h if h is not None else c) + (lo if lo is not None else c) + c) / 3.0


def find_setup(bars: list[dict], code: str = "") -> dict | None:
    n = len(bars)
    if n < L:
        return None
    last = n - 1
    code = ts_code(code or str(bars[last].get("code") or ""))
    shock_from = max(1, last - L + 1)
    best = None
    for r1i in range(last, shock_from, -1):
        if not _repair_day(bars, r1i):
            continue
        shock = _find_shock(bars, shock_from, r1i - 1)
        if not shock or shock["idx"] >= r1i:
            continue
        support = _px(bars[r1i], "low")
        if support is None:
            continue
        twin_lo = r1i + TWIN_MIN
        twin_hi = min(last, r1i + TWIN_MAX)
        if twin_lo > last:
            continue
        post_hi = None
        for j in range(r1i, twin_lo):
            h = _px(bars[j], "high")
            c = _px(bars[j], "close")
            for x in (h, c):
                if x is not None:
                    post_hi = x if post_hi is None else max(post_hi, x)
        pull_idx: list[int] = []
        for t in range(twin_lo, twin_hi + 1):
            close = _px(bars[t], "close")
            low = _px(bars[t], "low")
            if close is None or low is None:
                continue
            # 离开日不是回踩：涨幅≥3% 且收阳。
            leave = _ret(bars, t)
            if leave is not None and leave >= LEAVE - 1e-12 and _yang(bars[t]):
                continue
            if close < support - 1e-12:
                continue
            pulled = post_hi is not None and (low < post_hi - 1e-12 or close < post_hi - 1e-12)
            if not pulled:
                continue
            pull_idx.append(t)
        if not pull_idx:
            continue
        test_end = pull_idx[-1]
        lows = [_px(bars[j], "low") for j in range(pull_idx[0], test_end + 1)]
        lows = [x for x in lows if x is not None]
        if not lows:
            continue
        test_low = min(lows)
        test_vols = [_vol(bars[j]) for j in range(pull_idx[0], test_end + 1)]
        best = {
            "shock": shock,
            "r1": {
                "idx": r1i,
                "date": bars[r1i].get("date"),
                "low": support,
                "close": _px(bars[r1i], "close"),
                "vol": _vol(bars[r1i]),
            },
            "test_end": test_end,
            "test_date": bars[test_end].get("date"),
            "test_low": test_low,
            "support": support,
            "test_vol_avg": _mean(test_vols),
        }
        break
    return best


def _probe_repair(bars: list[dict], code: str) -> tuple[dict | None, dict | None]:
    last = len(bars) - 1
    shock_from = max(1, last - L + 1)
    shock = _find_shock(bars, shock_from, last)
    if not shock:
        return None, None
    r1 = None
    for i in range(shock["idx"] + 1, last + 1):
        if _repair_day(bars, i):
            r1 = {
                "idx": i,
                "date": bars[i].get("date"),
                "low": _px(bars[i], "low"),
                "close": _px(bars[i], "close"),
            }
            break
    return shock, r1


def _is_leave(bars: list[dict], i: int, setup: dict) -> tuple[bool, list[str], list[str]]:
    """试仓：最新更新同时齐。"""
    hit, miss = [], []
    last = bars[i]
    close = _px(last, "close")
    support = setup["support"]
    test_low = setup["test_low"]
    ret = _ret(bars, i)
    if ret is not None and ret >= LEAVE - 1e-12:
        hit.append(f"最新价涨幅 {ret * 100:.1f}% ≥ 3%")
    else:
        miss.append("试仓：最新价涨幅未到 3%")
    if _close_ge_open(last):
        hit.append("最新价 ≥ 开盘")
    else:
        miss.append("试仓：最新价 < 开盘")
    avg = setup.get("test_vol_avg") or 0.0
    if _vol(last) > avg + 1e-12:
        hit.append(f"本次已成交量 {_vol(last):.0f} > 回踩段日均 {avg:.0f}")
    else:
        miss.append("试仓：本次已成交量未大于回踩段日均量")
    if close is not None and close >= float(support) - 1e-12:
        hit.append(f"最新价站上支撑 {float(support):.2f}")
    else:
        miss.append(f"试仓：最新价未站上支撑 {float(support):.2f}")
    if close is not None and close >= float(test_low) - 1e-12:
        hit.append(f"最新价未跌破 Test 低点 {float(test_low):.2f}")
    else:
        miss.append(f"试仓：最新价跌破 Test 低点 {float(test_low):.2f}")
    return (not miss), hit, miss


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
    close = _px(last, "close")
    if close is None:
        return False, "", ""
    test_low = zone.get("test_low")
    support = zone.get("support")
    buy_px = open_pos.get("buy_price")
    try:
        buy_px = float(buy_px) if buy_px not in (None, "") else None
    except (TypeError, ValueError):
        buy_px = None
    stop = float(test_low) * STOP if test_low is not None else None
    raised = False
    if buy_px and stop is not None:
        r_dist = buy_px - stop
        if r_dist > 0 and close >= buy_px + r_dist - 1e-12:
            raised = True
        hi = max((_px(x, "close") or 0) for x in bars[buy_i:])
        if r_dist > 0 and hi >= buy_px + r_dist - 1e-12:
            raised = True
    # 止损 / 失败优先。命中任一条当天走。
    if raised and buy_px is not None and close <= buy_px + 1e-12:
        return True, "止损", f"浮盈到过 1R 后止损已抬到成本 {buy_px:.2f}，最新价 {close:.2f} ≤ 成本"
    if stop is not None and close <= stop + 1e-12:
        return True, "止损", f"最新价 {close:.2f} ≤ Test 低点×0.97（{stop:.2f}）"
    if support is not None and close < float(support) - 1e-12:
        return True, "失败", f"最新价 {close:.2f} 跌破支撑价 {float(support):.2f}"
    if buy_i == len(bars) - 2:
        trial = bars[buy_i]
        nxt = bars[buy_i + 1]
        if _yang(trial) and not _yang(nxt):
            t_o, t_c = trial.get("open"), trial.get("close")
            n_o, n_c = nxt.get("open"), nxt.get("close")
            if None not in (t_o, t_c, n_o, n_c) and n_o >= t_c - 1e-12 and n_c <= t_o + 1e-12:
                return True, "失败", "试仓阳线被下一根更新的阴线实体吃掉"
    held = len(bars) - 1 - buy_i
    if HOLD_MIN <= held <= HOLD_MAX:
        trial_typ = _typical(bars[buy_i])
        late = [_typical(x) for x in bars[-min(3, held + 1) :]]
        late = [x for x in late if x is not None]
        hi = max((_px(x, "close") or 0) for x in bars[buy_i:])
        trial_c = _px(bars[buy_i], "close") or 0
        if trial_typ is not None and late and _mean(late) <= trial_typ + 1e-12 and hi <= trial_c + 1e-12:
            return True, "失败", f"试仓后 {held} 日仍横着、重心不抬"
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
        "position_block": "总闸：排除 → 观察 → 试仓 → 持有 → 卖出。买入 = 试仓条件齐，不是下单。当日本规则新开 ≤ 1 只。",
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
        base["missing_rules"].append(f"池子：日线不足 {L} 根")
        return base
    if not any(_vol(x) > 0 for x in bars[-L:]):
        base["missing_rules"].append("池子：近 40 根无成交量")
        return base
    last = bars[-1]
    last_i = len(bars) - 1
    close = _px(last, "close")
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
            base["status"] = "卖出"
            base["gate"] = "卖出"
            base["summary_bucket"] = "卖出"
            base["hit_rules"].append(f"卖出已见（{section}）：{detail}")
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
        held = last_i - buy_i
        ma5 = sma([x.get("close") for x in bars], 5)
        trial_low = bars[buy_i].get("low")
        m5 = ma5[-1]
        if ADD_MIN <= held <= ADD_MAX and m5 and close and trial_low:
            if close <= m5 + 1e-12 and (last.get("low") or close) >= trial_low - 1e-12:
                base["hit_rules"].append("持有：回踩 5 日线且不破试仓阳线低点，可加到计划仓位的 70%。不再第三次加仓。")
        buy_px = open_pos.get("buy_price")
        test_low = zone.get("test_low")
        try:
            if buy_px and test_low:
                r_dist = float(buy_px) - float(test_low) * STOP
                if r_dist > 0 and close and close >= float(buy_px) + r_dist - 1e-12:
                    base["hit_rules"].append("浮盈到 1R，止损抬到成本。R = 进场价到止损的距离。")
        except (TypeError, ValueError):
            pass
        base["hit_rules"].append("持有：未到卖出。试仓仓位 30%～40% 计划仓。观察池不得一次打满。")
        return base

    setup = find_setup(bars, code)
    if not setup:
        shock, r1 = _probe_repair(bars, code)
        if not shock:
            base["missing_rules"].append("观察：近 40 根未出现单日最新价跌幅 ≥ 7%，也未距近高回撤 ≥ 12%")
            return base
        if not r1:
            base["missing_rules"].append("否决：没有过修复，直接抄地")
            base["veto"].append("没有过修复，直接抄地")
            return base
        if _chased_boards(bars, r1["idx"], last_i, code):
            base["missing_rules"].append("否决：修复后没有回踩，直接追连板")
            base["veto"].append("修复后没有回踩，直接追连板")
            return base
        base["missing_rules"].append("观察：修复后 3～15 个交易日尚未出现回踩（最低价靠近支撑，当时最新价不破支撑）")
        return base

    r1 = setup["r1"]
    support = setup["support"]
    test_low = setup["test_low"]
    if support is None or test_low is None:
        base["missing_rules"].append("否决：标不出支撑价和回踩低点两个数字")
        base["veto"].append("标不出支撑价和回踩低点两个数字")
        return base
    if close is not None and close < float(support) - 1e-12:
        base["missing_rules"].append(f"否决：最新价 {close:.2f} 跌破已标注支撑价 {float(support):.2f}，且未收回")
        base["veto"].append("最新价跌破已标注的支撑价，且未收回")
        return base

    base["facts"]["support"] = round(float(support), 3)
    base["facts"]["test_low"] = round(float(test_low), 3)
    base["facts"]["first_repair_date"] = r1["date"]
    base["facts"]["first_repair_low"] = r1["low"]
    base["facts"]["first_repair_close"] = r1["close"]
    base["facts"]["test_date"] = setup["test_date"]
    base["facts"]["stop_price"] = round(float(test_low) * STOP, 3)
    base["key_price"] = round(float(support), 3)
    base["stop_price"] = round(float(test_low) * STOP, 3)
    base["hit_rules"].append(
        f"观察四数：支撑价 {float(support):.2f} · Test低点 {float(test_low):.2f} · 第一次Repair {r1['date']} · Test {setup['test_date']}"
    )

    after_test = last_i > setup["test_end"]
    if after_test:
        ok, hits, miss = _is_leave(bars, last_i, setup)
        if ok:
            if _yi_zi(last):
                base["status"] = "观察"
                base["gate"] = "观察"
                base["summary_bucket"] = "观察"
                base["hit_rules"].extend(hits)
                base["hit_rules"].append("一字封死买不到 = 本轮作废，不改打板补进。")
                return base
            base["status"] = "试仓"
            base["gate"] = "试仓"
            base["summary_bucket"] = "试仓"
            base["path_ready"] = True
            base["hit_rules"].extend(hits)
            base["hit_rules"].append("试仓仓位 30%～40% 计划仓。观察池不得一次打满。试仓不是成交指令。")
            return base
        base["status"] = "观察"
        base["gate"] = "观察"
        base["summary_bucket"] = "观察"
        base["missing_rules"].extend(miss)
        return base
    base["status"] = "观察"
    base["gate"] = "观察"
    base["summary_bucket"] = "观察"
    base["missing_rules"].append("试仓：已在观察池。当日最新价离开条件未齐。")
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
    if _yi_zi(bars[last_i]):
        return False
    ok, _, _ = _is_leave(bars, last_i, setup)
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
                    zone = {
                        "test_low": setup["test_low"],
                        "support": setup["support"],
                        "r1": setup["r1"]["idx"],
                    }
            continue
        pos = {"buy_date": bars[open_i].get("date"), "code": code, "buy_price": bars[open_i].get("close")}
        hit, section, detail = evaluate_exit_trs(sl, pos, zone)
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
