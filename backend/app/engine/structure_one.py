"""RULES2 回调后的重新启动. Independent of RULES.md low-golden engine."""
from __future__ import annotations

from ..config import CSV_DIR, DATA_DIR, GATES, POOL_MIN_PRICE
from ..store import load_universe, read_json
from .bars import load_bars, ts_code
from .indicators import sma
from .pool import is_st_name
from .scanner import FACT_NOTE, detect_limit_streak, dyn_pe_value
from .sector import load_sector_snap

YI = 100_000_000.0
INDUSTRY_MAP_PATH = DATA_DIR / "industry_map.json"


def _vol(row: dict) -> float:
    return float(row.get("volume") or 0)


def _amt(row: dict) -> float:
    raw = float(row.get("amount") or 0)
    if raw > 0:
        return raw
    return float(row.get("close") or 0) * _vol(row)


def _limit_pct(code: str) -> float:
    c = ts_code(code)
    if c.startswith(("3", "68")):
        return 0.20
    return 0.10


def _any_limit(bars: list[dict], code: str, n: int) -> bool:
    pct = _limit_pct(code)
    last = len(bars)
    for i in range(max(1, last - n), last):
        prev = bars[i - 1].get("close")
        cur = bars[i].get("close")
        if prev and cur and (cur / prev - 1.0) >= pct - 0.005:
            return True
    return False


def _load_industry_map() -> dict[str, str]:
    """只读本地缓存。扫描路径不联网拉板块，避免把规则页卡死。"""
    data = read_json(INDUSTRY_MAP_PATH, {})
    if isinstance(data, dict) and data.get("codes"):
        return {str(k): str(v) for k, v in data["codes"].items() if v}
    snap = load_sector_snap()
    out = {}
    for code, rec in (snap.get("stocks") or {}).items():
        if rec.get("industry"):
            out[ts_code(code)] = str(rec["industry"])
    return out


def _ret(a, b) -> float | None:
    if not a:
        return None
    return (b / a - 1.0) * 100.0


def find_structure(bars: list[dict]) -> dict | None:
    """近 20 日：先强（≥8% 且放量）后接 3～8 日缩量回调。先强段结束日须靠近该段高点。"""
    n = len(bars)
    if n < 25:
        return None
    win0 = max(0, n - 20)
    vols = [_vol(b) for b in bars[win0:]]
    avg20 = sum(vols) / len(vols) if vols else 0.0
    best = None
    for e in range(n - 4, win0, -1):
        pb_len = n - 1 - e
        if pb_len < 3 or pb_len > 8:
            continue
        # 从高点往回取最近一段先强（至少 3 根），不要把 20 日窗口里更早的阴跌算进均量
        for s in range(e - 2, win0 - 1, -1):
            start_c = bars[s].get("close")
            if not start_c:
                continue
            seg = bars[s : e + 1]
            run_high = max(x["high"] for x in seg)
            if run_high < start_c * 1.08:
                continue
            if bars[e]["high"] < run_high * 0.97:
                continue
            if avg20 and not any(_vol(x) >= avg20 for x in seg):
                continue
            pb = bars[e + 1 :]
            vol_up = sum(_vol(x) for x in seg) / len(seg)
            vol_dn = sum(_vol(x) for x in pb) / len(pb)
            rally_low = min(x["low"] for x in seg)
            pb_low = min(x["low"] for x in pb)
            cand = {
                "strong_start": s,
                "strong_end": e,
                "pb_len": pb_len,
                "run_high": run_high,
                "start_c": start_c,
                "vol_up": vol_up,
                "vol_dn": vol_dn,
                "shrink": vol_dn < vol_up,
                "rally_low": rally_low,
                "pb_low": pb_low,
                "gain_pct": (run_high - start_c) / start_c * 100.0,
            }
            best = cand
            break
        if best:
            break
    return best


def _key_zone(bars: list[dict], st: dict) -> dict:
    last = bars[-1]
    close = last["close"]
    low = last["low"]
    closes = [b["close"] for b in bars]
    ma10 = sma(closes, 10)
    ma20 = sma(closes, 20)
    m10, m20 = ma10[-1], ma20[-1]
    m10p = ma10[-2] if len(ma10) > 1 else None
    m20p = ma20[-2] if len(ma20) > 1 else None
    strong = bars[st["strong_start"] : st["strong_end"] + 1]
    pb_low = st["pb_low"]
    ma20_down = m20p is not None and m20 is not None and m20 < m20p

    def was_above(ma_line):
        for i, row in enumerate(strong):
            idx = st["strong_start"] + i
            mv = ma_line[idx] if idx < len(ma_line) else None
            if mv and row["close"] >= mv:
                return True
        return False

    a1_n = None
    a1_px = None
    a1_gap = None
    if m10 and m10p is not None and m10 >= m10p and was_above(ma10):
        a1_n, a1_px, a1_gap = 10, m10, abs(close / m10 - 1)
    if m20 and m20p is not None and m20 >= m20p and was_above(ma20):
        gap20 = abs(close / m20 - 1)
        if a1_px is None or gap20 < (a1_gap or 99):
            a1_n, a1_px, a1_gap = 20, m20, gap20

    at_a1 = a1_px is not None and a1_gap is not None and a1_gap <= 0.02
    at_a2 = bool(pb_low) and (abs(close / pb_low - 1) <= 0.02 or abs(low / pb_low - 1) <= 0.02)
    near_a2 = bool(pb_low) and close >= pb_low * 0.98 and close <= pb_low * 1.08

    kind = None
    price = None
    ma_n = None
    if at_a2 or (near_a2 and not at_a1):
        kind, price, ma_n = "A2", pb_low, None
    elif at_a1:
        kind, price, ma_n = "A1", a1_px, a1_n
    elif near_a2:
        kind, price, ma_n = "A2", pb_low, None

    return {
        "kind": kind,
        "ma_n": ma_n,
        "price": price,
        "stop": price,
        "ma20": m20,
        "ma20_down": ma20_down,
        "at_key": bool(at_a1 or at_a2),
        "a1_price": a1_px,
        "a1_n": a1_n,
        "a2_price": pb_low,
        "run_high": st.get("run_high"),
        "near_a2": near_a2,
    }


def _shadow_ge_body(row: dict) -> bool:
    o, h, l, c = row["open"], row["high"], row["low"], row["close"]
    body = abs(c - o)
    upper = h - max(o, c)
    return upper + 1e-12 >= body


def _small_or_hammer(row: dict) -> bool:
    o, h, l, c = row["open"], row["high"], row["low"], row["close"]
    body = abs(c - o)
    rng = h - l
    lower = min(o, c) - l
    if c > o and body <= max(c * 0.012, 0.02):
        return True
    if rng and body / rng <= 0.25:
        return True
    if body and lower >= body:
        return True
    return False


def _industry_stats(cands: list[dict]) -> dict[str, dict]:
    groups: dict[str, list] = {}
    for item in cands:
        ind = item.get("industry")
        if not ind:
            continue
        groups.setdefault(ind, []).append(item)
    out = {}
    for ind, items in groups.items():
        r3, r20, bounce = [], [], []
        daily_rows: list[list] = []
        for it in items:
            bars = it["bars"]
            if len(bars) < 21:
                continue
            r3.append(_ret(bars[-4]["close"], bars[-1]["close"]))
            r20.append(_ret(bars[-21]["close"], bars[-1]["close"]))
            lo = min(x["low"] for x in bars[-20:])
            hi = max(x["high"] for x in bars[-20:])
            if lo:
                bounce.append((hi - lo) / lo * 100.0)
            day = []
            for i in range(-20, 0):
                day.append(_ret(bars[i - 1]["close"], bars[i]["close"]))
            daily_rows.append(day)
        r3 = [x for x in r3 if x is not None]
        r20 = [x for x in r20 if x is not None]
        daily = []
        if daily_rows:
            for j in range(20):
                vals = [row[j] for row in daily_rows if j < len(row) and row[j] is not None]
                daily.append(sum(vals) / len(vals) if vals else None)
        out[ind] = {
            "n": len(items),
            "ret_3d": sum(r3) / len(r3) if r3 else None,
            "ret_20d": sum(r20) / len(r20) if r20 else None,
            "bounce": sum(bounce) / len(bounce) if bounce else None,
            "daily": daily,
        }
    return out


def _stock_vs_board(bars: list[dict], ind: dict | None) -> bool:
    if not ind or len(bars) < 21:
        return False
    r20 = _ret(bars[-21]["close"], bars[-1]["close"])
    lo = min(x["low"] for x in bars[-20:])
    hi = max(x["high"] for x in bars[-20:])
    bounce = (hi - lo) / lo * 100.0 if lo else None
    if r20 is not None and ind.get("ret_20d") is not None and r20 >= ind["ret_20d"]:
        return True
    if bounce is not None and ind.get("bounce") is not None and bounce >= ind["bounce"]:
        return True
    daily = ind.get("daily") or []
    if len(daily) >= 20:
        good = 0
        for j, i in enumerate(range(-20, 0)):
            sr = _ret(bars[i - 1]["close"], bars[i]["close"])
            br = daily[j] if j < len(daily) else None
            if sr is not None and br is not None and sr >= br:
                good += 1
        return good >= 10
    return False


def _choose_kind(zone: dict) -> tuple[str | None, float | None, int | None]:
    """只认一种关键位：能写 A2 平台下沿则用 A2（样本止损前低），否则 A1。"""
    if zone.get("near_a2") and zone.get("a2_price"):
        return "A2", zone["a2_price"], None
    if zone.get("kind") == "A1" and zone.get("a1_price"):
        return "A1", zone["a1_price"], zone.get("a1_n")
    if zone.get("a2_price"):
        return "A2", zone["a2_price"], None
    if zone.get("a1_price"):
        return "A1", zone["a1_price"], zone.get("a1_n")
    return None, None, None


def classify_s1(
    meta: dict,
    settings: dict,
    trades: list | None,
    industry_stats: dict,
    market_3d: float | None,
    structure_only: bool = False,
) -> dict:
    code = ts_code(str(meta.get("code") or ""))
    name = meta.get("name") or code
    bars = meta.get("bars") or load_bars(code, last_n=80)
    base = {
        "code": code,
        "name": name,
        "status": "排除",
        "gate": "排除",
        "summary_bucket": "排除",
        "path": "回调再启动",
        "hit_rules": [],
        "missing_rules": [],
        "reminders": [],
        "veto": [],
        "risk": [],
        "facts": {},
        "fact_note": FACT_NOTE,
        "person_present": bool(settings.get("person_present", True)),
        "market_regime": settings.get("market_regime") or "未设置",
        "path_ready": False,
        "data_ok": False,
        "index_member": meta.get("index_member") or [],
        "tags": meta.get("tags") or [],
        "key_kind": None,
        "key_price": None,
        "stop_price": None,
    }
    if len(bars) < 25:
        base["missing_rules"].append("数据不足：日线不足以核对结构一，排除")
        return base
    last = bars[-1]
    close = last["close"]
    name = (last.get("name") or name or "").strip() or code
    if name == code and (meta.get("name") or "").strip() and meta.get("name") != code:
        name = str(meta["name"]).strip()
    base["name"] = name
    base["facts"] = {"date": last["date"], "close": close, "pe": dyn_pe_value(meta)}
    if is_st_name(name):
        base["veto"] = ["ST / *ST"]
        return base
    if close < POOL_MIN_PRICE:
        base["missing_rules"].append(f"第3条 底池：股价 {close:.2f} < 5 元")
        return base
    amt = _amt(last)
    if amt < YI:
        base["missing_rules"].append(f"第3条 底池：成交额 {amt / YI:.2f} 亿 < 1 亿")
        return base
    industry = meta.get("industry")
    if industry:
        base["industry"] = industry
        base["facts"]["industry"] = industry
    if not industry:
        base["missing_rules"].append("第3条 底池：无板块归属")
        return base
    pe = dyn_pe_value(meta)
    if pe is not None and pe <= 0:
        base["veto"] = [f"动态市盈 {pe:.2f} ≤ 0"]
        return base
    if detect_limit_streak(bars, code) >= 2:
        base["veto"] = ["连板"]
        return base
    tags = meta.get("tags") or []
    banned = [t for t in tags if t in ("打板", "连板", "高位接力", "隔夜情绪票", "小盘题材", "连板妖股", "游资票")]
    if banned:
        base["veto"] = banned
        return base

    ind = industry_stats.get(industry) or {}
    if ind.get("ret_3d") is not None:
        base["facts"]["board_ret_3d"] = round(ind["ret_3d"], 2)
    if market_3d is not None:
        base["facts"]["market_ret_3d"] = round(market_3d, 2)
        if ind.get("ret_3d") is not None:
            base["facts"]["vs_market"] = round(ind["ret_3d"] - market_3d, 2)
    if not structure_only:
        if ind.get("ret_3d") is None:
            base["missing_rules"].append(f"第3条 板块池：{industry} 近3日涨幅证据不足，出池")
            return base
        if market_3d is not None and ind.get("ret_3d") is not None:
            if ind["ret_3d"] < market_3d:
                base["missing_rules"].append(
                    f"第3条 板块池：{industry} 近3日 {ind['ret_3d']:.2f}% < 沪深300 {market_3d:.2f}%，整板块出池"
                )
                return base
            base["hit_rules"].append(
                f"第3条 板块池：{industry} 近3日 {ind['ret_3d']:.2f}% ≥ 沪深300 {market_3d:.2f}%"
            )
        elif market_3d is None:
            base["reminders"].append("第3条 板块池：沪深300近3日未知，相对大盘未核，不挡筛选")
        rets = [v["ret_3d"] for v in industry_stats.values() if v.get("ret_3d") is not None and v.get("n", 0) >= 2]
        if len(rets) >= 2 and ind.get("ret_3d") is not None and ind["ret_3d"] <= min(rets) + 1e-12:
            base["missing_rules"].append(f"第3条 板块池：{industry} 近3日为最弱一档，整板块出池")
            return base
        if not _stock_vs_board(bars, ind):
            base["missing_rules"].append(f"第3条 个股相对 {industry} 偏弱，出池")
            return base
        base["hit_rules"].append(f"第3条 个股相对 {industry} 不弱")

    if _any_limit(bars, code, 3):
        base["veto"] = ["近3个交易日出现涨停"]
        return base
    if len(bars) >= 6:
        r5 = _ret(bars[-6]["close"], close)
        if r5 is not None and r5 >= 20:
            base["veto"] = [f"近5日涨幅 {r5:.1f}% ≥ 20%"]
            return base
    vols = [_vol(b) for b in bars[-20:]]
    avg20 = sum(vols) / len(vols) if vols else 0
    if avg20 and _vol(last) >= avg20 * 2 and _shadow_ge_body(last):
        base["veto"] = ["放量上影（当日量≥20日均量×2 且上影≥实体）"]
        return base
    closes = [b["close"] for b in bars]
    ma20_line = sma(closes, 20)
    m20 = ma20_line[-1]
    if m20 and (close - m20) / m20 > 0.08:
        base["veto"] = [f"收盘距20日线 {(close - m20) / m20 * 100:.1f}% > 8%，沿5日加速"]
        return base

    st = find_structure(bars)
    if not st:
        base["missing_rules"].append("第3条 结构预筛：未见近20日先强后 3～8 日缩量回调")
        return base
    if not st["shrink"]:
        base["veto"] = ["回踩不缩量"]
        return base
    if st["pb_low"] < st["rally_low"] * 0.98:
        base["veto"] = [f"最新低 {st['pb_low']:.2f} < 前低 {st['rally_low']:.2f} × 0.98"]
        return base

    zone = _key_zone(bars, st)
    kind, key_px, ma_n = _choose_kind(zone)
    if kind == "A1" and (zone.get("ma20_down") or (zone.get("ma20") and close < zone["ma20"])):
        base["veto"] = ["A1：20日线向下或收盘已在20日线下方"]
        return base
    if not kind or not key_px:
        base["missing_rules"].append("第3条 关键位未写明")
        return base

    at_key = bool(zone.get("at_key"))
    # 买入日可以离开 ±2% 关键位；观察日必须还在附近
    last = bars[-1]
    decay = []
    if st["shrink"]:
        decay.append("回调段日均量 < 先强段")
    if _small_or_hammer(last):
        decay.append("触关键位收小阳/十字/长下影")
    prev_low = bars[-2]["low"] if len(bars) > 1 else None
    if prev_low and last["low"] < prev_low and last["close"] >= prev_low:
        decay.append("盘中击穿当日收回")
    if last["low"] >= st["pb_low"] - 1e-9:
        decay.append("不创新低")

    yang = last["close"] > last["open"]
    pb_vols = [_vol(x) for x in bars[st["strong_end"] + 1 : -1]]
    vol_dn_ex = (sum(pb_vols) / len(pb_vols)) if pb_vols else st["vol_dn"]
    vol_up = _vol(last) > vol_dn_ex
    stand_a1 = kind == "A1" and last["close"] >= key_px
    stand_a2 = kind == "A2" and last["close"] >= key_px
    vs_board = False
    chg = _ret(bars[-2]["close"], last["close"]) if len(bars) > 1 else None
    if chg is not None and chg > 0 and ind.get("ret_3d") is not None:
        vs_board = chg > (ind["ret_3d"] / 3.0)
    demand = []
    if yang:
        demand.append("收阳")
    if vol_up:
        demand.append("当日量 > 回调段日均量")
    if stand_a1:
        demand.append(f"收盘站回{ma_n}日线内侧")
    if stand_a2:
        demand.append("收盘站回调整低点之上")
    if vs_board:
        demand.append("当日涨幅 > 0 且高于所属板块")
    demand_ready = len(demand) >= 2

    if not at_key and not demand_ready:
        base["missing_rules"].append("第3条 现价未落入关键位 ±2%（A1 均线 / A2 调整低点）")
        return base

    base["data_ok"] = True
    base["key_kind"] = kind
    base["key_price"] = round(key_px, 3)
    base["stop_price"] = round(key_px, 3)
    base["facts"]["key_kind"] = kind
    base["facts"]["key_price"] = round(key_px, 3)
    base["facts"]["stop_price"] = round(key_px, 3)
    base["facts"]["industry"] = industry
    base["status"] = "观察"
    base["gate"] = "观察"
    base["summary_bucket"] = "观察"
    base["hit_rules"].append(
        f"第3条 结构：先强 {st['gain_pct']:.1f}% 后缩量回调 {st['pb_len']} 日；{kind} 关键位 {key_px:.2f}，止损 {key_px:.2f}"
    )

    if not decay:
        base["missing_rules"].append("第5条 卖压衰减未见到")
        return base
    base["hit_rules"].append("第5条 卖压衰减：" + "；".join(decay))

    if not demand_ready:
        base["missing_rules"].append("第6条 需求转强未满 2 项（" + "、".join(demand or ["无"]) + "）")
        return base
    base["hit_rules"].append("第6条 需求转强：" + "；".join(demand))
    base["path_ready"] = True
    base["status"] = "买入"
    base["gate"] = "买入"
    base["summary_bucket"] = "买入"
    base["hit_rules"].append("第6条 路径到达买入。买入不是成交指令")

    if not settings.get("person_present", True):
        base["status"] = "观察"
        base["gate"] = "观察"
        base["summary_bucket"] = "观察"
        base["risk"].append("人不在场：只输出观察，不升买入")
        return base

    open_trade = None
    for trade in trades or []:
        if ts_code(str(trade.get("code", ""))) == code and trade.get("direction") in ("开仓", "加仓"):
            open_trade = trade
    if open_trade:
        hit, section, detail = evaluate_exit_s1(bars, open_trade, {**zone, "kind": kind, "stop": key_px, "price": key_px})
        if hit:
            base["status"] = "卖出"
            base["gate"] = "卖出"
            base["summary_bucket"] = "卖出"
            base["hit_rules"].append(f"第{section}条 卖出已见：{detail}")
    return base


def evaluate_exit_s1(bars: list[dict], open_trade: dict | None, zone: dict | None) -> tuple[bool, str, str]:
    if not bars:
        return False, "", ""
    entry_idx = len(bars) - 1
    trade_date = str((open_trade or {}).get("date") or (open_trade or {}).get("buy_date") or "")
    if trade_date:
        for i, row in enumerate(bars):
            if str(row.get("date")) >= trade_date:
                entry_idx = i
                break
    if len(bars) - 1 <= entry_idx:
        return False, "", ""
    last = bars[-1]
    entry = bars[entry_idx]
    kind = (zone or {}).get("kind")
    stop = (zone or {}).get("stop") or (zone or {}).get("price")
    run_high = (zone or {}).get("run_high")
    if kind == "A1" and stop and last["close"] < stop:
        return True, "7.1", f"收盘跌破买入时所写均线 {stop:.2f}"
    if kind == "A2" and stop and last["close"] < stop:
        return True, "7.1", f"收盘跌破平台下沿/前低 {stop:.2f}"
    if last["close"] < entry["low"]:
        return True, "7.1", f"收盘跌破买入日最低价 {entry['low']:.2f}"
    vols5 = [_vol(b) for b in bars[-5:]]
    avg5 = sum(vols5) / len(vols5) if vols5 else 0
    prev_closes = [x["close"] for x in bars[entry_idx : len(bars) - 1]]
    if prev_closes and last["close"] < min(prev_closes) and avg5 and _vol(last) >= avg5:
        return True, "7.1", "收盘再创新低且当日量 ≥ 近5日均量"
    if len(bars) - 1 - entry_idx >= 15 and run_high and last["close"] < run_high:
        return True, "7.1", "买入后15个交易日仍未收盘站上观察日后区间高点"

    code = str(last.get("code") or (open_trade or {}).get("code") or "")
    pct = _limit_pct(code)
    had_event = False
    for i in range(entry_idx, len(bars) - 1):
        if i > 0:
            prev = bars[i - 1]["close"]
            if prev and (bars[i]["close"] / prev - 1) >= pct - 0.005:
                had_event = True
        if i - 3 >= entry_idx:
            r3 = _ret(bars[i - 3]["close"], bars[i]["close"])
            if r3 is not None and r3 >= 25:
                had_event = True
    prior_high = max(x["high"] for x in bars[entry_idx : len(bars) - 1])
    if had_event and last["high"] <= prior_high + 1e-12 and last["close"] < prior_high:
        return True, "7.2", "持仓后涨停或近3日大涨后，收盘不再创新高"
    if len(bars) >= 2:
        a, b = bars[-2], bars[-1]
        if a["close"] < a["open"] and b["close"] < b["open"]:
            if _vol(a) >= _vol(entry) and _vol(b) >= _vol(entry):
                return True, "7.2", "连续2日收阴且两日量都 ≥ 买入日量"
    if avg5 and _vol(last) >= avg5 and last["high"] < prior_high and last["close"] < prior_high:
        return True, "7.2", "本波高点过后收盘不再创新高，且量 ≥ 近5日均量"
    return False, "", ""


def is_buy_s1(bars: list[dict]) -> bool:
    if not bars:
        return False
    meta = {
        "code": bars[-1].get("code") or "",
        "name": bars[-1].get("name") or "",
        "bars": bars,
        "industry": "结构回放",
    }
    row = classify_s1(meta, {"person_present": True}, None, {}, None, structure_only=True)
    return row.get("status") == "买入"


def classify_one_s1(code: str, settings: dict, trades: list | None = None) -> dict:
    code = ts_code(code)
    uni = {ts_code(str(x.get("code") or "")): x for x in load_universe()}
    imap = _load_industry_map()
    bars = load_bars(code, last_n=80)
    if not bars:
        return {
            "code": code,
            "name": code,
            "status": "排除",
            "gate": "排除",
            "hit_rules": [],
            "missing_rules": ["数据不足：没有本地 CSV"],
            "facts": {},
            "fact_note": FACT_NOTE,
        }
    last = bars[-1]
    name = last.get("name") or (uni.get(code) or {}).get("name") or code
    industry = imap.get(code) or (uni.get(code) or {}).get("industry")
    meta = dict(uni.get(code) or {})
    meta.update({"code": code, "name": name, "bars": bars, "industry": industry})
    snap = load_sector_snap()
    market_3d = (snap.get("market") or {}).get("ret_3d_pct")
    stats = _industry_stats([meta])
    return classify_s1(meta, settings, trades, stats, market_3d)


def list_s1_cycle_universe() -> list[dict]:
    """轨迹回放底池：非 ST、股价≥5、最近成交额≥1亿。不挡板块（回放不看当日板块快照）。"""
    uni = {ts_code(str(x.get("code") or "")): x for x in load_universe()}
    out = []
    for path in CSV_DIR.glob("*.csv"):
        code = path.stem
        bars = load_bars(code, last_n=2)
        if len(bars) < 1:
            continue
        last = bars[-1]
        name = last.get("name") or (uni.get(code) or {}).get("name") or code
        if is_st_name(name):
            continue
        if last["close"] < POOL_MIN_PRICE:
            continue
        if _amt(last) < YI:
            continue
        out.append({"code": code, "name": name})
    return out


def list_s1_pool() -> list[dict]:
    """RULES2 第3.1条底池，不走 RULES.md 300亿/5亿宇宙。"""
    uni = {ts_code(str(x.get("code") or "")): x for x in load_universe()}
    imap = _load_industry_map()
    cands = []
    for path in CSV_DIR.glob("*.csv"):
        code = path.stem
        bars = load_bars(code, last_n=80)
        if len(bars) < 25:
            continue
        last = bars[-1]
        name = last.get("name") or (uni.get(code) or {}).get("name") or code
        if is_st_name(name):
            continue
        if last["close"] < POOL_MIN_PRICE:
            continue
        if _amt(last) < YI:
            continue
        industry = imap.get(code) or (uni.get(code) or {}).get("industry")
        if not industry:
            continue
        meta = dict(uni.get(code) or {})
        meta.update({"code": code, "name": name, "bars": bars, "industry": industry})
        cands.append(meta)
    return cands


def board_funnel(industry_stats: dict, market_3d: float | None) -> list[dict]:
    """第3.2条：先筛板块，再给个股用。"""
    ranked = [v["ret_3d"] for v in industry_stats.values() if v.get("ret_3d") is not None and v.get("n", 0) >= 2]
    weakest = min(ranked) if ranked else None
    out = []
    for name, st in industry_stats.items():
        r3 = st.get("ret_3d")
        vs = None if r3 is None or market_3d is None else r3 - market_3d
        passed = True
        reason = "近3日相对大盘不弱"
        if r3 is None:
            passed = False
            reason = "近3日涨幅证据不足"
        elif market_3d is not None and r3 < market_3d:
            passed = False
            reason = f"近3日 {r3:.2f}% < 沪深300 {market_3d:.2f}%"
        elif weakest is not None and len(ranked) >= 2 and r3 <= weakest + 1e-12:
            passed = False
            reason = "近3日为全市场最弱一档"
        elif market_3d is None:
            reason = "沪深300近3日未知，未挡板块"
        else:
            reason = f"近3日 {r3:.2f}% ≥ 沪深300 {market_3d:.2f}%"
        out.append(
            {
                "name": name,
                "n": st.get("n") or 0,
                "ret_3d": None if r3 is None else round(r3, 2),
                "market_ret_3d": None if market_3d is None else round(market_3d, 2),
                "vs_market": None if vs is None else round(vs, 2),
                "pass": passed,
                "reason": reason,
            }
        )
    out.sort(key=lambda x: (0 if x["pass"] else 1, -(x["ret_3d"] if x["ret_3d"] is not None else -999)))
    return out


def scan_structure_one(settings: dict, trades: list | None = None) -> list[dict]:
    cands = list_s1_pool()
    snap = load_sector_snap()
    market_3d = (snap.get("market") or {}).get("ret_3d_pct")
    stats = _industry_stats(cands)
    funnel = board_funnel(stats, market_3d)
    scan_structure_one.funnel = funnel
    scan_structure_one.market = {
        "name": (snap.get("market") or {}).get("name") or "沪深300",
        "ret_3d_pct": market_3d,
    }
    rows = [classify_s1(item, settings, trades, stats, market_3d) for item in cands]
    order = {name: i for i, name in enumerate(GATES)}
    rows.sort(key=lambda item: (order.get(item["status"], 9), item.get("industry") or "", item["code"]))
    return rows
