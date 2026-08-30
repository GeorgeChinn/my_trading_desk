from __future__ import annotations

from typing import Optional

from ..config import (
    DIF_LOOKBACK,
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


def _cross_up(fast: list, slow: list) -> bool:
    if len(fast) < 2 or len(slow) < 2:
        return False
    a0, a1 = fast[-2], fast[-1]
    b0, b1 = slow[-2], slow[-1]
    if None in (a0, a1, b0, b1):
        return False
    return a0 <= b0 and a1 > b1


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
        "veto": [],
        "risk": [],
        "facts": {},
        "fact_note": FACT_NOTE,
        "person_present": person_present,
        "market_regime": market_regime,
        "position_block": "仓位阈值空缺，不得升到试仓/标准仓",
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
        base["status"] = "禁止"
        base["gate"] = "禁止"
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

    if market_regime == "未设置":
        base["missing_rules"].append("漏斗第1项大盘未定性（只定性，不编造）")
        base["risk"].append("大盘证据不足")
    else:
        base["hit_rules"].append(f"大盘开关（人工定性）：{market_regime}")

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
    green_now = h1 is not None and h1 < 0
    shrink = h0 is not None and h1 is not None and abs(h1) < abs(h0) and h1 < 0 and h0 < 0
    prev_hist = _recent(hist, 20, skip_last=1)
    not_new_low = bool(prev_hist) and h1 is not None and h1 > min(prev_hist)
    cond_macd_watch = bool(green_now and shrink and not_new_low)

    kd_cross = _cross_up(k_line, d_line)
    k0, d0 = k_line[-1], d_line[-1]
    kd_le_20 = k0 is not None and d0 is not None and max(k0, d0) <= KDJ_LOW
    kdj_prev_min = min(_recent(j_line, 20, skip_last=1) or [None]) if _recent(j_line, 20, skip_last=1) else None
    kdj_not_new_low = (
        j_line[-1] is not None and kdj_prev_min is not None and j_line[-1] > kdj_prev_min
    )
    cond_kdj_watch = bool((kd_cross and kd_le_20) or kdj_not_new_low)

    if cond_macd_watch:
        base["hit_rules"].append("RULES §5：MACD 绿柱缩短且不创新低")
    else:
        why = []
        if not green_now:
            why.append("当前不是绿柱")
        elif not shrink:
            why.append("绿柱未缩短")
        if not not_new_low:
            why.append("绿柱创新低或窗口不足")
        base["missing_rules"].append("RULES §5 MACD：" + "，".join(why) if why else "RULES §5 MACD 未齐")

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

    # §5 complete → 等待. Never skip to 试仓/标准仓.
    base["status"] = "等待"
    base["gate"] = "等待"
    base["summary_bucket"] = "继续跟踪"

    buy_cross = _cross_up(dif, dea)
    hist_green_to_red = h0 is not None and h1 is not None and h0 < 0 <= h1
    already_gold = last.get("dif") is not None and last.get("dea") is not None and last["dif"] > last["dea"]
    # 连续缩短、向 0 收敛：最近 3 根绿柱绝对值递减
    cont = False
    if len(hist) >= 3 and all(hist[i] is not None and hist[i] < 0 for i in (-3, -2, -1)):
        cont = abs(hist[-1]) < abs(hist[-2]) < abs(hist[-3])
    near_zero = h1 is not None and h1 < 0 and abs(h1) < abs(h0 or h1)
    buy_hist = bool(cont and near_zero and not_new_low) or hist_green_to_red or (buy_cross and already_gold)

    if buy_cross:
        base["hit_rules"].append("RULES §6：DIF 上穿 DEA（金叉）")
    else:
        base["missing_rules"].append("RULES §6：DIF 尚未上穿 DEA")

    if buy_hist:
        base["hit_rules"].append("RULES §6：绿柱连续缩短向 0 收敛 / 已金叉或已由绿转红")
    else:
        base["missing_rules"].append("RULES §6：绿柱未连续缩短向 0 收敛，且未见绿转红")

    # 「靠近近 20 日 DIF 最小值」—— RULES 没有量化阈值，不得自行发明。
    dif_window = _recent(dif, DIF_LOOKBACK)
    if dif_window and last.get("dif") is not None:
        dif_min = min(dif_window)
        base["facts"]["dif_20_min"] = dif_min
        base["facts"]["dif_to_20min"] = last["dif"] - dif_min
        base["missing_rules"].append(
            "RULES §6：DIF「靠近」近 20 日最小值无量化阈值（当前 DIF 与窗口最低差 "
            f"{last['dif'] - dif_min:.4f}），不得升到试仓/标准仓"
        )
    else:
        base["missing_rules"].append("RULES §6：近 20 日 DIF 窗口不足，证据不足")

    base["missing_rules"].append("RULES 未给出试仓/标准仓仓位数字，阈值空缺不得升到试仓/标准仓")

    if buy_cross and buy_hist:
        base["summary_bucket"] = "符合"
        base["hit_rules"].append("第6条可核对项已见，但仓位档与「靠近」阈值空缺，总闸停在等待")
    else:
        base["summary_bucket"] = "继续跟踪"

    # §7 仅对照已有手工记账，不新增状态词
    open_trade = None
    for trade in trades or []:
        if ts_code(str(trade.get("code", ""))) == code and trade.get("direction") in ("开仓", "加仓"):
            open_trade = trade
    if open_trade:
        red_now = h1 is not None and h1 > 0
        red_hist = [x for x in hist if x is not None]
        # previous red-wave peak vs current red-wave length: report facts only
        dif_down = last.get("dif") is not None and prev and prev.get("dif") is not None and last["dif"] < prev["dif"]
        hhv = max(_recent(h_line, HHV_LOOKBACK) or [last["high"]])
        new_high = last["high"] >= hhv
        kd_dead = _cross_down(k_line, d_line)
        kd_high = k0 is not None and d0 is not None and min(k0, d0) >= KDJ_HIGH
        j_prev_max = max(_recent(j_line, 20, skip_last=1) or [None]) if _recent(j_line, 20, skip_last=1) else None
        kdj_not_new_high = j_line[-1] is not None and j_prev_max is not None and j_line[-1] < j_prev_max
        notes = [
            f"手工开仓记录存在（仓位 {open_trade.get('position_pct')}%）",
            f"红柱={red_now} DIF下行={dif_down} 近20日新高={new_high}",
            f"KDJ死叉={kd_dead} K/D≥80={kd_high} KDJ不创新高={kdj_not_new_high}",
        ]
        base["hit_rules"].append("RULES §7 对照（非新状态）：" + "；".join(notes))

    return base


def scan_universe(universe: list[dict], settings: dict, trades: list[dict] | None = None) -> list[dict]:
    rows = [classify_stock(meta, settings, trades) for meta in universe]
    order = {"禁止": 0, "排除": 1, "观察": 2, "等待": 3, "试仓": 4, "标准仓": 5}
    rows.sort(key=lambda item: (order.get(item["status"], 9), item["code"]))
    return rows


def summarize(rows: list[dict]) -> dict:
    counts = {key: 0 for key in ("符合", "继续跟踪", "观察", "排除")}
    by_gate = {key: 0 for key in ("排除", "观察", "等待", "试仓", "标准仓", "禁止")}
    for row in rows:
        bucket = row.get("summary_bucket") or "排除"
        if bucket not in counts:
            bucket = "排除"
        counts[bucket] += 1
        gate = row.get("gate") or row.get("status") or "排除"
        if gate in by_gate:
            by_gate[gate] += 1
    return {"summary": counts, "by_gate": by_gate, "total": len(rows)}
