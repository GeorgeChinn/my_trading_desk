"""RULES2 回调后的重新启动. Independent of RULES.md low-golden engine."""
from __future__ import annotations

from ..config import CSV_DIR, DATA_DIR, GATES, POOL_MIN_PRICE
from ..store import load_quotes, load_universe, read_json
from .bars import load_bars, parse_amount, peek_last_bar, ts_code
from .indicators import sma
from .pool import is_st_name
from .scanner import FACT_NOTE, detect_limit_streak, dyn_pe_value
from .sector import load_sector_snap

YI = 100_000_000.0
MIN_FLOAT_YI = 80.0
INDUSTRY_MAP_PATH = DATA_DIR / "industry_map.json"
MISSING_NO_BUY = "缺数据，不升买入"


def _vol(row: dict) -> float:
    return float(row.get("volume") or 0)


def _amt(row: dict) -> float | None:
    return parse_amount(row.get("amount") if row else None)


def _float_mcap_yi(meta: dict, last: dict | None = None) -> float | None:
    blob = dict(meta or {})
    if last:
        blob.setdefault("float_mcap_yi", last.get("float_mcap_yi"))
    for key in ("float_mcap_yi", "float_mcap", "circ_mv", "流通市值", "mcap"):
        raw = blob.get(key)
        if raw is None or raw == "":
            continue
        try:
            val = float(raw)
        except (TypeError, ValueError):
            continue
        if val <= 0:
            continue
        if key != "float_mcap_yi" and val > 10000:
            return val / YI
        return val
    return None


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
    """近 20 日：先强收盘涨幅≥12%、段长 5～12 日，后接 4～8 日缩量（日均量≤先强×0.7）。"""
    n = len(bars)
    if n < 25:
        return None
    win0 = max(0, n - 20)
    vols = [_vol(b) for b in bars[win0:]]
    avg20 = sum(vols) / len(vols) if vols else 0.0
    best = None
    # 回调段不含最后一根（候选买日），避免 8/20 放量把缩量洗没。
    for e in range(n - 6, win0 + 3, -1):
        pb_len = n - 2 - e
        if pb_len < 4 or pb_len > 8:
            continue
        s_hi = e - 4
        s_lo = max(win0, e - 11)
        for s in range(s_hi, s_lo - 1, -1):
            start_c = bars[s].get("close")
            if not start_c:
                continue
            seg = bars[s : e + 1]
            run_close_high = max(x["close"] for x in seg)
            run_high = max(x["high"] for x in seg)
            if run_close_high < start_c * 1.12:
                continue
            if bars[e]["close"] < run_close_high * 0.97:
                continue
            if avg20 and not any(_vol(x) >= avg20 for x in seg):
                continue
            yang = [x for x in seg if x["close"] > x["open"]]
            yin = [x for x in seg if x["close"] < x["open"]]
            if yin:
                y_avg = (sum(_vol(x) for x in yang) / len(yang)) if yang else 0.0
                n_avg = sum(_vol(x) for x in yin) / len(yin)
                y_sum = sum(_vol(x) for x in yang)
                n_sum = sum(_vol(x) for x in yin)
                # 收阳日均量 ≥ 收阴日均量。一根洗盘阴把日均量抬高时，仍要求阳量合计不低于阴量合计。
                if y_avg < n_avg and y_sum < n_sum:
                    continue
            pb = bars[e + 1 : n - 1]
            if not pb:
                continue
            if max(x["close"] for x in pb) > run_close_high:
                continue
            vol_up = sum(_vol(x) for x in seg) / len(seg)
            vol_dn = sum(_vol(x) for x in pb) / len(pb)
            rally_low = min(x["low"] for x in seg)
            pb_low = min(x["low"] for x in pb)
            pre0 = max(0, s - 3)
            pre_low = min(x["low"] for x in bars[pre0:s]) if s > pre0 else bars[s]["low"]
            cand = {
                "strong_start": s,
                "strong_end": e,
                "pb_len": pb_len,
                "run_high": run_high,
                "run_close_high": run_close_high,
                "start_c": start_c,
                "vol_up": vol_up,
                "vol_dn": vol_dn,
                "shrink": vol_dn <= vol_up * 0.7 + 1e-12,
                "rally_low": rally_low,
                "pb_low": pb_low,
                "pre_low": pre_low,
                "a2_price": pre_low,
                "gain_pct": (run_close_high - start_c) / start_c * 100.0,
            }
            best = cand
            break
        if best:
            break
    return best


def _within_pct(px, key, pct=0.02) -> bool:
    if not px or not key:
        return False
    return abs(px / key - 1.0) <= pct


def _key_zone(bars: list[dict], st: dict) -> dict:
    last = bars[-1]
    close = last["close"]
    low = last["low"]
    closes = [b["close"] for b in bars]
    ma20 = sma(closes, 20)
    m20 = ma20[-1]
    m20p = ma20[-2] if len(ma20) > 1 else None
    strong = bars[st["strong_start"] : st["strong_end"] + 1]
    a2 = st.get("a2_price") or st.get("pre_low") or st.get("pb_low")
    ma20_down = m20p is not None and m20 is not None and m20 < m20p

    def was_above_ma20():
        for i, row in enumerate(strong):
            idx = st["strong_start"] + i
            mv = ma20[idx] if idx < len(ma20) else None
            if mv and row["close"] >= mv:
                return True
        return False

    a1_px = m20
    at_a1 = m20 is not None and close >= m20
    kind = None
    price = None
    ma_n = None
    stop = None
    if m20:
        kind, price, ma_n = "A1", m20, 20
        stop = m20 * 0.95

    return {
        "kind": kind,
        "ma_n": ma_n,
        "price": price,
        "stop": stop,
        "buy_ma20": m20,
        "ma20": m20,
        "ma20_down": ma20_down,
        "at_key": bool(at_a1),
        "a1_price": a1_px,
        "a1_n": 20 if a1_px else None,
        "a2_price": a2,
        "run_high": st.get("run_close_high") or st.get("run_high"),
        "near_a2": False,
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
        hot_code = None
        hot_r5 = None
        for it in items:
            b = it["bars"]
            if len(b) < 6:
                continue
            r5 = _ret(b[-6]["close"], b[-1]["close"])
            if r5 is None:
                continue
            if hot_r5 is None or r5 > hot_r5:
                hot_r5 = r5
                hot_code = ts_code(str(it.get("code") or ""))
        out[ind] = {
            "n": len(items),
            "ret_3d": sum(r3) / len(r3) if r3 else None,
            "ret_20d": sum(r20) / len(r20) if r20 else None,
            "bounce": sum(bounce) / len(bounce) if bounce else None,
            "daily": daily,
            "hot_code": hot_code,
            "hot_r5": None if hot_r5 is None else round(hot_r5, 2),
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
    """买点只认 20 日线。"""
    if zone.get("ma20"):
        return "A1", zone["ma20"], 20
    if zone.get("a1_price"):
        return "A1", zone["a1_price"], 20
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
    pe = dyn_pe_value(meta)
    mcap = _float_mcap_yi(meta, last)
    data_gap = []
    if not structure_only:
        if amt is None:
            data_gap.append("成交额")
        elif amt < YI:
            base["missing_rules"].append(f"第3条 底池：成交额 {amt / YI:.2f} 亿 < 1 亿")
            return base
        else:
            base["facts"]["amount_yi"] = round(amt / YI, 2)
        industry = meta.get("industry")
        if industry:
            base["industry"] = industry
            base["facts"]["industry"] = industry
        if not industry:
            base["missing_rules"].append("第3条 底池：无板块归属")
            return base
        if pe is not None and pe <= 0:
            base["veto"] = [f"动态市盈 {pe:.2f} ≤ 0"]
            return base
        if pe is None:
            data_gap.append("市盈")
        if mcap is not None and mcap < MIN_FLOAT_YI:
            base["veto"] = [f"小盘：流通市值 {mcap:.1f} 亿 < {MIN_FLOAT_YI:.0f} 亿"]
            return base
        if mcap is None:
            data_gap.append("流通市值")
        else:
            base["facts"]["float_mcap_yi"] = round(mcap, 2)
    else:
        industry = meta.get("industry") or "结构回放"
        base["industry"] = industry
        base["facts"]["industry"] = industry
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
            base["missing_rules"].append(f"第3条 主线：{industry} 近3日涨幅证据不足，出池")
            return base
        if market_3d is not None and ind.get("ret_3d") is not None:
            if ind["ret_3d"] < market_3d:
                base["missing_rules"].append(
                    f"第3条 主线：{industry} 近3日 {ind['ret_3d']:.2f}% < 沪深300 {market_3d:.2f}%，整组出池"
                )
                return base
            base["hit_rules"].append(
                f"第3条 主线：{industry} 近3日 {ind['ret_3d']:.2f}% ≥ 沪深300 {market_3d:.2f}%（不弱即可，不要求领涨）"
            )
        elif market_3d is None:
            base["reminders"].append("第3条 主线：沪深300近3日未知，相对大盘未核，不挡筛选")
        rets = [v["ret_3d"] for v in industry_stats.values() if v.get("ret_3d") is not None and v.get("n", 0) >= 2]
        if len(rets) >= 2 and ind.get("ret_3d") is not None and ind["ret_3d"] <= min(rets) + 1e-12:
            base["missing_rules"].append(f"第3条 主线：{industry} 近3日为最弱一档，整组出池")
            return base
        if not _stock_vs_board(bars, ind):
            base["missing_rules"].append(f"第3条 个股近20日相对 {industry} 偏弱，出池")
            return base
        base["hit_rules"].append(f"第3条 个股近20日相对 {industry} 不弱")
        if ind.get("hot_code") == code and (ind.get("n") or 0) >= 2:
            base["veto"] = [f"第3条 主线内最热（近5日 {ind.get('hot_r5')}%），排除"]
            return base

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
        base["missing_rules"].append("第3条 结构预筛：未见近20日先强（收盘≥12%、5～12日）后 4～8 日缩量回调")
        return base
    if not st["shrink"]:
        base["veto"] = ["回踩不缩量（回调日均量未≤先强×0.7）"]
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
    if zone.get("ma20") and close < zone["ma20"]:
        base["veto"] = ["收盘在20日线下方，不是回踩启动"]
        return base

    at_key = bool(zone.get("at_key"))
    last = bars[-1]
    decay = []
    if st["shrink"]:
        decay.append("回调段日均量 ≤ 先强段 × 0.7")
    if _small_or_hammer(last):
        decay.append("触关键位收小阳/十字/长下影")
    prev_low = bars[-2]["low"] if len(bars) > 1 else None
    if prev_low and last["low"] < prev_low and last["close"] >= prev_low:
        decay.append("盘中击穿当日收回")
    if last["low"] >= st["pb_low"] - 1e-9:
        decay.append("不创新低")

    pb_vols = [_vol(x) for x in bars[st["strong_end"] + 1 : -1]]
    vol_dn_ex = (sum(pb_vols) / len(pb_vols)) if pb_vols else st["vol_dn"]
    vol_ok = _vol(last) > vol_dn_ex
    stand_ma20 = bool(zone.get("ma20") and last["close"] >= zone["ma20"])
    demand = []
    if stand_ma20:
        demand.append("收盘站上当日20日线")
    if vol_ok:
        demand.append("当日量 > 回调段日均量")
    demand_ready = bool(vol_ok and stand_ma20)
    if _any_limit(bars, code, 3):
        demand_ready = False

    if not stand_ma20:
        base["veto"] = ["收盘在20日线下方，不是回踩启动"]
        return base

    ma20_px = zone.get("ma20") or key_px
    stop_px = (ma20_px * 0.95) if ma20_px else None
    base["data_ok"] = True
    base["key_kind"] = "20日线"
    base["key_price"] = round(ma20_px, 3) if ma20_px else None
    base["stop_price"] = round(stop_px, 3) if stop_px else None
    base["facts"]["key_kind"] = "20日线"
    base["facts"]["key_price"] = base["key_price"]
    base["facts"]["stop_price"] = base["stop_price"]
    base["facts"]["buy_ma20"] = round(ma20_px, 3) if ma20_px else None
    base["facts"]["industry"] = industry
    if pe is not None:
        base["facts"]["pe"] = pe
    if amt is not None:
        base["facts"]["amount_yi"] = round(amt / YI, 2)
    if mcap is not None:
        base["facts"]["float_mcap_yi"] = round(mcap, 2)
    base["status"] = "观察"
    base["gate"] = "观察"
    base["summary_bucket"] = "观察"
    if data_gap and not structure_only:
        base["missing_rules"].append(f"{MISSING_NO_BUY}（{'、'.join(data_gap)}）")
    stop_txt = f"{stop_px:.2f}" if stop_px else "未写"
    base["hit_rules"].append(
        f"第3条 结构：先强 {st['gain_pct']:.1f}% 后缩量回调 {st['pb_len']} 日；关键位=20日线 {ma20_px:.2f}，止损 {stop_txt}（买入日20日线×0.95）"
    )

    if not decay:
        base["missing_rules"].append("第5条 卖压衰减未见到")
        return base
    base["hit_rules"].append("第5条 卖压衰减：" + "；".join(decay))

    if _any_limit(bars, code, 3):
        base["missing_rules"].append("第6条 近3日有涨停，不得买入")
        return base
    if not demand_ready:
        why = []
        if not stand_ma20:
            why.append("收盘未站上当日20日线")
        if not vol_ok:
            why.append("当日量未大于回调段日均量")
        base["missing_rules"].append("第6条 未齐（" + "、".join(why or ["无"]) + "）")
        return base
    if stop_px is None:
        base["missing_rules"].append("第6条 止损价未写明，先写后买")
        return base
    base["hit_rules"].append("第6条：" + "；".join(demand) + f"；止损 {stop_txt}")
    base["path_ready"] = not bool(data_gap and not structure_only)
    if data_gap and not structure_only:
        base["status"] = "观察"
        base["gate"] = "观察"
        base["summary_bucket"] = "观察"
        note = f"{MISSING_NO_BUY}（{'、'.join(data_gap)}）"
        if note not in base["missing_rules"]:
            base["missing_rules"].append(note)
        return base
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
        hit, section, detail = evaluate_exit_s1(
            bars,
            open_trade,
            {**zone, "kind": "A1", "buy_ma20": ma20_px, "stop": stop_px, "price": ma20_px},
        )
        if hit:
            base["status"] = "卖出"
            base["gate"] = "卖出"
            base["summary_bucket"] = "卖出"
            base["hit_rules"].append(f"第{section}条 卖出已见：{detail}")
    return base


def _ma20_at(bars: list[dict], idx: int) -> float | None:
    sl = bars[: idx + 1]
    if len(sl) < 20:
        return None
    line = sma([b["close"] for b in sl], 20)
    return line[-1] if line else None


def _is_limit_up(bars: list[dict], i: int, code: str) -> bool:
    if i < 1:
        return False
    prev = bars[i - 1]["close"]
    if not prev:
        return False
    return (bars[i]["close"] / prev - 1) >= _limit_pct(code) - 0.005


def _is_big_yang(bars: list[dict], i: int, code: str) -> bool:
    if i < 1:
        return False
    row = bars[i]
    if row["close"] <= row["open"]:
        return False
    prev = bars[i - 1]["close"]
    if not prev:
        return False
    need = 0.12 if ts_code(code).startswith(("3", "68")) else 0.07
    return (row["close"] / prev - 1) >= need - 1e-12


def _had_accel(bars: list[dict], entry_idx: int, code: str) -> bool:
    for i in range(entry_idx, len(bars)):
        if _is_limit_up(bars, i, code) or _is_big_yang(bars, i, code):
            return True
    return False


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
    code = str(last.get("code") or (open_trade or {}).get("code") or "")
    buy_ma20 = (zone or {}).get("buy_ma20") or (zone or {}).get("ma20")
    if buy_ma20 is None:
        buy_ma20 = _ma20_at(bars, entry_idx)
    hard_stop = buy_ma20 * 0.95 if buy_ma20 else ((zone or {}).get("stop"))
    run_high = (zone or {}).get("run_high")
    elapsed = len(bars) - 1 - entry_idx

    if hard_stop and last["close"] < hard_stop:
        return True, "7.1", f"收盘低于买入日20日线×0.95（{hard_stop:.2f}）"
    vols5 = [_vol(b) for b in bars[-5:]]
    avg5 = sum(vols5) / len(vols5) if vols5 else 0
    prev_closes = [x["close"] for x in bars[entry_idx : len(bars) - 1]]
    if prev_closes and last["close"] < min(prev_closes) and avg5 and _vol(last) >= avg5:
        return True, "7.1", "收盘再创新低且当日量 ≥ 近5日均量"
    if elapsed >= 15 and run_high and last["close"] < run_high:
        return True, "7.1", "买入后15个交易日仍未收盘站上观察日后区间高点"

    if buy_ma20 and last["close"] < buy_ma20 and len(bars) >= 2 and bars[-2]["close"] < buy_ma20:
        return True, "7.1b", f"连续2日收盘低于买入日20日线 {buy_ma20:.2f}"

    today_limit = _is_limit_up(bars, len(bars) - 1, code)
    had_accel = _had_accel(bars, entry_idx, code)
    hold = bars[entry_idx:]
    m20_today = _ma20_at(bars, len(bars) - 1)
    if had_accel and elapsed >= 3 and not today_limit:
        prior_closes = [x["close"] for x in bars[entry_idx : len(bars) - 1]]
        new_close_high = bool(prior_closes) and last["close"] > max(prior_closes)
        hold_vols = [_vol(x) for x in hold]
        avg_hold = sum(hold_vols) / len(hold_vols) if hold_vols else 0
        order = sorted(range(len(hold_vols)), key=lambda j: hold_vols[j], reverse=True)
        last_rank = order.index(len(hold) - 1) if hold_vols else 99
        is_top2 = last_rank <= 1
        vol_boom = bool(avg_hold and _vol(last) >= avg_hold * 2)
        left_zone = bool(m20_today and last["close"] > m20_today * 1.12)
        if new_close_high and (is_top2 or vol_boom) and left_zone:
            return True, "7.2", "高潮离场：收盘新高、未涨停、量能居前、高于当天20日线12%"

    prior_closes = [x["close"] for x in bars[entry_idx : len(bars) - 1]]
    prior_hi = max(prior_closes) if prior_closes else last["close"]
    if len(bars) >= 5:
        r3y = _ret(bars[-5]["close"], bars[-2]["close"])
        if r3y is not None and r3y >= 25 and last["close"] <= prior_hi:
            return True, "7.3", "近3日累计涨幅≥25%，下一交易日收盘不再创新高"
    if len(bars) >= 2:
        a, b = bars[-2], bars[-1]
        if a["close"] < a["open"] and b["close"] < b["open"]:
            if _vol(a) >= _vol(entry) and _vol(b) >= _vol(entry):
                return True, "7.3", "连续2日收阴且两日量都 ≥ 买入日量"
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
    stats = _industry_stats(list_s1_pool())
    return classify_s1(meta, settings, trades, stats, market_3d)


def list_s1_cycle_universe() -> list[dict]:
    """轨迹回放底池：非 ST、股价≥5。不挡 PE/成交额/市值/主线（回放只看历史上买过没有、卖了没有）。"""
    uni = {ts_code(str(x.get("code") or "")): x for x in load_universe()}
    out = []
    for path in CSV_DIR.glob("*.csv"):
        code = path.stem
        last = peek_last_bar(code)
        if not last:
            continue
        name = last.get("name") or (uni.get(code) or {}).get("name") or code
        if is_st_name(name):
            continue
        if last["close"] < POOL_MIN_PRICE:
            continue
        out.append({"code": code, "name": name})
    return out


def list_s1_pool() -> list[dict]:
    """RULES2 第3.1条底池，不走 RULES.md 300亿/5亿宇宙。"""
    uni = {ts_code(str(x.get("code") or "")): x for x in load_universe()}
    quotes = load_quotes()
    imap = _load_industry_map()
    cands = []
    for path in CSV_DIR.glob("*.csv"):
        code = path.stem
        last = peek_last_bar(code)
        if not last:
            continue
        q = quotes.get(code) or {}
        name = last.get("name") or q.get("name") or (uni.get(code) or {}).get("name") or code
        if is_st_name(name):
            continue
        if last["close"] < POOL_MIN_PRICE:
            continue
        amt = _amt(last)
        if amt is None and q.get("amount"):
            amt = q["amount"]
        if amt is not None and amt < YI:
            continue
        industry = imap.get(code) or (uni.get(code) or {}).get("industry")
        if not industry:
            continue
        bars = load_bars(code, last_n=80)
        if len(bars) < 25:
            continue
        last = bars[-1]
        if last.get("amount") in (None, "", 0, 0.0) and q.get("amount"):
            last["amount"] = q["amount"]
            bars[-1]["amount"] = q["amount"]
        name = last.get("name") or name
        meta = dict(uni.get(code) or {})
        meta.update({"code": code, "name": name, "bars": bars, "industry": industry})
        if q.get("pe") is not None:
            meta["pe"] = q["pe"]
        if q.get("float_mcap_yi") is not None:
            meta["float_mcap_yi"] = q["float_mcap_yi"]
        if q.get("amount_yi") is not None:
            meta["amount_yi"] = q["amount_yi"]
        cands.append(meta)
    return cands


def board_funnel(industry_stats: dict, market_3d: float | None) -> list[dict]:
    """第3.2条：先筛主线，再给个股用。不弱即可，不要求领涨。"""
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
            reason = "沪深300近3日未知，主线未挡"
        else:
            reason = f"近3日 {r3:.2f}% ≥ 沪深300 {market_3d:.2f}%（不弱即可）"
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
    from .eastmoney import ensure_quotes

    ensure_quotes()
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
