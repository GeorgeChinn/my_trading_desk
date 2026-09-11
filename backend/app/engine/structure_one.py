"""RULES2 回调后的重新启动（野人哥 C 区）。按 RULES2.MD 全文量化，不沿用旧 20 日线买点。"""
from __future__ import annotations

import re

from ..config import CSV_DIR, DATA_DIR, GATES_S1, POOL_MIN_PRICE
from ..store import load_quotes, load_universe
from .bars import bar_amount, load_bars, overlay_quote_bar, peek_last_bar, ts_code
from .pool import is_st_name
from .scanner import FACT_NOTE, dyn_pe_value

YI = 100_000_000.0
MIN_FLOAT_YI = 80.0
LISTED_DAYS = 60
A_MIN, A_MAX = 5, 12
C_MIN, C_MAX = 4, 8
TRIAL_WINDOW = 3
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def _strip_html_comments(text: str) -> str:
    return _HTML_COMMENT.sub("", text or "")


def need_mainline(text: str | None = None) -> bool:
    """现行 RULES2 无主线闸。仅当正文仍有未注释的 ## 主线 才打开。"""
    raw = text
    if raw is None:
        from .rulesets import get_ruleset

        rs = get_ruleset("rules2")
        raw = (rs or {}).get("text") or ""
    body = _strip_html_comments(raw)
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("## ") and "主线" in s:
            return True
    return False


def _vol(row: dict) -> float:
    return float(row.get("volume") or 0)


def _amt(row: dict) -> float | None:
    return bar_amount(row)


def _yang(row: dict) -> bool:
    return float(row.get("close") or 0) > float(row.get("open") or 0)


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


def _day_gain(bars: list[dict], i: int) -> float | None:
    if i < 1:
        return None
    prev = bars[i - 1].get("close")
    cur = bars[i].get("close")
    if not prev or not cur:
        return None
    return cur / prev - 1.0


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


def _load_industry_map() -> dict[str, str]:
    from .boards import sw_maps

    sw1, sw2 = sw_maps()
    out = dict(sw1)
    out.update(sw2)
    return out


def _hs300() -> dict[str, float]:
    from .boards import load_hs300, refresh_hs300

    hs = load_hs300()
    if len(hs) < 10:
        try:
            hs = refresh_hs300()
        except Exception:
            pass
    return hs or {}


def _hs_px(hs: dict[str, float], day: str) -> float | None:
    if day in hs:
        return hs[day]
    prev = [d for d in hs if d <= day]
    if not prev:
        return None
    return hs[max(prev)]


def _hs_ret(hs: dict[str, float], d0: str, d1: str) -> float | None:
    a, b = _hs_px(hs, d0), _hs_px(hs, d1)
    if not a or not b:
        return None
    return (b / a - 1.0) * 100.0


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _check_a(bars: list[dict], s: int, e: int, hs: dict[str, float]) -> dict | None:
    if e - s + 1 < A_MIN or e - s + 1 > A_MAX:
        return None
    start_c = bars[s].get("close")
    if not start_c:
        return None
    seg = bars[s : e + 1]
    closes = [x["close"] for x in seg if x.get("close")]
    if len(closes) != len(seg):
        return None
    hi_c = max(closes)
    end_c = bars[e]["close"]
    gain = (hi_c - start_c) / start_c
    if gain < 0.12:
        return None
    if end_c < hi_c * 0.97:
        return None
    yang_n = sum(1 for x in seg if _yang(x))
    yin_n = len(seg) - yang_n
    if yang_n < yin_n:
        return None
    peak = closes[0]
    max_dd = 0.0
    for c in closes:
        peak = max(peak, c)
        if peak:
            max_dd = max(max_dd, (peak - c) / peak)
    if max_dd > gain * 0.30 + 1e-12:
        return None
    d0, d1 = str(bars[s].get("date") or ""), str(bars[e].get("date") or "")
    hs_ret = _hs_ret(hs, d0, d1)
    if hs_ret is None:
        return None
    stk_ret = (end_c / start_c - 1.0) * 100.0
    if stk_ret - hs_ret < 0:
        return None
    vols = [_vol(x) for x in seg]
    a_avg = _mean(vols)
    if a_avg <= 0:
        return None
    fund = None
    for i, row in enumerate(seg):
        gi = _day_gain(bars, s + i)
        if gi is None:
            continue
        if _vol(row) >= a_avg * 2 and _yang(row) and gi >= 0.03:
            fund = {
                "idx": s + i,
                "low": row["low"],
                "close": row["close"],
                "vol": _vol(row),
                "date": row.get("date"),
            }
    if not fund:
        return None
    pre_c = bars[s - 1]["close"] if s > 0 else None
    return {
        "s": s,
        "e": e,
        "gain": gain,
        "hi_c": hi_c,
        "start_c": start_c,
        "end_c": end_c,
        "pre_c": pre_c,
        "a_avg": a_avg,
        "fund": fund,
        "stk_ret": stk_ret,
        "hs_ret": hs_ret,
        "len": e - s + 1,
    }


def _check_c(bars: list[dict], a: dict, c_end: int) -> dict | None:
    c0 = a["e"] + 1
    clen = c_end - c0 + 1
    if clen < C_MIN or clen > C_MAX:
        return None
    if c_end >= len(bars):
        return None
    seg = bars[c0 : c_end + 1]
    if len(seg) != clen:
        return None
    c_vols = [_vol(x) for x in seg]
    c_avg = _mean(c_vols)
    if c_avg > a["a_avg"] * 0.70 + 1e-12:
        return None
    c_closes = [x["close"] for x in seg]
    c_min_c = min(c_closes)
    retrace = (a["hi_c"] - c_min_c) / a["hi_c"]
    if retrace > a["gain"] * 0.40 + 1e-12:
        return None
    win0 = max(0, c_end + 1 - 20)
    ma20 = _mean([_vol(x) for x in bars[win0 : c_end + 1]])
    di_cap = min(a["a_avg"] * 0.45, ma20 * 0.50) if ma20 else a["a_avg"] * 0.45
    di = None
    for row in seg:
        if _vol(row) <= di_cap + 1e-12:
            if di is None or row["low"] <= di["low"]:
                di = {
                    "idx": None,
                    "low": row["low"],
                    "close": row["close"],
                    "vol": _vol(row),
                    "date": row.get("date"),
                }
    if not di:
        return None
    for i, row in enumerate(seg):
        if row.get("date") == di["date"] and abs(row["low"] - di["low"]) < 1e-12:
            di["idx"] = c0 + i
            break
    c_low = min(x["low"] for x in seg)
    if a.get("pre_c") and c_low < a["pre_c"] * 0.97 - 1e-12:
        return None
    if c_low < a["fund"]["low"] * 0.95 - 1e-12:
        return None
    for i, row in enumerate(seg):
        g = _day_gain(bars, c0 + i)
        if g is not None and g <= -0.07 and _vol(row) >= a["a_avg"] - 1e-12:
            return None
    return {
        "s": c0,
        "e": c_end,
        "len": clen,
        "c_avg": c_avg,
        "di": di,
        "c_low": c_low,
        "c_min_c": c_min_c,
        "ma20_vol": ma20,
    }


def find_structure(bars: list[dict], hs: dict[str, float] | None = None) -> dict | None:
    """近 20 日取结束日最晚的合格 A，再接 4～8 日 C。"""
    n = len(bars)
    if n < 25:
        return None
    hs = hs if hs is not None else _hs300()
    win0 = max(0, n - 20)
    for e in range(n - 1, win0 + A_MIN - 2, -1):
        for alen in range(A_MAX, A_MIN - 1, -1):
            s = e - alen + 1
            if s < win0 or s < 1:
                continue
            a = _check_a(bars, s, e, hs)
            if not a:
                continue
            for clen in range(C_MIN, C_MAX + 1):
                c_end = e + clen
                if c_end >= n:
                    break
                c = _check_c(bars, a, c_end)
                if c:
                    return {"a": a, "c": c}
    return None


def _recent_limit(bars: list[dict], code: str, n: int) -> bool:
    last = len(bars)
    for i in range(max(1, last - n), last):
        if _is_limit_up(bars, i, code):
            return True
    return False


def _ret5(bars: list[dict]) -> float | None:
    if len(bars) < 6:
        return None
    a, b = bars[-6].get("close"), bars[-1].get("close")
    if not a or not b:
        return None
    return (b / a - 1.0) * 100.0


def _trial_ok(bars: list[dict], st: dict) -> tuple[bool, list[str], list[str]]:
    last = bars[-1]
    a, c = st["a"], st["c"]
    hit, miss = [], []
    vol_ok = _vol(last) <= a["a_avg"] * 0.80 + 1e-12
    (hit if vol_ok else miss).append(
        f"量 {_vol(last):.0f} {'≤' if vol_ok else '>'} A段日均×0.80（{a['a_avg'] * 0.80:.0f}）"
    )
    prev_h = bars[-2]["high"] if len(bars) > 1 else None
    yang_or = _yang(last) or (prev_h is not None and last["close"] > prev_h)
    (hit if yang_or else miss).append("收阳或收盘>昨高" if yang_or else "未收阳且收盘未过昨高")
    di_low = c["di"]["low"]
    low_ok = last["low"] >= di_low * 0.99 - 1e-12
    (hit if low_ok else miss).append(
        f"最低 {last['low']:.2f} {'≥' if low_ok else '<'} 地量日最低×0.99（{di_low * 0.99:.2f}）"
    )
    gap_ok = last["close"] <= a["hi_c"] * 0.96 + 1e-12
    gap_pct = (a["hi_c"] - last["close"]) / a["hi_c"] * 100.0
    (hit if gap_ok else miss).append(
        f"收盘低于A段最高收盘 {gap_pct:.1f}% {'≥' if gap_ok else '<'} 4%"
    )
    return bool(vol_ok and yang_or and low_ok and gap_ok), hit, miss


def _add_ok(bars: list[dict], st: dict, entry_idx: int) -> tuple[bool, str]:
    last = bars[-1]
    a, c = st["a"], st["c"]
    entry = bars[entry_idx]
    elapsed = len(bars) - 1 - entry_idx
    if elapsed < 1 or elapsed > 5:
        return False, f"加仓窗口 1～5 日，当前第 {elapsed} 日"
    if last["close"] < entry["close"]:
        return False, "收盘未站上试仓日收盘"
    v = _vol(last)
    if v <= c["c_avg"]:
        return False, "当日量未大于 C 段日均"
    if v > a["a_avg"] * 1.5 + 1e-12:
        return False, "当日量超过 A 段日均×1.5"
    if last["close"] >= a["hi_c"]:
        return False, "收盘已过 A 段最高收盘，不再加仓，按取关"
    return True, "加仓条件齐：收盘≥试仓日、C均量<当日量≤A均量×1.5、收盘<A最高收盘"


def evaluate_exit_s1(bars: list[dict], open_trade: dict | None, zone: dict | None) -> tuple[bool, str, str]:
    """取关。当天走 / 次日走 / 高潮走。"""
    if not bars:
        return False, "", ""
    zone = zone or {}
    entry_idx = len(bars) - 1
    trade_date = str((open_trade or {}).get("date") or (open_trade or {}).get("buy_date") or "")
    if trade_date:
        for i, row in enumerate(bars):
            if str(row.get("date")) >= trade_date:
                entry_idx = i
                break
    if len(bars) - 1 < entry_idx:
        return False, "", ""
    last = bars[-1]
    entry = bars[entry_idx]
    code = str(last.get("code") or (open_trade or {}).get("code") or "")
    stop = zone.get("stop") or (open_trade or {}).get("stop_price")
    di_low = zone.get("di_low")
    di_close = zone.get("di_close")
    a_pre = zone.get("a_pre_close")
    a_hi = zone.get("a_high_close")
    c_avg = zone.get("c_vol_avg")
    trial_c = zone.get("trial_close") or entry.get("close")
    elapsed = len(bars) - 1 - entry_idx

    if stop and last["close"] < float(stop):
        return True, "取关", f"收盘 {last['close']:.2f} < 止损 {float(stop):.2f}"
    if di_low and c_avg and last["close"] < float(di_low) and _vol(last) >= float(c_avg) * 1.3 - 1e-12:
        return True, "取关", f"收盘低于地量日最低 {float(di_low):.2f} 且量 ≥ C段日均×1.3"
    if a_pre and last["close"] < float(a_pre):
        return True, "取关", f"收盘低于 A 开始日前收盘 {float(a_pre):.2f}"
    if a_hi and last["close"] > float(a_hi):
        return True, "取关", f"收盘过 A 段最高收盘 {float(a_hi):.2f}，不再加仓，按取关"

    if elapsed >= 2 and trial_c:
        c0, c1 = bars[-2]["close"], last["close"]
        if c0 < trial_c and c1 < trial_c:
            recovered = di_close is not None and last["close"] >= float(di_close)
            if not recovered:
                return True, "取关", f"连续2日收盘低于试仓日收盘 {trial_c:.2f}，且未收回地量日收盘"
    if elapsed > 8 and trial_c:
        hold_closes = [x["close"] for x in bars[entry_idx : len(bars)]]
        new_high = max(hold_closes) > trial_c + 1e-12
        if (not new_high) and last["close"] < trial_c:
            return True, "取关", "持仓超 8 日未收盘新高，且收盘 < 试仓日收盘"

    if elapsed >= 1:
        had = False
        for i in range(entry_idx, len(bars)):
            if _is_limit_up(bars, i, code):
                had = True
                break
            g = _day_gain(bars, i)
            if g is not None and g >= 0.07 - 1e-12:
                had = True
                break
        today_limit = _is_limit_up(bars, len(bars) - 1, code)
        prior = [x["close"] for x in bars[entry_idx : len(bars) - 1]]
        new_c_high = bool(prior) and last["close"] > max(prior)
        hold = bars[entry_idx:]
        vols = [_vol(x) for x in hold]
        rank = 99
        if vols:
            order = sorted(range(len(vols)), key=lambda j: vols[j], reverse=True)
            rank = order.index(len(hold) - 1)
        over_a = bool(a_hi) and last["close"] > float(a_hi)
        if had and (not today_limit) and new_c_high and rank <= 1 and over_a:
            return True, "高潮走", "高潮走：持仓后有过涨停或涨幅≥7%，当日收盘新高、未涨停、量列买入以来前2、收盘>A段最高收盘"
    return False, "", ""


def _base_row(code: str, name: str, settings: dict) -> dict:
    return {
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
        "path_ready": False,
        "data_ok": False,
        "index_member": [],
        "tags": [],
        "key_kind": None,
        "key_price": None,
        "stop_price": None,
    }


def _stamp_structure(base: dict, st: dict) -> None:
    a, c = st["a"], st["c"]
    stop = min(c["di"]["low"], a["fund"]["low"]) * 0.97
    base["data_ok"] = True
    base["stop_price"] = round(stop, 3)
    base["key_kind"] = "地量/资金柱止损"
    base["key_price"] = round(c["di"]["low"], 3)
    base["facts"].update(
        {
            "a_start": a["s"],
            "a_end": a["e"],
            "a_len": a["len"],
            "a_gain_pct": round(a["gain"] * 100, 2),
            "a_high_close": round(a["hi_c"], 3),
            "a_pre_close": None if a["pre_c"] is None else round(a["pre_c"], 3),
            "a_vol_avg": round(a["a_avg"], 2),
            "c_len": c["len"],
            "c_vol_avg": round(c["c_avg"], 2),
            "di_low": round(c["di"]["low"], 3),
            "di_close": round(c["di"]["close"], 3),
            "di_date": c["di"].get("date"),
            "fund_low": round(a["fund"]["low"], 3),
            "fund_date": a["fund"].get("date"),
            "stop_price": round(stop, 3),
            "buy_ma20": None,
            "hs_ret": round(a["hs_ret"], 2),
            "stk_ret": round(a["stk_ret"], 2),
        }
    )


def classify_s1(
    meta: dict,
    settings: dict,
    trades: list | None,
    industry_stats: dict,
    market_3d: float | None,
    structure_only: bool = False,
    board_daily: dict | None = None,
    require_mainline: bool | None = None,
    quotes: dict | None = None,
    apply_quote: bool = True,
    open_pos: dict | None = None,
    hs: dict | None = None,
) -> dict:
    code = ts_code(str(meta.get("code") or ""))
    name = meta.get("name") or code
    bars = meta.get("bars") or load_bars(code, last_n=120)
    if apply_quote:
        bars = overlay_quote_bar(bars, code, quotes)
    base = _base_row(code, name, settings)
    if len(bars) < LISTED_DAYS:
        base["missing_rules"].append(f"池子：上市不满 {LISTED_DAYS} 日（仅 {len(bars)} 根日线）")
        return base
    last = bars[-1]
    close = last["close"]
    name = (last.get("name") or name or "").strip() or code
    if name == code and (meta.get("name") or "").strip() and meta.get("name") != code:
        name = str(meta["name"]).strip()
    base["name"] = name
    pe_meta = dict(meta or {})
    qrow = {}
    if isinstance(quotes, dict) and quotes.get(code):
        qrow = quotes.get(code) or {}
    elif isinstance(quotes, dict) and (quotes.get("pe") is not None or quotes.get("float_mcap_yi") is not None):
        qrow = quotes
    if not qrow and (pe_meta.get("pe") is None or pe_meta.get("float_mcap_yi") is None):
        from ..store import load_quotes as _lq

        qrow = _lq().get(code) or {}
    if qrow.get("pe") is not None and pe_meta.get("pe") is None:
        pe_meta["pe"] = qrow["pe"]
    if qrow.get("float_mcap_yi") is not None and pe_meta.get("float_mcap_yi") is None:
        pe_meta["float_mcap_yi"] = qrow["float_mcap_yi"]
    pe = dyn_pe_value(pe_meta)
    mcap = _float_mcap_yi(pe_meta, last)
    amt = _amt(last)
    base["facts"] = {"date": last["date"], "close": close, "pe": pe}
    industry = meta.get("industry")
    if not industry:
        from .boards import industry_of

        industry = industry_of(code)
    if industry:
        base["industry"] = industry
        base["facts"]["industry"] = industry

    if is_st_name(name):
        base["missing_rules"].append("池子：ST / *ST")
        return base
    if close < POOL_MIN_PRICE:
        base["missing_rules"].append(f"池子：股价 {close:.2f} < 5 元")
        return base
    if not structure_only:
        if amt is None:
            base["missing_rules"].append("池子：成交额证据不足")
            return base
        if amt < YI:
            base["missing_rules"].append(f"池子：成交额 {amt / YI:.2f} 亿 < 1 亿")
            return base
        base["facts"]["amount_yi"] = round(amt / YI, 2)
        if pe is None:
            base["missing_rules"].append("池子：市盈证据不足")
            return base
        if pe <= 0:
            base["missing_rules"].append(f"池子：动态市盈 {pe:.2f} ≤ 0")
            return base
        if mcap is None:
            base["missing_rules"].append("池子：流通市值证据不足")
            return base
        if mcap < MIN_FLOAT_YI:
            base["missing_rules"].append(f"池子：流通市值 {mcap:.1f} 亿 < {MIN_FLOAT_YI:.0f} 亿")
            return base
        base["facts"]["float_mcap_yi"] = round(mcap, 2)

    if _recent_limit(bars, code, 3):
        base["missing_rules"].append("结构：近 3 日有涨停")
        return base
    r5 = _ret5(bars)
    if r5 is not None and r5 >= 20:
        base["missing_rules"].append(f"结构：近 5 日涨幅 {r5:.1f}% ≥ 20%")
        return base

    st = find_structure(bars, hs if hs is not None else _hs300())
    if not st:
        base["missing_rules"].append("结构：未见近20日合格 A（5～12日、涨幅≥12%、阳≥阴、回撤、相对沪深300、资金柱）后接 4～8 日 C")
        return base
    _stamp_structure(base, st)
    a, c = st["a"], st["c"]
    after = (len(bars) - 1) - c["e"]
    base["hit_rules"].append(
        f"结构：A {a['len']} 日涨 {a['gain'] * 100:.1f}%（相对沪深300 {a['stk_ret'] - a['hs_ret']:.1f}pct），"
        f"C {c['len']} 日缩量；资金柱 {a['fund'].get('date')}，地量 {c['di'].get('date')}；"
        f"止损 {base['stop_price']:.2f}（min(地量最低,资金柱最低)×0.97）"
    )

    if open_pos:
        zone = {
            "stop": base["stop_price"],
            "di_low": c["di"]["low"],
            "di_close": c["di"]["close"],
            "a_pre_close": a.get("pre_c"),
            "a_high_close": a["hi_c"],
            "c_vol_avg": c["c_avg"],
            "trial_close": open_pos.get("buy_price") or last["close"],
        }
        hit, section, detail = evaluate_exit_s1(bars, open_pos, zone)
        if hit:
            base["status"] = "取关"
            base["gate"] = "取关"
            base["summary_bucket"] = "取关"
            base["hit_rules"].append(f"取关已见（{section}）：{detail}")
            return base
        base["status"] = "持有"
        base["gate"] = "持有"
        base["summary_bucket"] = "持有"
        base["path_ready"] = False
        add_ok, add_why = _add_ok(bars, st, _entry_idx(bars, str(open_pos.get("buy_date") or "")))
        if add_ok:
            base["hit_rules"].append("加仓：" + add_why)
        else:
            base["missing_rules"].append("加仓未齐：" + add_why)
        return base

    if after > TRIAL_WINDOW:
        base["missing_rules"].append(f"观察：C 段结束后 {after} 日仍未试仓 → 排除，重扫 A")
        return base
    if after < 1:
        base["status"] = "观察"
        base["gate"] = "观察"
        base["summary_bucket"] = "观察"
        base["missing_rules"].append("观察：C 段刚结束，当天未到试仓窗（C 结束后 3 日内）")
        return base

    ok, hit, miss = _trial_ok(bars, st)
    if not ok:
        base["status"] = "观察"
        base["gate"] = "观察"
        base["summary_bucket"] = "观察"
        base["hit_rules"].extend(["观察已见：" + x for x in hit])
        base["missing_rules"].extend(["试仓未齐：" + x for x in miss])
        base["missing_rules"].append("须先观察。当天未满足试仓")
        return base
    base["hit_rules"].extend(["试仓：" + x for x in hit])
    base["hit_rules"].append("路径到达试仓。试仓不是成交指令。仓位 8%～12%")
    base["path_ready"] = True
    base["status"] = "试仓"
    base["gate"] = "试仓"
    base["summary_bucket"] = "试仓"
    return base


def _entry_idx(bars: list[dict], buy_date: str) -> int:
    if not buy_date:
        return max(0, len(bars) - 1)
    for i, row in enumerate(bars):
        if str(row.get("date") or "") >= str(buy_date)[:10]:
            return i
    return max(0, len(bars) - 1)


def is_buy_s1(bars: list[dict], ctx: dict | None = None) -> bool:
    """与规则扫描同一套试仓。历史回放不算主线。"""
    if not bars:
        return False
    ctx = ctx or {}
    code = ts_code(str(bars[-1].get("code") or ctx.get("code") or ""))
    name = bars[-1].get("name") or ctx.get("name") or code
    meta = {
        "code": code,
        "name": name,
        "bars": bars,
        "industry": ctx.get("industry"),
        "pe": ctx.get("pe"),
        "float_mcap_yi": ctx.get("float_mcap_yi"),
    }
    row = classify_s1(
        meta,
        {"person_present": True},
        None,
        {},
        ctx.get("market_3d"),
        structure_only=False,
        apply_quote=False,
        hs=ctx.get("hs"),
    )
    return row.get("status") == "试仓"


def classify_one_s1(code: str, settings: dict, trades: list | None = None) -> dict:
    code = ts_code(code)
    uni = {ts_code(str(x.get("code") or "")): x for x in load_universe()}
    imap = _load_industry_map()
    bars = load_bars(code, last_n=120)
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
    open_pos = None
    for trade in trades or []:
        if ts_code(str(trade.get("code") or "")) == code and trade.get("direction") in ("开仓", "加仓"):
            open_pos = {"buy_date": trade.get("date"), "buy_price": None, "code": code}
    return classify_s1(meta, settings, trades, {}, None, open_pos=open_pos)


def list_s1_cycle_universe() -> list[dict]:
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


def list_s1_pool() -> list[dict]:
    """总股池 = 有效 CSV。池子/结构在 classify 记排除。"""
    from .eastmoney import hydrate_universe

    items = hydrate_universe()
    imap = _load_industry_map()
    quotes = load_quotes()
    cands = []
    for item in items:
        code = ts_code(str(item.get("code") or ""))
        if not code:
            continue
        q = quotes.get(code) or {}
        meta = dict(item)
        meta["code"] = code
        meta["name"] = meta.get("name") or q.get("name") or code
        meta["industry"] = imap.get(code) or meta.get("industry") or q.get("industry") or ""
        if q.get("pe") is not None:
            meta["pe"] = q["pe"]
        if q.get("float_mcap_yi") is not None:
            meta["float_mcap_yi"] = q["float_mcap_yi"]
        if q.get("amount_yi") is not None:
            meta["amount_yi"] = q["amount_yi"]
        cands.append(meta)
    return cands


def scan_structure_one(settings: dict, trades: list | None = None) -> list[dict]:
    cands = list_s1_pool()
    quotes = load_quotes()
    hs = _hs300()
    opens = {}
    try:
        from .buy_log import load_buy_log, record_since

        since = record_since()
        for item in (load_buy_log("rules2").get("items") or []):
            if item.get("closed"):
                continue
            if str(item.get("buy_date") or "")[:10] < since:
                continue
            opens[ts_code(str(item.get("code") or ""))] = item
    except Exception:
        opens = {}
    scan_structure_one.funnel = []
    scan_structure_one.mainline = False
    scan_structure_one.market = {"name": "沪深300", "ret_3d_pct": None}
    scan_structure_one.board_daily = None
    rows = [
        classify_s1(
            item,
            settings,
            trades,
            {},
            None,
            quotes=quotes,
            open_pos=opens.get(ts_code(str(item.get("code") or ""))),
            hs=hs,
        )
        for item in cands
    ]
    order = {name: i for i, name in enumerate(GATES_S1)}
    rows.sort(key=lambda item: (order.get(item["status"], 9), item.get("industry") or "", item["code"]))
    return rows
