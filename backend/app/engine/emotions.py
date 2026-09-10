"""EMOTIONS.MD：三层情绪（只过滤，不触发买卖）+ 资金异动（试盘 vs 建仓）。独立于 RULES。"""
from __future__ import annotations

from datetime import datetime

from ..config import CSV_DIR, EMOTIONS_PATH
from ..store import load_quotes, load_universe, read_json, write_json
from .bars import load_bars, ts_code
from .boards import build_board_daily, industry_of, load_board_daily
from .clock import asof_date
from .indicators import sma
from .pool import is_st_name

YI = 100_000_000.0


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


def _clip(score: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return round(max(lo, min(hi, score)), 1)


def _label_market(score: float) -> str:
    if score >= 65:
        return "偏暖"
    if score >= 40:
        return "震荡"
    return "冰点"


def _tone(score: float) -> str:
    if score >= 65:
        return "warm"
    if score >= 40:
        return "chop"
    return "ice"


def _limit_pct(code: str) -> float:
    c = ts_code(code)
    if c.startswith(("3", "68")):
        return 0.20
    if c.startswith(("8", "4")):
        return 0.30
    return 0.10


def _up_share_score(pct) -> tuple[float, str]:
    if pct is None:
        return 0.0, "上涨家数占比证据不足"
    if pct > 60:
        return 100.0, f"上涨家数 {pct:.1f}% > 60%，偏暖"
    if pct >= 40:
        return 40.0 + (pct - 40.0) / 20.0 * 40.0, f"上涨家数 {pct:.1f}% 在 40–60%，震荡"
    return pct / 40.0 * 40.0, f"上涨家数 {pct:.1f}% < 40%，冰点"


def _limit_count_score(n) -> tuple[float, str]:
    if n is None:
        return 0.0, "涨停家数证据不足"
    if n > 35:
        return 100.0, f"涨停 {n} 家 > 35，活跃"
    if n >= 10:
        return 40.0 + (n - 10) / 25.0 * 45.0, f"涨停 {n} 家，一般"
    return n / 10.0 * 40.0, f"涨停 {n} 家 < 10，冰点"


def _fail_rate_score(pct) -> tuple[float, str]:
    if pct is None:
        return 0.0, "炸板率证据不足"
    if pct < 30:
        return 100.0, f"炸板率 {pct:.1f}% < 30%，好"
    if pct <= 40:
        return 80.0 - (pct - 30.0) / 10.0 * 30.0, f"炸板率 {pct:.1f}%，一般"
    return max(0.0, 50.0 - (pct - 40.0) * 2.0), f"炸板率 {pct:.1f}% > 40%，差"


def _height_score(h) -> tuple[float, str]:
    if not h:
        return 0.0, "连板高度证据不足"
    if h >= 8:
        return 100.0, f"连板高度 {h}，资金敢博弈上限高"
    if h >= 5:
        return 70.0 + (h - 5) / 3.0 * 20.0, f"连板高度 {h}，偏活跃"
    if h >= 3:
        return 45.0 + (h - 3) / 2.0 * 20.0, f"连板高度 {h}，一般"
    return 25.0 * h, f"连板高度 {h}，偏弱"


def _amp_pct(row: dict) -> float | None:
    high, low, pre = _num(row.get("high")), _num(row.get("low")), _num(row.get("preclose") or row.get("settlement"))
    if high is None or low is None:
        return None
    base = pre or _num(row.get("close"))
    if not base:
        return None
    return (high - low) / base * 100.0


def market_from_quotes(quotes: dict, zt: list[dict], zb: list[dict]) -> dict:
    rows = [q for q in (quotes or {}).values() if isinstance(q, dict)]
    non_st = []
    for rec in rows:
        name = str(rec.get("name") or "")
        if is_st_name(name):
            continue
        non_st.append(rec)
    total = len(non_st)
    up = 0
    down = 0
    limit_n = 0
    fail_n = 0
    for rec in non_st:
        code = ts_code(str(rec.get("code") or rec.get("symbol") or ""))
        pct = _num(rec.get("pct") or rec.get("changepercent"))
        close = _num(rec.get("close") or rec.get("trade"))
        pre = _num(rec.get("preclose") or rec.get("settlement"))
        high = _num(rec.get("high"))
        if pct is None and close and pre:
            pct = (close / pre - 1.0) * 100.0
        if pct is not None:
            if pct > 0:
                up += 1
            elif pct < 0:
                down += 1
        if close and pre:
            lim = _limit_pct(code or str(rec.get("code") or ""))
            if (close / pre - 1.0) >= lim - 0.005:
                limit_n += 1
            elif high and (high / pre - 1.0) >= lim - 0.005:
                fail_n += 1
    if zt:
        limit_n = sum(1 for x in zt if not is_st_name(str(x.get("name") or "")))
    if zb:
        fail_n = len(zb)
    if total and up == 0 and down == 0:
        daily = load_board_daily()
        asof = asof_date()
        up_n = tot_n = 0
        for rec in (daily.get("boards") or {}).values():
            dates = [d for d in rec.get("dates") or [] if d <= asof]
            if not dates:
                continue
            last_row = (rec.get("by_date") or {}).get(dates[-1]) or {}
            up_n += int(last_row.get("up") or 0)
            tot_n += int(last_row.get("n") or 0)
        if tot_n:
            total = tot_n
            up = up_n
    touched = limit_n + fail_n
    up_pct = (up / total * 100.0) if total else None
    fail_pct = (fail_n / touched * 100.0) if touched else None
    height = 0
    for rec in zt or []:
        try:
            height = max(height, int(rec.get("lbc") or rec.get("height") or 0))
        except (TypeError, ValueError):
            continue
    if not height:
        height = 1 if limit_n else 0
    s1, d1 = _up_share_score(up_pct)
    s2, d2 = _limit_count_score(limit_n)
    s3, d3 = _fail_rate_score(fail_pct)
    s4, d4 = _height_score(height)
    score = _clip(s1 * 0.35 + s2 * 0.25 + s3 * 0.25 + s4 * 0.15)
    return {
        "score": score,
        "label": _label_market(score),
        "tone": _tone(score),
        "total": total,
        "up": up,
        "down": down,
        "up_pct": None if up_pct is None else round(up_pct, 1),
        "limit_ups": limit_n,
        "fail_n": fail_n,
        "fail_pct": None if fail_pct is None else round(fail_pct, 1),
        "height": height,
        "metrics": [
            {"key": "上涨家数占比", "value": up_pct, "unit": "%", "score": _clip(s1), "detail": d1, "bands": ">60 偏暖 / 40–60 震荡 / <40 冰点"},
            {"key": "涨停家数", "value": limit_n, "unit": "家", "score": _clip(s2), "detail": d2, "bands": ">35 活跃 / 10–35 一般 / <10 冰点"},
            {"key": "炸板率", "value": fail_pct, "unit": "%", "score": _clip(s3), "detail": d3, "bands": "<30 好 / >40 差"},
            {"key": "连板高度", "value": height, "unit": "板", "score": _clip(s4), "detail": d4, "bands": "资金敢博弈的上限"},
        ],
        "note": "大盘情绪只过滤，不触发买卖。",
    }


def _hist_next_win_pct(rec: dict, asof: str, lookback: int = 20) -> float | None:
    dates = [d for d in (rec.get("dates") or []) if d <= asof]
    rates = []
    for d in reversed(dates):
        row = (rec.get("by_date") or {}).get(d) or {}
        n = int(row.get("next_n") or 0)
        w = int(row.get("next_win") or 0)
        if n <= 0:
            continue
        rates.append(w / n * 100.0)
        if len(rates) >= lookback:
            break
    if not rates:
        return None
    return round(sum(rates) / len(rates), 1)


def _board_kind(limit_rows: list[dict], members: list[dict]) -> tuple[str, str]:
    """涨停数：中军+多只小票联动＝共振；只有孤零零一只涨停＝孤狼。"""
    limit_n = len(limit_rows)
    if limit_n <= 0:
        return "无涨停", "板块内没有涨停"
    if limit_n == 1:
        one = limit_rows[0]
        return "孤狼", f"只有孤零零一只涨停：{one.get('name') or one.get('code')}"
    mcaps = [m.get("mcap") for m in members if m.get("mcap")]
    median = sorted(mcaps)[len(mcaps) // 2] if mcaps else None
    leader = None
    if members:
        leader = max(members, key=lambda x: x.get("mcap") or 0)
    def is_zhongjun(row: dict) -> bool:
        mcap = row.get("mcap")
        if not mcap:
            return False
        if leader and row.get("code") == leader.get("code"):
            return True
        if median and mcap >= median * 2:
            return True
        return False
    zhong = [x for x in limit_rows if is_zhongjun(x)]
    small = [x for x in limit_rows if not is_zhongjun(x)]
    if zhong and len(small) >= 2:
        zname = "、".join((x.get("name") or x.get("code") or "") for x in zhong[:2])
        return "共振", f"中军 {zname} + {len(small)} 只小票涨停联动"
    if zhong and len(small) == 1:
        return "多票", f"中军已涨停，但小票只有 1 只，还不够多只联动"
    return "多票无中军", f"{limit_n} 只涨停，未见中军带小票"


def boards_live(quotes: dict, daily: dict, asof: str) -> list[dict]:
    """当日板块：家数、收红、涨停、共振/孤狼、近20日次日赚钱概率。"""
    from .boards import industry_of, is_limit_up, sw_maps

    sw1, sw2 = sw_maps()
    uni = {ts_code(str(x.get("code") or "")): x for x in load_universe()}
    groups: dict[str, list[dict]] = {}
    codes = set(uni) | set(quotes or {})
    for code in codes:
        q = (quotes or {}).get(code) or {}
        meta = uni.get(code) or {}
        name = str(q.get("name") or meta.get("name") or code)
        if is_st_name(name):
            continue
        industry = (
            sw2.get(code)
            or sw1.get(code)
            or industry_of(code)
            or str(q.get("industry") or meta.get("industry") or "").strip()
            or None
        )
        if not industry:
            continue
        pct = _num(q.get("pct"))
        close = _num(q.get("close") or meta.get("close"))
        pre = _num(q.get("preclose"))
        if pct is None and close and pre:
            pct = (close / pre - 1.0) * 100.0
        if pct is None:
            bars = load_bars(code, last_n=4)
            if len(bars) >= 2 and bars[-1].get("close") and bars[-2].get("close"):
                close = bars[-1]["close"]
                pre = bars[-2]["close"]
                pct = (close / pre - 1.0) * 100.0
        limit = False
        if close and pre:
            limit = is_limit_up(pre, close, code)
        elif pct is not None:
            limit = pct >= (_limit_pct(code) * 100.0 - 0.5)
        mcap = _num(q.get("float_mcap_yi") or meta.get("float_mcap_yi"))
        groups.setdefault(industry, []).append(
            {
                "code": code,
                "name": name,
                "pct": pct,
                "mcap": mcap,
                "limit": limit,
                "red": bool(pct is not None and pct > 0),
            }
        )

    hist = daily.get("boards") or {}
    out = []
    for name, members in groups.items():
        n = len(members)
        if n < 2:
            continue
        up = sum(1 for m in members if m.get("red"))
        up_pct = round(up / n * 100.0, 1) if n else None
        limit_rows = [m for m in members if m.get("limit")]
        limit_n = len(limit_rows)
        kind, kind_detail = _board_kind(limit_rows, members)
        next_pct = _hist_next_win_pct(hist.get(name) or {}, asof)
        up_score = _up_share_score(up_pct)[0]
        if kind == "共振":
            limit_score = 90.0 + min(10.0, limit_n)
        elif kind == "孤狼":
            limit_score = 28.0
        elif limit_n >= 2:
            limit_score = 50.0 + limit_n * 3
        else:
            limit_score = 10.0
        score = _clip(up_score * 0.4 + min(100.0, limit_score) * 0.35 + (next_pct or 40) * 0.25)
        leader = max(members, key=lambda x: x.get("mcap") or 0) if members else None
        out.append(
            {
                "name": name,
                "n": n,
                "up": up,
                "up_pct": up_pct,
                "limit_ups": limit_n,
                "limit_names": [x.get("name") or x.get("code") for x in limit_rows[:8]],
                "zhongjun": (leader.get("name") if leader and (leader.get("mcap") or 0) > 0 else None),
                "kind": kind,
                "kind_detail": kind_detail,
                "score": score,
                "tone": _tone(score),
                "next_day_win_pct": next_pct,
                "next_day": (
                    f"近20日板块内个股次日买入赚钱 {next_pct:.1f}%"
                    if next_pct is not None
                    else "次日赚钱概率证据不足（还缺下一根日线）"
                ),
            }
        )
    out.sort(key=lambda x: (0 if x["kind"] == "共振" else 1 if x["kind"] == "孤狼" else 2, -(x["limit_ups"] or 0), -(x["score"] or 0)))
    return out


def _near_low(closes: list[float], lookback: int, half_year: int = 120) -> tuple[bool, str]:
    if not closes:
        return False, "收盘不足"
    last = closes[-1]
    win20 = closes[-lookback:] if len(closes) >= lookback else closes
    win_long = closes[-half_year:] if len(closes) >= 20 else closes
    lo20, hi20 = min(win20), max(win20)
    lo_l = min(win_long)
    near20 = hi20 == lo20 or (last - lo20) <= (hi20 - last)
    no_new_low = last >= lo_l * 0.995
    ok = near20 and no_new_low
    why = "近20日低位区" if near20 else "不在近20日低位"
    if not no_new_low:
        why += "，仍在创新低"
    else:
        why += "，横盘不再创新低"
    return ok, why


def _vol(row: dict) -> float:
    return float(row.get("volume") or 0)


def classify_flow(bars: list[dict]) -> dict | None:
    if len(bars) < 25:
        return None
    last = bars[-1]
    closes = [b["close"] for b in bars if b.get("close")]
    if len(closes) < 20:
        return None
    vols = [_vol(b) for b in bars]
    ma5 = sma(vols, 5)
    v5 = ma5[-1]
    v_last = vols[-1]
    yang = last["close"] > last["open"]
    if not v5 or v_last < v5 * 1.5 or not yang:
        return None
    low_ok, low_why = _near_low(closes, 20)
    if not low_ok:
        return None
    after_pulse = 0
    pulse_i = None
    for i in range(len(bars) - 2, max(0, len(bars) - 8), -1):
        if ma5[i] and vols[i] >= ma5[i] * 1.5 and bars[i]["close"] > bars[i]["open"]:
            pulse_i = i
            after_pulse = len(bars) - 1 - i
            break
    hold = 0
    shrink = 0
    higher_low = False
    yin_shrink_yang_exp = False
    if pulse_i is not None:
        post = bars[pulse_i + 1 :]
        for j, row in enumerate(post):
            idx = pulse_i + 1 + j
            avg = ma5[idx]
            if avg and _vol(row) >= avg:
                hold += 1
            else:
                shrink += 1
        lows = [x["low"] for x in bars[max(0, pulse_i - 3) :]]
        if len(lows) >= 3:
            higher_low = lows[-1] >= min(lows[:-1])
        yin = [x for x in post if x["close"] < x["open"]]
        yangs = [x for x in post if x["close"] > x["open"]]
        if yin and yangs:
            yv = sum(_vol(x) for x in yangs) / len(yangs)
            nv = sum(_vol(x) for x in yin) / len(yin)
            yin_shrink_yang_exp = yv >= nv
    fail_hold = last["close"] < last["high"] * 0.97 and last["close"] <= (bars[-2]["high"] if len(bars) > 1 else last["high"])
    probe = False
    build = False
    if pulse_i is not None and 1 <= after_pulse <= 3 and shrink >= hold and fail_hold:
        probe = True
    if v_last >= v5 and hold >= shrink and (higher_low or yin_shrink_yang_exp):
        build = True
    if probe and build:
        probe = False
    if not probe and not build:
        if hold >= 2 and higher_low:
            build = True
        else:
            probe = True
    build_score = 20.0
    if v_last >= v5:
        build_score += 20
    if hold >= 2:
        build_score += 20
    if higher_low:
        build_score += 20
    if yin_shrink_yang_exp:
        build_score += 15
    if not fail_hold:
        build_score += 5
    probe_score = 20.0
    if pulse_i is not None and after_pulse <= 3:
        probe_score += 25
    if shrink > hold:
        probe_score += 20
    if fail_hold:
        probe_score += 20
    if not higher_low:
        probe_score += 15
    kind = "建仓" if build and not probe else "试盘"
    score = _clip(build_score if kind == "建仓" else probe_score)
    reasons = [low_why, f"单日量 {v_last / v5:.2f}× 5日均量，收阳"]
    if kind == "建仓":
        if hold >= 2:
            reasons.append("放量后量能维持在 5 日均量之上")
        if higher_low:
            reasons.append("低点抬高")
        if yin_shrink_yang_exp:
            reasons.append("阴缩阳放")
        reasons.append("持续承接")
    else:
        reasons.append("一次性放量测抛压")
        if shrink:
            reasons.append("放量阳后迅速缩量")
        if fail_hold:
            reasons.append("冲高回落、难破压力")
        reasons.append("试盘≠拉升")
    return {
        "kind": kind,
        "score": score,
        "tone": "warm" if kind == "建仓" else "chop",
        "vol_ratio": round(v_last / v5, 2) if v5 else None,
        "reasons": reasons,
        "date": last.get("date"),
        "close": last.get("close"),
    }


def stock_emotion(code: str, quotes: dict | None = None) -> dict:
    code = ts_code(code)
    quotes = quotes if quotes is not None else load_quotes()
    q = quotes.get(code) or {}
    bars = load_bars(code, last_n=140)
    name = q.get("name") or (bars[-1].get("name") if bars else code) or code
    amp = _amp_pct(q) if q else None
    if amp is None and len(bars) >= 2:
        last, prev = bars[-1], bars[-2]
        pre = prev.get("close")
        if pre and last.get("high") and last.get("low"):
            amp = (last["high"] - last["low"]) / pre * 100.0
    turnover = _num(q.get("turnover"))
    if turnover is None and q.get("float_mcap_yi") and q.get("amount_yi"):
        mcap = _num(q.get("float_mcap_yi"))
        amt = _num(q.get("amount_yi"))
        if mcap:
            turnover = amt / mcap * 100.0
    closes = [b["close"] for b in bars if b.get("close")]
    low_ok, low_why = _near_low(closes, 20) if closes else (False, "日线不足")
    high_pos = False
    if len(closes) >= 20:
        lo, hi = min(closes[-20:]), max(closes[-20:])
        high_pos = hi != lo and (closes[-1] - lo) > (hi - closes[-1])
    turn_note = "换手证据不足"
    turn_score = 40.0
    if turnover is not None:
        if low_ok and turnover >= 3:
            turn_score = 75.0
            turn_note = f"低位换手 {turnover:.1f}%，放量抬升=活跃"
        elif high_pos and turnover >= 8:
            turn_score = 25.0
            turn_note = f"高位换手 {turnover:.1f}%，慎出货"
        else:
            turn_score = 50.0
            turn_note = f"换手 {turnover:.1f}%"
    amp_note = "振幅证据不足"
    amp_score = 40.0
    if amp is not None:
        if amp >= 7:
            amp_score = 80.0
            amp_note = f"日内振幅 {amp:.1f}%，活跃"
        elif amp <= 2:
            amp_score = 30.0
            amp_note = f"日内振幅 {amp:.1f}%，沉闷"
        else:
            amp_score = 55.0
            amp_note = f"日内振幅 {amp:.1f}%"
    seal = None
    seal_score = 50.0
    seal_note = "非涨停，封板质量仅作波段参考"
    flow = classify_flow(bars) if bars else None
    score = _clip(turn_score * 0.4 + amp_score * 0.35 + seal_score * 0.25)
    return {
        "code": code,
        "name": name,
        "industry": industry_of(code),
        "score": score,
        "tone": _tone(score),
        "turnover": None if turnover is None else round(turnover, 2),
        "amplitude": None if amp is None else round(amp, 2),
        "position": low_why,
        "metrics": [
            {"key": "换手", "value": turnover, "unit": "%", "score": _clip(turn_score), "detail": turn_note},
            {"key": "日内振幅", "value": amp, "unit": "%", "score": _clip(amp_score), "detail": amp_note},
            {"key": "封板质量", "value": seal, "unit": "", "score": _clip(seal_score), "detail": seal_note},
        ],
        "flow": flow,
        "note": "个股情绪只过滤，不触发买卖。资金异动第一优先级是量价。",
    }


def _scan_flow(quotes: dict, limit: int = 40) -> tuple[list[dict], list[dict]]:
    uni = {ts_code(str(x.get("code") or "")): x for x in load_universe()}
    build, probe = [], []
    seen = set()
    codes = list(uni) or list(quotes)
    if len(codes) < 200:
        codes = [p.stem for p in CSV_DIR.glob("*.csv")]
    for code in codes:
        if code in seen:
            continue
        seen.add(code)
        bars = load_bars(code, last_n=40)
        if len(bars) < 25:
            continue
        name = (quotes.get(code) or {}).get("name") or bars[-1].get("name") or code
        if is_st_name(name):
            continue
        hit = classify_flow(bars)
        if not hit:
            continue
        row = {
            "code": code,
            "name": name,
            "industry": industry_of(code),
            **hit,
        }
        if hit["kind"] == "建仓":
            build.append(row)
        else:
            probe.append(row)
    build.sort(key=lambda x: -(x.get("score") or 0))
    probe.sort(key=lambda x: -(x.get("score") or 0))
    return build[:limit], probe[:limit]


def build_emotions(force: bool = False) -> dict:
    from .eastmoney import fetch_fail_pool, fetch_limit_pool

    asof = asof_date()
    cached = read_json(EMOTIONS_PATH, {}) if EMOTIONS_PATH.exists() else {}
    if not force and isinstance(cached, dict) and cached.get("asof") == asof and cached.get("market"):
        return cached
    quotes = load_quotes()
    if not quotes:
        try:
            from .eastmoney import ensure_quotes

            quotes = ensure_quotes()
        except Exception:
            quotes = {}
    zt, zb = [], []
    try:
        zt = fetch_limit_pool(asof)
    except Exception:
        zt = []
    try:
        zb = fetch_fail_pool(asof)
    except Exception:
        zb = []
    from .boards import ensure_industry_map

    ensure_industry_map(quotes)
    daily = load_board_daily()
    if daily.get("asof") != asof or not daily.get("boards") or not daily.get("board_count"):
        try:
            daily = build_board_daily(asof, force=True)
        except Exception:
            daily = daily or {}
    market = market_from_quotes(quotes, zt, zb)
    boards = boards_live(quotes, daily, asof)
    build, probe = _scan_flow(quotes)
    payload = {
        "asof": asof,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "fact_note": "这是事实记录。情绪只过滤，不触发买卖。",
        "market": market,
        "boards": boards,
        "board_total": len(boards),
        "board_note": "上涨占比=板块家数里收红只数。涨停数：中军+多只小票联动＝共振；只有孤零零一只涨停＝孤狼。赚钱效应=近20日板块内个股次日收盘上涨的比例。",
        "flow": {
            "build": build,
            "probe": probe,
            "note": "试盘=打一枪就停；建仓=持续承接、底抬高。量价第一，情绪/MACD只辅助。",
        },
        "zt_n": len(zt),
        "zb_n": len(zb),
    }
    write_json(EMOTIONS_PATH, payload)
    return payload


def load_emotions() -> dict:
    data = read_json(EMOTIONS_PATH, {})
    return data if isinstance(data, dict) else {}
