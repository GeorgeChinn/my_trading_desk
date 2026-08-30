from __future__ import annotations

from typing import Optional

from ..config import (
    DIF_LOOKBACK,
    GATES,
    HHV_LOOKBACK,
    KDJ_HIGH,
    KDJ_LOW,
    POOL_AMOUNT_YI,
    POOL_FLOAT_MCAP_YI,
    POOL_MIN_PRICE,
    VETO_AMOUNT_YI,
)
from .bars import attach_indicators, load_bars, ts_code
from .indicators import last_number

YI = 100_000_000.0
PROFILE_BAN = ("打板", "连板", "高位接力", "隔夜情绪票")
VETO_EXCLUDE = ("小盘题材", "连板妖股", "游资票", "亏损暴雷股")

FACT_NOTE = "这是事实记录"


def funnel_reminders(settings: dict) -> list[str]:
    if (settings.get("market_regime") or "未设置") == "未设置":
        return ["大盘未定性（多 / 空 / 震荡）。只提醒，不参与筛选。"]
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


def recent_dif_golden_cross(dif: list, dea: list) -> tuple[bool, str]:
    """金叉近一两日即可，不要求确认收盘当天正在上穿。"""
    last = len(dif) - 1
    if last < 1:
        return False, "DIF/DEA 窗口不足"
    still = dif[last] is not None and dea[last] is not None and dif[last] > dea[last]
    for offset in (0, 1):
        idx = last - offset
        if _cross_up_at(dif, dea, idx):
            day = "当日" if offset == 0 else "前一日"
            if offset == 0 or still:
                return True, f"{day} DIF 上穿 DEA（金叉）"
    if still:
        return False, "DIF 在 DEA 上方，但近两日未见上穿"
    return False, "DIF 尚未上穿 DEA"


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
    """靠近近20日 DIF 最小值：相对窗口高低点更靠近最低，用来挡高位金叉。"""
    window = _recent(dif, DIF_LOOKBACK)
    if not window or last_dif is None:
        return None, "近 20 日 DIF 窗口不足"
    dmin, dmax = min(window), max(window)
    if dmax == dmin:
        return True, "近 20 日 DIF 无波动，当前即窗口值"
    nearer_low = (last_dif - dmin) <= (dmax - last_dif)
    if nearer_low:
        return True, f"DIF 更靠近近20日最低（低 {dmin:.4f} / 高 {dmax:.4f}）"
    return False, f"DIF 更靠近近20日高点，视为高位金叉（低 {dmin:.4f} / 高 {dmax:.4f}）"


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


def classify_stock(meta: dict, settings: dict, trades: list[dict] | None = None) -> dict:
    code = ts_code(str(meta.get("code", "")))
    name = meta.get("name") or code
    person_present = bool(settings.get("person_present", True))
    market_regime = settings.get("market_regime") or "未设置"

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
        "person_present": person_present,
        "market_regime": market_regime,
        "position_block": "总闸「买入」只表示 RULES 第6条路径到达，不是成交指令",
        "can_upgrade_position": False,
        "data_ok": False,
        "index_member": meta.get("index_member") or [],
        "tags": meta.get("tags") or [],
    }

    raw_bars = load_bars(code)
    if len(raw_bars) < 40:
        base["missing_rules"].append("数据不足：日线不足以计算 MACD(7,28,4)，排除")
        base["risk"].append("证据不足，禁止用想象补 K 线")
        return base

    bars = attach_indicators(raw_bars)
    last = bars[-1]
    prev = bars[-2] if len(bars) > 1 else None
    if last.get("dif") is None or last.get("hist") is None or last.get("k") is None:
        base["missing_rules"].append("数据不足或参数对不上 → 排除")
        base["facts"] = _snapshot(last)
        return base

    base["data_ok"] = True
    base["facts"] = _snapshot(last)
    base["facts"]["source"] = "本地 CSV 确认收盘"
    if prev:
        base["facts"]["prev_date"] = prev["date"]
        base["facts"]["prev_close"] = prev["close"]
        base["facts"]["prev_hist"] = prev.get("hist")
        base["facts"]["prev_ma5"] = prev.get("ma5")

    amount = _num(last.get("amount")) or 0.0
    amount_yi = amount / YI
    close = float(last["close"])
    float_mcap = _num(meta.get("float_mcap_yi"))
    is_st = bool(meta.get("is_st"))
    tags = list(meta.get("tags") or [])

    # PROFILE 不做 → 禁止
    banned = [tag for tag in tags if tag in PROFILE_BAN]
    streak = detect_limit_streak(bars, code)
    # 连板可从确认收盘核对。单日涨停不等于打板行为，不另发明否决。
    if streak >= 2 and "连板" not in banned:
        banned.append("连板")
    if banned:
        base["status"] = "排除"
        base["gate"] = "排除"
        base["summary_bucket"] = "排除"
        base["veto"] = banned
        base["hit_rules"].append("PROFILE 不做 / RULES 否决：" + "、".join(banned))
        base["risk"].append("打板、连板、高位接力、隔夜情绪票不在主路径")
        return base

    # RULES §4 否决 → 排除
    veto_hits = [tag for tag in tags if tag in VETO_EXCLUDE]
    if amount_yi < VETO_AMOUNT_YI:
        veto_hits.append(f"日成交额 {amount_yi:.2f} 亿 < 1 亿")
    if is_st:
        veto_hits.append("ST / *ST")
    if veto_hits:
        base["veto"] = veto_hits
        base["hit_rules"].append("RULES §4 否决：" + "、".join(veto_hits))
        return base

    # RULES §3 池子
    pool_fail = []
    pool_hit = []
    if float_mcap is None:
        pool_fail.append("流通市值证据不足")
    elif float_mcap < POOL_FLOAT_MCAP_YI:
        pool_fail.append(f"流通市值 {float_mcap:.0f} 亿 < 300 亿")
    else:
        pool_hit.append(f"流通市值 {float_mcap:.0f} 亿 ≥ 300 亿")

    if amount_yi < POOL_AMOUNT_YI:
        pool_fail.append(f"日成交额 {amount_yi:.2f} 亿 < 5 亿（1–5 亿不进池，一律排除）")
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

    if pool_fail:
        base["missing_rules"].extend(["RULES §3 池子未过：" + x for x in pool_fail])
        base["hit_rules"].extend(["池子已见：" + x for x in pool_hit])
        return base

    # Passed pool. Stop at 观察 unless §5 is complete.
    base["status"] = "观察"
    base["gate"] = "观察"
    base["summary_bucket"] = "观察"
    base["hit_rules"].append("RULES §3 池子：" + "；".join(pool_hit))
    if meta.get("index_member"):
        base["hit_rules"].append("优先样本：" + " / ".join(meta["index_member"]))

    if market_regime != "未设置":
        base["hit_rules"].append(f"大盘开关（人工定性，不参与筛选）：{market_regime}")

    if not person_present:
        base["risk"].append("人不在场：只输出观察，不升仓位档")

    hist = [row.get("hist") for row in bars]
    dif = [row.get("dif") for row in bars]
    dea = [row.get("dea") for row in bars]
    k_line = [row.get("k") for row in bars]
    d_line = [row.get("d") for row in bars]
    j_line = [row.get("j") for row in bars]
    h_line = [row.get("high") for row in bars]

    h0, h1 = hist[-2], hist[-1]
    cond_macd_watch, macd_watch_detail = macd_section5(hist)

    kd_cross = _cross_up(k_line, d_line)
    k0, d0 = k_line[-1], d_line[-1]
    kd_le_20 = k0 is not None and d0 is not None and max(k0, d0) <= KDJ_LOW
    kdj_prev_min = min(_recent(j_line, 20, skip_last=1) or [None]) if _recent(j_line, 20, skip_last=1) else None
    kdj_not_new_low = (
        j_line[-1] is not None and kdj_prev_min is not None and j_line[-1] > kdj_prev_min
    )
    cond_kdj_watch = bool((kd_cross and kd_le_20) or kdj_not_new_low)

    if cond_macd_watch:
        base["hit_rules"].append("RULES §5 MACD：" + macd_watch_detail)
    else:
        base["missing_rules"].append("RULES §5 MACD：" + macd_watch_detail)

    if cond_kdj_watch:
        detail = []
        if kd_cross and kd_le_20:
            detail.append("K/D 低于或等于 20 金叉")
        if kdj_not_new_low:
            detail.append("KDJ 不创新低")
        base["hit_rules"].append("RULES §5 KDJ：" + "；".join(detail))
    else:
        notes = []
        if kd_cross and not kd_le_20:
            notes.append("有金叉但 K/D 未低于 20；「靠近 20」无量化阈值，不记为命中")
        if not kdj_not_new_low:
            notes.append("KDJ 创新低或窗口不足")
        if not notes:
            notes.append("K/D 未金叉且未满足不创新低")
        base["missing_rules"].append("RULES §5 KDJ：" + "；".join(notes))

    if not (cond_macd_watch and cond_kdj_watch):
        return base

    # §5 complete → 等待
    base["status"] = "等待"
    base["gate"] = "等待"
    base["summary_bucket"] = "继续跟踪"

    buy_cross, cross_detail = recent_dif_golden_cross(dif, dea)
    hist_green_to_red = _just_red(hist, len(hist) - 1) or _just_red(hist, len(hist) - 2)
    already_gold = last.get("dif") is not None and last.get("dea") is not None and last["dif"] > last["dea"]
    cont = False
    if len(hist) >= 3 and all(hist[i] is not None and hist[i] < 0 for i in (-3, -2, -1)):
        cont = abs(hist[-1]) < abs(hist[-2]) < abs(hist[-3])
    near_zero = h1 is not None and h1 < 0 and h0 is not None and abs(h1) < abs(h0)
    still_green_ok = _green_shrink_not_new_low(hist, len(hist) - 1)
    buy_hist = bool(cont and near_zero and still_green_ok) or hist_green_to_red or (buy_cross and already_gold)

    if buy_cross:
        base["hit_rules"].append("RULES §6：" + cross_detail)
    else:
        base["missing_rules"].append("RULES §6：" + cross_detail)

    if buy_hist:
        base["hit_rules"].append("RULES §6：绿柱连续缩短向 0 收敛 / 已金叉或已由绿转红")
    else:
        base["missing_rules"].append("RULES §6：绿柱未连续缩短向 0 收敛，且未见绿转红")

    near_low, near_detail = dif_near_20d_low(dif, last.get("dif"))
    if last.get("dif") is not None and _recent(dif, DIF_LOOKBACK):
        base["facts"]["dif_20_min"] = min(_recent(dif, DIF_LOOKBACK))
        base["facts"]["dif_to_20min"] = last["dif"] - min(_recent(dif, DIF_LOOKBACK))
    if near_low is True:
        base["hit_rules"].append("RULES §6：" + near_detail)
    elif near_low is False:
        base["missing_rules"].append("RULES §6：" + near_detail + "，不得买入")
    else:
        base["missing_rules"].append("RULES §6：" + near_detail)

    if buy_cross and buy_hist and near_low is True:
        base["status"] = "买入"
        base["gate"] = "买入"
        base["summary_bucket"] = "符合"
        base["hit_rules"].append("总闸到达买入（路径匹配，不是成交指令）")
    else:
        base["summary_bucket"] = "继续跟踪"

    open_trade = None
    for trade in trades or []:
        if ts_code(str(trade.get("code", ""))) == code and trade.get("direction") in ("开仓", "加仓"):
            open_trade = trade
    if open_trade:
        dif_down = last.get("dif") is not None and prev and prev.get("dif") is not None and last["dif"] < prev["dif"]
        hhv = max(_recent(h_line, HHV_LOOKBACK) or [last["high"]])
        new_high = last["high"] >= hhv
        kd_dead = _cross_down(k_line, d_line)
        kd_high = k0 is not None and d0 is not None and min(k0, d0) >= KDJ_HIGH
        j_prev_max = max(_recent(j_line, 20, skip_last=1) or [None]) if _recent(j_line, 20, skip_last=1) else None
        kdj_not_new_high = j_line[-1] is not None and j_prev_max is not None and j_line[-1] < j_prev_max
        kdj_exit = bool((kd_dead and kd_high) or kdj_not_new_high)
        notes = [
            f"手工开仓记录存在（仓位 {open_trade.get('position_pct')}%）",
            f"DIF下行={dif_down} 近20日新高={new_high} KDJ出势={kdj_exit}",
        ]
        if dif_down and new_high and kdj_exit:
            base["status"] = "清仓"
            base["gate"] = "清仓"
            base["hit_rules"].append("RULES §7 减仓/清仓条件已见：" + "；".join(notes))
        else:
            base["hit_rules"].append("RULES §7 对照未齐：" + "；".join(notes))

    return base


def scan_universe(universe: list[dict], settings: dict, trades: list[dict] | None = None) -> list[dict]:
    rows = [classify_stock(meta, settings, trades) for meta in universe]
    order = {name: i for i, name in enumerate(GATES)}
    rows.sort(key=lambda item: (order.get(item["status"], 9), item["code"]))
    return rows


def summarize(rows: list[dict]) -> dict:
    counts = {key: 0 for key in ("符合", "继续跟踪", "观察", "排除")}
    by_gate = {key: 0 for key in GATES}
    for row in rows:
        bucket = row.get("summary_bucket") or "排除"
        if bucket not in counts:
            bucket = "排除"
        counts[bucket] += 1
        gate = row.get("gate") or row.get("status") or "排除"
        if gate in by_gate:
            by_gate[gate] += 1
    return {"summary": counts, "by_gate": by_gate, "total": len(rows)}
