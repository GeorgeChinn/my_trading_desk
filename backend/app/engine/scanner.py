from __future__ import annotations

from typing import Optional

from ..config import (
    DIF_LOOKBACK,
    GATES,
    GATES_S1,
    KDJ_LOW,
    POOL_AMOUNT_YI,
    POOL_FLOAT_MCAP_YI,
    POOL_MIN_PRICE,
    VETO_AMOUNT_YI,
)
from .bars import attach_indicators, bar_amount, load_bars, overlay_quote_bar, parse_amount, ts_code
from .indicators import last_number, sma
from .pool import is_st_name

YI = 100_000_000.0
PROFILE_BAN = ("打板", "连板", "高位接力")

FACT_NOTE = "这是事实记录"
MISSING_NO_BUY = "缺数据，不升买入"


def funnel_reminders(settings: dict) -> list[str]:
    return []


def _num(value) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _recent(series: list, n: int, skip_last: int = 0):
    end = len(series) - skip_last
    start = max(0, end - n)
    return [item for item in series[start:end] if item is not None]


def _cross_up_at(fast: list, slow: list, end_idx: int) -> bool:
    if end_idx < 1 or end_idx >= len(fast) or end_idx >= len(slow):
        return False
    a0, a1 = fast[end_idx - 1], fast[end_idx]
    b0, b1 = slow[end_idx - 1], slow[end_idx]
    if None in (a0, a1, b0, b1):
        return False
    return a0 <= b0 and a1 > b1


def _cross_up(fast: list, slow: list) -> bool:
    return _cross_up_at(fast, slow, len(fast) - 1)


def recent_dif_golden_cross(
    dif: list, dea: list, within_two_days: bool = False
) -> tuple[bool, str, int | None]:
    """只认最新一根相对上一根的 DIF 上穿 DEA。within_two_days 仅当正文仍写「近一两日」时打开。"""
    last = len(dif) - 1
    if last < 1:
        return False, "DIF/DEA 窗口不足", None
    still = dif[last] is not None and dea[last] is not None and dif[last] > dea[last]
    offsets = (0, 1) if within_two_days else (0,)
    for offset in offsets:
        idx = last - offset
        if _cross_up_at(dif, dea, idx):
            day = "当日" if offset == 0 else "前一日"
            if offset == 0 or still:
                return True, f"{day} DIF 上穿 DEA（金叉）", idx
    if still:
        if within_two_days:
            return False, "DIF 在 DEA 上方，但近两日未见上穿", None
        return False, "DIF 在 DEA 上方，但当日未见上穿（不得用快要上穿代替）", None
    return False, "DIF 尚未上穿 DEA", None


def nearer_to_window_low(series: list, last_val, label: str) -> tuple[bool | None, str]:
    window = _recent(series, DIF_LOOKBACK)
    if not window or last_val is None:
        return None, f"近 20 日{label}窗口不足"
    lo, hi = min(window), max(window)
    if hi == lo:
        return True, f"近 20 日{label}无波动，当前即窗口值"
    if (last_val - lo) <= (hi - last_val):
        return True, f"{label}更靠近近20日最低（低 {lo:.4f} / 高 {hi:.4f}）"
    return False, f"{label}更靠近近20日高点，不在低位区（低 {lo:.4f} / 高 {hi:.4f}）"


def kdj_overbought(k, j) -> tuple[bool | None, str]:
    """§4：J ≥ 80 或 K > 50 → 超买区，不得新开。"""
    if k is None or j is None:
        return None, "KDJ 证据不足，超买否决无法核对"
    if j >= 80 or k > 50:
        why = []
        if j >= 80:
            why.append(f"J {j:.2f} ≥ 80")
        if k > 50:
            why.append(f"K {k:.2f} > 50")
        return True, "超买区（" + "；".join(why) + "），不得新开"
    return False, f"KDJ 未超买（J {j:.2f} < 80 且 K {k:.2f} ≤ 50）"


def pullback_60_below_zero(highs: list, close, dif) -> tuple[bool | None, str, dict]:
    """否决：最新价距近 60 日最高价回撤 ≥ 15%，且 DIF 仍在零轴下。"""
    facts: dict = {"hhv60": None, "retrace_60_pct": None}
    window = _recent(highs, 60)
    if close is None or not window:
        return None, "近 60 日最高价窗口不足", facts
    if len(window) < 60:
        return None, f"近 60 日最高价仅 {len(window)} 根，证据不足", facts
    hhv = max(window)
    facts["hhv60"] = hhv
    if hhv <= 0:
        return None, "近 60 日最高价无效", facts
    retrace = (hhv - close) / hhv
    facts["retrace_60_pct"] = retrace * 100.0
    dif_below = dif is not None and dif < 0
    if retrace >= 0.15 and dif is None:
        return None, f"距近60日高回撤 {retrace * 100:.1f}% ≥ 15%，但 DIF 证据不足", facts
    if retrace >= 0.15 and dif_below:
        return (
            True,
            f"距近60日高 {hhv:.2f} 回撤 {retrace * 100:.1f}% ≥ 15%，且 DIF {dif:.4f} 仍在零轴下",
            facts,
        )
    if retrace >= 0.15:
        return False, f"回撤 {retrace * 100:.1f}% ≥ 15%，但 DIF 不在零轴下，本条否决不命中", facts
    return False, f"距近60日高回撤 {retrace * 100:.1f}% < 15%", facts


def ma30_down_veto(closes: list) -> tuple[bool | None, str, float | None]:
    """否决：最新价 < MA30 且 MA30 向下。"""
    ma = sma(closes, 30)
    if len(ma) < 2 or ma[-1] is None or ma[-2] is None or not closes or closes[-1] is None:
        return None, "MA30 窗口不足", None
    last_ma, prev_ma = ma[-1], ma[-2]
    close = closes[-1]
    if close < last_ma and last_ma < prev_ma:
        return (
            True,
            f"最新价 {close:.2f} < MA30 {last_ma:.2f} 且 MA30 向下（前值 {prev_ma:.2f}）",
            last_ma,
        )
    return (
        False,
        f"未同时满足最新价<MA30且MA30向下（最新 {close:.2f} / MA30 {last_ma:.2f} / 前值 {prev_ma:.2f}）",
        last_ma,
    )


def dyn_pe_value(meta: dict) -> Optional[float]:
    for key in ("pe", "pe_ttm", "dyn_pe", "动态市盈"):
        val = _num(meta.get(key))
        if val is not None:
            return val
    return None


def zero_axis_golden(dif: list, dea: list, cross_idx: int | None) -> tuple[bool | None, str]:
    """金叉在零轴下方，或刚过零轴附近。DIF/DEA 已远离零轴上方 → 不得买入。"""
    idx = cross_idx if cross_idx is not None else len(dif) - 1
    if idx < 0 or idx >= len(dif):
        return None, "零轴判定窗口不足"
    d, e = dif[idx], dea[idx]
    if d is None or e is None:
        return None, "DIF/DEA 不足，零轴无法核对"
    if d <= 0 and e <= 0:
        return True, f"金叉在零轴下方（DIF {d:.4f} / DEA {e:.4f}）"
    for j in (idx, idx - 1):
        if j < 0:
            continue
        dd, ee = dif[j], dea[j]
        if (dd is not None and dd <= 0) or (ee is not None and ee <= 0):
            return True, f"金叉刚过零轴附近（DIF {d:.4f} / DEA {e:.4f}）"
    return False, f"DIF {d:.4f}、DEA {e:.4f} 已远离零轴上方，不得买入"


def _just_red(hist: list, end_idx: int) -> bool:
    if end_idx < 1 or end_idx >= len(hist):
        return False
    h0, h1 = hist[end_idx - 1], hist[end_idx]
    return h0 is not None and h1 is not None and h0 < 0 <= h1


def _green_shrink_not_new_low(hist: list, end_idx: int) -> bool:
    if end_idx < 1 or end_idx >= len(hist):
        return False
    h0, h1 = hist[end_idx - 1], hist[end_idx]
    if h0 is None or h1 is None or h1 >= 0 or h0 >= 0:
        return False
    if abs(h1) >= abs(h0):
        return False
    window = [x for x in hist[max(0, end_idx - 20) : end_idx] if x is not None]
    return bool(window) and h1 > min(window)


def dif_near_20d_low(dif: list, last_dif) -> tuple[bool | None, str]:
    return nearer_to_window_low(dif, last_dif, "DIF")


def macd_section5(hist: list) -> tuple[bool, str]:
    """观察→等待：近两日绿柱缩短不创新低，或刚由绿转红。不要求当天仍是绿柱。"""
    if len(hist) < 2:
        return False, "MACD 柱窗口不足"
    hits = []
    last = len(hist) - 1
    for offset in (0, 1):
        idx = last - offset
        if idx < 1:
            continue
        day = "当日" if offset == 0 else "前一日"
        if _just_red(hist, idx):
            hits.append(f"{day}刚由绿转红")
        if _green_shrink_not_new_low(hist, idx):
            hits.append(f"{day}绿柱缩短且不创新低")
    if hits:
        return True, "；".join(hits)
    return False, "近两日未见绿柱缩短，也未见刚转红"


def _cross_down(fast: list, slow: list) -> bool:
    if len(fast) < 2 or len(slow) < 2:
        return False
    a0, a1 = fast[-2], fast[-1]
    b0, b1 = slow[-2], slow[-1]
    if None in (a0, a1, b0, b1):
        return False
    return a0 >= b0 and a1 < b1


def _limit_pct(code: str) -> float:
    c = ts_code(code)
    if c.startswith(("3", "68")):
        return 0.20
    return 0.10


def detect_limit_streak(bars: list[dict], code: str) -> int:
    pct = _limit_pct(code)
    streak = 0
    for idx in range(len(bars) - 1, -1, -1):
        row = bars[idx]
        o = row.get("open")
        c = row.get("close")
        if not o or not c:
            break
        prev_close = bars[idx - 1]["close"] if idx > 0 else o
        change = (c - prev_close) / prev_close if prev_close else 0.0
        if change >= pct - 0.005:
            streak += 1
        else:
            break
    return streak


def _snapshot(row: Optional[dict]) -> dict:
    if not row:
        return {}
    keys = (
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "ma5",
        "ma10",
        "ma20",
        "dif",
        "dea",
        "hist",
        "k",
        "d",
        "j",
    )
    snap = {key: row.get(key) for key in keys}
    if snap.get("close") and snap.get("ma5"):
        snap["ma5_gap_pct"] = (snap["close"] - snap["ma5"]) / snap["ma5"] * 100.0
    else:
        snap["ma5_gap_pct"] = None
    return snap


def classify_stock(
    meta: dict,
    settings: dict,
    trades: list[dict] | None = None,
    flags: dict | None = None,
    quotes: dict | None = None,
) -> dict:
    code = ts_code(str(meta.get("code", "")))
    name = meta.get("name") or code
    if flags is None:
        from .rules_bind import parse_flags

        flags = parse_flags()

    base = {
        "code": code,
        "name": name,
        "status": "排除",
        "gate": "排除",
        "summary_bucket": "排除",
        "path": "波段持有",
        "hit_rules": [],
        "missing_rules": [],
        "reminders": [],
        "veto": [],
        "risk": [],
        "facts": {},
        "fact_note": FACT_NOTE,
        "position_block": "总闸「买入」只表示路径到达，不是成交指令",
        "can_upgrade_position": False,
        "path_ready": False,
        "data_ok": False,
        "index_member": meta.get("index_member") or [],
        "tags": meta.get("tags") or [],
    }

    raw_bars = overlay_quote_bar(load_bars(code), code, quotes)
    if len(raw_bars) < 40:
        base["missing_rules"].append("数据不足：日线不足以计算 MACD(7,28,4)，排除")
        base["risk"].append("证据不足，禁止用想象补 K 线")
        return base

    bars = attach_indicators(raw_bars)
    last = bars[-1]
    prev = bars[-2] if len(bars) > 1 else None
    bar_name = (last.get("name") or "").strip()
    if bar_name and (not name or name == code):
        name = bar_name
        base["name"] = name
    if last.get("dif") is None or last.get("hist") is None or last.get("k") is None:
        base["missing_rules"].append("数据不足或参数对不上 → 排除")
        base["facts"] = _snapshot(last)
        return base

    base["data_ok"] = True
    base["facts"] = _snapshot(last)
    base["facts"]["source"] = "数据与设置最新更新"
    if prev:
        base["facts"]["prev_date"] = prev["date"]
        base["facts"]["prev_close"] = prev["close"]
        base["facts"]["prev_hist"] = prev.get("hist")
        base["facts"]["prev_ma5"] = prev.get("ma5")

    amount = bar_amount(last)
    amount_yi = amount / YI if amount is not None else None
    close = float(last["close"])
    float_mcap = _num(meta.get("float_mcap_yi"))
    pe = dyn_pe_value(meta)
    q = {}
    if float_mcap is None or pe is None:
        from ..store import load_quotes

        q = load_quotes().get(code) or {}
        if float_mcap is None:
            float_mcap = _num(q.get("float_mcap_yi"))
        if pe is None:
            pe = dyn_pe_value(q)
    if float_mcap is not None and float_mcap <= 0:
        float_mcap = None
    is_st = bool(meta.get("is_st")) or is_st_name(name)
    tags = list(meta.get("tags") or [])

    # 否决：打板、连板、高位接力
    banned = [tag for tag in tags if tag in PROFILE_BAN]
    streak = detect_limit_streak(bars, code)
    # 连板用最新一根往回核对。单日涨停不等于打板行为，不另发明否决。
    if streak >= 2 and "连板" not in banned:
        banned.append("连板")
    if banned:
        base["status"] = "排除"
        base["gate"] = "排除"
        base["summary_bucket"] = "排除"
        base["veto"] = banned
        base["hit_rules"].append("否决：" + "、".join(banned))
        base["risk"].append("打板、连板、高位接力不得新开")
        return base

    # 池子（否则排除）
    pool_fail = []
    pool_hit = []
    if float_mcap is None:
        pool_fail.append("流通市值证据不足")
    elif float_mcap < POOL_FLOAT_MCAP_YI:
        pool_fail.append(f"流通市值 {float_mcap:.0f} 亿 < 300 亿")
    else:
        pool_hit.append(f"流通市值 {float_mcap:.0f} 亿 ≥ 300 亿")

    if amount_yi is None:
        pool_fail.append("成交额证据不足")
    elif amount_yi < POOL_AMOUNT_YI:
        pool_fail.append(f"日成交额 {amount_yi:.2f} 亿 < 5 亿")
    else:
        pool_hit.append(f"日成交额 {amount_yi:.2f} 亿 ≥ 5 亿")

    if close < POOL_MIN_PRICE:
        pool_fail.append(f"股价 {close:.2f} < 5 元")
    else:
        pool_hit.append(f"股价 {close:.2f} ≥ 5 元")

    if is_st:
        pool_fail.append("ST")
    else:
        pool_hit.append("非 ST")

    if pe is not None:
        base["facts"]["pe"] = pe
    if pe is None:
        pool_fail.append("动态市盈证据不足")
    elif pe <= 0:
        pool_fail.append(f"动态市盈 {pe:.2f} ≤ 0")
    else:
        pool_hit.append(f"动态市盈 {pe:.2f} > 0")

    if pool_fail:
        base["missing_rules"].extend(["池子未过：" + x for x in pool_fail])
        base["hit_rules"].extend(["池子已见：" + x for x in pool_hit])
        return base

    hist = [row.get("hist") for row in bars]
    dif = [row.get("dif") for row in bars]
    dea = [row.get("dea") for row in bars]
    k_line = [row.get("k") for row in bars]
    d_line = [row.get("d") for row in bars]
    j_line = [row.get("j") for row in bars]
    h_line = [row.get("high") for row in bars]
    c_line = [row.get("close") for row in bars]

    # 否决 → 排除（硬闸）
    veto_tech: list[str] = []
    s4_unknown: list[str] = []
    if flags.get("veto_kdj_overbought", True):
        ob, ob_detail = kdj_overbought(last.get("k"), last.get("j"))
        if ob is True:
            veto_tech.append(ob_detail)
        elif ob is None:
            s4_unknown.append(ob_detail)
    if flags.get("veto_pullback_60", True):
        pb, pb_detail, pb_facts = pullback_60_below_zero(h_line, last.get("close"), last.get("dif"))
        base["facts"].update(pb_facts)
        if pb is True:
            veto_tech.append(pb_detail)
        elif pb is None:
            s4_unknown.append(pb_detail)
    else:
        pb = False
    if flags.get("veto_ma30_down", True):
        m30, m30_detail, ma30_val = ma30_down_veto(c_line)
        base["facts"]["ma30"] = ma30_val
        if m30 is True:
            veto_tech.append(m30_detail)
        elif m30 is None:
            s4_unknown.append(m30_detail)
    else:
        m30 = False
    if veto_tech:
        base["veto"] = veto_tech
        base["hit_rules"].append("否决：" + "；".join(veto_tech))
        return base

    # 过池、未否决 → 观察。买入另核当日金叉 + 零轴 + 低位。
    base["status"] = "观察"
    base["gate"] = "观察"
    base["summary_bucket"] = "观察"
    base["hit_rules"].append("池子：" + "；".join(pool_hit or ["股价/非ST已见"]))
    if meta.get("index_member"):
        base["hit_rules"].append("优先样本：" + " / ".join(meta["index_member"]))

    if s4_unknown:
        base["missing_rules"].extend(["否决证据不足：" + x for x in s4_unknown])

    cond_macd_watch, macd_watch_detail = macd_section5(hist)
    if cond_macd_watch:
        base["hit_rules"].append("观察：绿柱缩短或不创新低 / 刚转红，只够观察（" + macd_watch_detail + "）")
    else:
        base["hit_rules"].append("观察：两线粘合不够买入（" + macd_watch_detail + "）")

    buy_cross, cross_detail, cross_idx = recent_dif_golden_cross(dif, dea, within_two_days=False)
    near_low, near_detail = nearer_to_window_low(dif, last.get("dif"), "DIF")
    px6, px6_detail = nearer_to_window_low(c_line, last.get("close"), "最新价")
    zero_ok, zero_detail = zero_axis_golden(dif, dea, cross_idx)
    if last.get("dif") is not None and _recent(dif, DIF_LOOKBACK):
        base["facts"]["dif_20_min"] = min(_recent(dif, DIF_LOOKBACK))
        base["facts"]["dif_to_20min"] = last["dif"] - min(_recent(dif, DIF_LOOKBACK))

    if buy_cross:
        base["hit_rules"].append("买入：" + cross_detail)
    else:
        base["missing_rules"].append("买入：" + cross_detail + "。尚未当日金叉，留观察")

    if zero_ok is True:
        base["hit_rules"].append("买入：" + zero_detail)
    else:
        base["missing_rules"].append("买入：" + zero_detail)

    if near_low is True:
        base["hit_rules"].append("买入：" + near_detail)
    else:
        base["missing_rules"].append("买入：" + near_detail + "，不得买入")

    if px6 is True:
        base["hit_rules"].append("买入 股价低位区：" + px6_detail)
    else:
        base["missing_rules"].append("买入 股价不在近20日低位区：" + px6_detail)

    s4_clear = not s4_unknown
    if s4_unknown:
        base["missing_rules"].append("买入：否决未能全部核对，不得买入")
    else:
        base["hit_rules"].append("买入：否决全部未命中")

    path_ready = bool(
        buy_cross and near_low is True and zero_ok is True and px6 is True and s4_clear
    )
    base["path_ready"] = path_ready
    if path_ready:
        base["status"] = "买入"
        base["gate"] = "买入"
        base["summary_bucket"] = "买入"
        base["hit_rules"].append("路径到达买入。买入不是成交指令")
    else:
        base["summary_bucket"] = "观察"

    open_trade = None
    for trade in trades or []:
        if ts_code(str(trade.get("code", ""))) == code and trade.get("direction") in ("开仓", "加仓"):
            open_trade = trade
    if open_trade:
        from .exits import evaluate_exit

        series = {
            "bars": bars,
            "hist": hist,
            "dif": dif,
            "dea": dea,
            "k": k_line,
            "d": d_line,
            "j": j_line,
            "h": h_line,
            "c": c_line,
            "last": last,
            "prev": prev,
        }
        entry_idx = len(bars) - 1
        trade_date = str(open_trade.get("date") or open_trade.get("buy_date") or "")
        if trade_date:
            for idx, row in enumerate(bars):
                if str(row.get("date")) >= trade_date:
                    entry_idx = idx
                    break
        hit, section, detail = evaluate_exit(series, entry_idx)
        note = f"手工开仓记录存在（仓位 {open_trade.get('position_pct')}%）"
        if hit:
            base["status"] = "卖出"
            base["gate"] = "卖出"
            base["summary_bucket"] = "卖出"
            base["hit_rules"].append(f"卖出已见（{section}）：{note}；{detail}")
        else:
            base["status"] = "买入"
            base["gate"] = "买入"
            base["summary_bucket"] = "买入"
            base["hit_rules"].append("卖出对照未齐，持仓仍按买入闸记录：" + note)

    return base


def scan_universe(
    universe: list[dict],
    settings: dict,
    trades: list[dict] | None = None,
    flags: dict | None = None,
    engine: str = "low_golden",
) -> list[dict]:
    from .rules_bind import parse_flags

    if engine == "pullback_restart":
        from .structure_one import scan_structure_one

        rows = scan_structure_one(settings, trades)
        order = {name: i for i, name in enumerate(GATES_S1)}
        rows.sort(key=lambda item: (order.get(item["status"], 9), item["code"]))
        return rows
    if engine == "ma20_swing":
        from .ma20_swing import scan_ma20

        rows = scan_ma20(settings, trades)
        order = {name: i for i, name in enumerate(GATES)}
        rows.sort(key=lambda item: (order.get(item["status"], 9), item["code"]))
        return rows
    if engine != "low_golden":
        return []
    if flags is None:
        flags = parse_flags()
    from ..store import load_quotes
    from .eastmoney import hydrate_universe

    universe = hydrate_universe(universe)
    quotes = load_quotes()
    rows = [classify_stock(meta, settings, trades, flags=flags, quotes=quotes) for meta in universe]
    order = {name: i for i, name in enumerate(GATES)}
    rows.sort(key=lambda item: (order.get(item["status"], 9), item["code"]))
    return rows


def summarize(rows: list[dict], gates: tuple | list | None = None) -> dict:
    gates = list(gates or GATES)
    by_gate = {key: 0 for key in gates}
    names = {key: [] for key in gates}
    for row in rows:
        gate = row.get("gate") or row.get("status") or "排除"
        if gate not in by_gate:
            by_gate[gate] = 0
            names[gate] = []
            gates.append(gate)
        by_gate[gate] += 1
        pe = None
        facts = row.get("facts") or {}
        if facts.get("pe") is not None:
            pe = facts.get("pe")
        names[gate].append(
            {
                "code": row.get("code"),
                "name": row.get("name"),
                "pe": pe,
                "industry": row.get("industry") or facts.get("industry"),
                "ruleset": row.get("ruleset"),
                "engine": row.get("engine"),
            }
        )
    total = len(rows)
    remain = total - int(by_gate.get("排除") or 0)
    return {
        "summary": by_gate,
        "by_gate": by_gate,
        "names": names,
        "total": total,
        "remain": remain,
        "gates": gates,
    }
