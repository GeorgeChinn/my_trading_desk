"""RULES2 野人哥低吸。按 RULES2.MD 全文量化。他没给的阈值不写。无分时不得把日线代理记成正式试仓。"""
from __future__ import annotations

import re

from ..config import CSV_DIR, GATES_S1
from ..store import load_quotes, load_universe
from .bars import bar_amount, load_bars, overlay_quote_bar, peek_last_bar, ts_code
from .pool import is_st_name
from .scanner import FACT_NOTE, dyn_pe_value

RATIO_7030 = 30.0 / 70.0
RATIO_5149 = 40.0 / 60.0
C_MIN_LOW_ABSORB = 8
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def _strip_html_comments(text: str) -> str:
    return _HTML_COMMENT.sub("", text or "")


def need_mainline(text: str | None = None) -> bool:
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


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


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
    if not day:
        return None
    if day in hs:
        return hs[day]
    prev = [d for d in hs if d <= day]
    return hs[max(prev)] if prev else None


def _day_ret(bars: list[dict], i: int) -> float | None:
    if i < 1:
        return None
    a, b = bars[i - 1].get("close"), bars[i].get("close")
    if not a or not b:
        return None
    return b / a - 1.0


def _close_dd_and_gain(seg: list[dict]) -> tuple[float | None, float | None]:
    closes = [x.get("close") for x in seg]
    if any(c is None for c in closes) or not closes:
        return None, None
    start = closes[0]
    hi = max(closes)
    if not start or hi <= start:
        return None, None
    gain = (hi - start) / start
    peak = closes[0]
    max_dd = 0.0
    for c in closes:
        peak = max(peak, c)
        if peak:
            max_dd = max(max_dd, (peak - c) / peak)
    return max_dd, gain


def _check_a(bars: list[dict], s: int, e: int) -> dict | None:
    """近 20 日连续上涨段：结束日收在该段最高收盘，7030 小回；>40/60 为 5149。"""
    if e <= s:
        return None
    seg = bars[s : e + 1]
    end_c = bars[e].get("close")
    hi_c = max(x["close"] for x in seg if x.get("close"))
    if end_c is None or hi_c is None:
        return None
    if end_c < hi_c:
        return None
    max_dd, gain = _close_dd_and_gain(seg)
    if gain is None or gain <= 0:
        return None
    ratio = max_dd / gain
    if ratio > RATIO_5149 + 1e-12:
        return None
    if ratio > RATIO_7030 + 1e-12:
        return None
    vols = [_vol(x) for x in seg]
    a_avg = _mean(vols)
    if a_avg <= 0:
        return None
    pre_c = bars[s - 1].get("close") if s > 0 else None
    return {
        "s": s,
        "e": e,
        "len": e - s + 1,
        "start_c": seg[0]["close"],
        "end_c": end_c,
        "hi_c": hi_c,
        "gain": gain,
        "dd": max_dd,
        "ratio": ratio,
        "a_avg": a_avg,
        "pre_c": pre_c,
    }


def _cycle_pressure(bars: list[dict], a: dict, hs: dict[str, float]) -> tuple[bool | None, str]:
    """先强这段里：沪深300 收跌日，个股跌幅小于沪深300 的天数 ≥ 一半。"""
    down = 0
    hold = 0
    missing = 0
    for i in range(a["s"], a["e"] + 1):
        if i < 1:
            continue
        d0 = str(bars[i - 1].get("date") or "")
        d1 = str(bars[i].get("date") or "")
        h0, h1 = _hs_px(hs, d0), _hs_px(hs, d1)
        if not h0 or not h1:
            missing += 1
            continue
        hs_ret = h1 / h0 - 1.0
        if hs_ret >= 0:
            continue
        down += 1
        stk = _day_ret(bars, i)
        if stk is None:
            missing += 1
            continue
        if stk > hs_ret:
            hold += 1
    if down == 0 and missing:
        return None, "周期测压：沪深300 先强段内收跌日证据不足"
    if down == 0:
        return True, "周期测压：先强段内沪深300 无收跌日，测压未挡"
    share = hold / down
    if share + 1e-12 >= 0.5:
        return True, f"周期测压：大盘收跌 {down} 日，个股跌幅更小 {hold} 日（≥一半）"
    return False, f"周期测压：大盘收跌 {down} 日，个股只在 {hold} 日抗跌（不足一半），大盘跌它跟跌"


def _fund_bar(bars: list[dict], a: dict) -> dict | None:
    """A 段放量阳线。放量 = 量 ≥ A 段日均（与第 5 条同一口径）。取离结束最近的一根。"""
    fund = None
    for i in range(a["s"], a["e"] + 1):
        row = bars[i]
        if _yang(row) and _vol(row) >= a["a_avg"] - 1e-12:
            fund = {"idx": i, "low": row["low"], "close": row["close"], "vol": _vol(row), "date": row.get("date")}
    if not fund:
        return None
    if bars[a["e"]]["close"] < fund["close"]:
        return None
    return fund


def _c_retrace_ratio(a: dict, c_seg: list[dict]) -> float | None:
    lows = [x.get("close") for x in c_seg if x.get("close")]
    if not lows:
        return None
    c_min = min(lows)
    den = a["hi_c"] - a["start_c"]
    if den <= 0:
        return None
    return (a["hi_c"] - c_min) / den


def _broken_fund(bars: list[dict], a: dict, fund: dict, c0: int, today: int) -> bool:
    """不破资金柱最低价。放量破位后再抄 = 第二次释放。"""
    for i in range(c0, today + 1):
        row = bars[i]
        if row.get("low") is not None and row["low"] < fund["low"]:
            return True
        if row.get("close") is not None and row["close"] < fund["low"] and _vol(row) >= a["a_avg"] - 1e-12:
            return True
    return False


def _di_volume(bars: list[dict], a: dict, c0: int, i: int) -> bool:
    """当日量是 C 段到当日为止的最低量，且低于 A 段日均。"""
    v = _vol(bars[i])
    if v >= a["a_avg"] - 1e-12:
        return False
    so_far = [_vol(bars[j]) for j in range(c0, i + 1)]
    return bool(so_far) and v <= min(so_far) + 1e-12


def _di_price_ok(bars: list[dict], c0: int, i: int) -> bool:
    """地价：地量日附近不再创新低，或创新低但当日收回。"""
    if i <= c0:
        return True
    prior_low = min(bars[j]["low"] for j in range(c0, i) if bars[j].get("low") is not None)
    row = bars[i]
    low, close = row.get("low"), row.get("close")
    if low is None or close is None:
        return False
    if low >= prior_low - 1e-12:
        return True
    return close >= prior_low - 1e-12


def find_structure(bars: list[dict], hs: dict[str, float] | None = None) -> dict | None:
    """1～4：周期测压 + 近20日最晚 7030 A + 资金柱 + 第一段 C≥8 缩量小回。"""
    n = len(bars)
    if n < 10:
        return None
    hs = hs if hs is not None else _hs300()
    win0 = max(0, n - 20)
    last_e = n - 1 - C_MIN_LOW_ABSORB
    if last_e < win0:
        return None
    best_a = None
    for e in range(last_e, win0 - 1, -1):
        for s in range(win0, e):
            a = _check_a(bars, s, e)
            if a:
                best_a = a
                break
        if best_a:
            break
    if not best_a:
        return None
    ok, why = _cycle_pressure(bars, best_a, hs)
    if ok is None:
        return None
    if ok is False:
        return None
    fund = _fund_bar(bars, best_a)
    if not fund:
        return None
    c0 = best_a["e"] + 1
    if c0 >= n:
        return None
    c_len = n - c0
    if c_len < C_MIN_LOW_ABSORB:
        return None
    if c_len == 4:
        return None
    c_seg = bars[c0:]
    c_avg = _mean([_vol(x) for x in c_seg])
    if c_avg >= best_a["a_avg"] - 1e-12:
        return None
    cr = _c_retrace_ratio(best_a, c_seg)
    if cr is None or cr > RATIO_7030 + 1e-12:
        return None
    if _broken_fund(bars, best_a, fund, c0, n - 1):
        return None
    return {
        "a": best_a,
        "fund": fund,
        "c0": c0,
        "c_len": c_len,
        "c_avg": c_avg,
        "c_retrace": cr,
        "pressure": why,
    }


def _latest_di(bars: list[dict], a: dict, c0: int) -> dict | None:
    di = None
    for i in range(c0, len(bars)):
        if _di_volume(bars, a, c0, i) and _di_price_ok(bars, c0, i):
            row = bars[i]
            di = {"idx": i, "low": row["low"], "close": row["close"], "vol": _vol(row), "date": row.get("date")}
    return di


def _has_intraday(code: str, day: str) -> bool:
    """本系统无分时库。有分时再接。"""
    return False


def evaluate_exit_s1(bars: list[dict], open_trade: dict | None, zone: dict | None) -> tuple[bool, str, str]:
    """第 7 条取关。"""
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
    last = bars[-1]
    code = str(last.get("code") or (open_trade or {}).get("code") or "")
    fund_low = zone.get("fund_low")
    a_pre = zone.get("a_pre_close")
    a_hi = zone.get("a_high_close")
    a_avg = zone.get("a_vol_avg")
    di_low = zone.get("di_low")
    v = _vol(last)
    close = last.get("close")
    if close is None:
        return False, "", ""

    if fund_low is not None and close < float(fund_low):
        return True, "取关", f"收盘跌破资金柱最低价 {float(fund_low):.2f}"
    if a_pre is not None and a_avg is not None and close < float(a_pre) and v >= float(a_avg) - 1e-12:
        return True, "取关", f"收盘跌破 A 段起点前收盘 {float(a_pre):.2f} 且当日放量"
    if di_low is not None and a_avg is not None and v >= float(a_avg) - 1e-12:
        low = last.get("low")
        if low is not None and low < float(di_low):
            return True, "取关", f"C 段之后再次放量破地量低点 {float(di_low):.2f}（倒 ACB）"
        if close < float(di_low):
            return True, "取关", f"C 段之后再次放量破地量低点 {float(di_low):.2f}（倒 ACB）"
    if a_hi is not None and close > float(a_hi):
        prior = [x["close"] for x in bars[entry_idx : len(bars) - 1] if x.get("close")]
        new_high = bool(prior) and close > max(prior)
        sealed = _is_limit_up(bars, len(bars) - 1, code)
        hold_vols = [_vol(x) for x in bars[entry_idx:]]
        is_top = bool(hold_vols) and v >= max(hold_vols) - 1e-12
        if new_high and (not sealed) and is_top:
            return True, "取关", "已到 B：收盘过 A 段最高后放量滞涨（收盘新高、未封死、量是持仓以来最大之一）"
    return False, "", ""


def _base_row(code: str, name: str) -> dict:
    return {
        "code": code,
        "name": name,
        "status": "排除",
        "gate": "排除",
        "summary_bucket": "排除",
        "path": "野人哥低吸",
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
        "daily_proxy": False,
    }


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
    base = _base_row(code, name)
    if len(bars) < 12:
        base["missing_rules"].append("数据不足：日线不够核对周期 / A / C")
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
    if qrow.get("pe") is not None:
        pe_meta["pe"] = qrow["pe"]
    pe = dyn_pe_value(pe_meta)
    base["facts"] = {"date": last["date"], "close": close, "pe": pe}
    industry = meta.get("industry")
    if not industry:
        from .boards import industry_of

        industry = industry_of(code)
    if industry:
        base["industry"] = industry
        base["facts"]["industry"] = industry

    hs_map = hs if hs is not None else _hs300()
    n = len(bars)
    win0 = max(0, n - 20)
    last_e = n - 1 - C_MIN_LOW_ABSORB
    best_a = None
    if last_e >= win0:
        for e in range(last_e, win0 - 1, -1):
            for s in range(win0, e):
                a = _check_a(bars, s, e)
                if a:
                    best_a = a
                    break
            if best_a:
                break
    if not best_a:
        base["missing_rules"].append("流畅高频 7030：近 20 日未见结束日收在最高、回撤/涨幅≤30/70、且其后已有 C≥8 的上涨段")
        return base
    max_dd, gain = best_a["dd"], best_a["gain"]
    if gain and max_dd / gain > RATIO_5149:
        base["missing_rules"].append("5149：回撤/涨幅 > 40/60，排除")
        return base
    ok, why = _cycle_pressure(bars, best_a, hs_map)
    if ok is None:
        base["missing_rules"].append(why)
        return base
    if ok is False:
        base["missing_rules"].append(why)
        return base
    base["hit_rules"].append(why)
    fund = _fund_bar(bars, best_a)
    if not fund:
        base["missing_rules"].append("资金柱：A 段内无放量阳线，或结束日收盘低于该柱收盘（重心下移）")
        return base
    base["hit_rules"].append(f"资金柱：{fund.get('date')} 放量阳线，结束日重心未下移")

    c0 = best_a["e"] + 1
    c_len = n - c0
    if c_len < C_MIN_LOW_ABSORB:
        if c_len == 4:
            base["missing_rules"].append("时间周期：C=4 是日线 ACB 进攻，本规则不做")
        elif c_len < 4:
            base["missing_rules"].append(f"时间周期：C={c_len} 是连板/轴心，本规则不做")
        else:
            base["missing_rules"].append(f"时间周期：低吸只做 C≥8，当前 C={c_len}")
        return base
    c_seg = bars[c0:]
    c_avg = _mean([_vol(x) for x in c_seg])
    if c_avg >= best_a["a_avg"] - 1e-12:
        base["missing_rules"].append("时间周期：C 段未缩量（C 日均量未小于 A 日均量）")
        return base
    cr = _c_retrace_ratio(best_a, c_seg)
    if cr is None or cr > RATIO_7030 + 1e-12:
        base["missing_rules"].append("时间周期：C 段回撤/A 段涨幅 > 30/70，或从 B 阴跌吃掉 A 涨幅")
        return base
    if _broken_fund(bars, best_a, fund, c0, n - 1):
        base["missing_rules"].append("C 区失效：已破资金柱最低价，或第一段 C 放量破位后再抄（第二次释放）")
        return base
    base["hit_rules"].append(
        f"7030 A {best_a['len']} 日（回撤/涨幅 {best_a['ratio'] * 100:.1f}%≤30/70）；"
        f"第一段 C {c_len} 日缩量小回"
    )
    base["data_ok"] = True
    base["key_kind"] = "资金柱最低价"
    base["key_price"] = round(fund["low"], 3)
    base["stop_price"] = round(fund["low"], 3)
    base["facts"].update(
        {
            "a_len": best_a["len"],
            "a_gain_pct": round(best_a["gain"] * 100, 2),
            "a_high_close": round(best_a["hi_c"], 3),
            "a_pre_close": None if best_a["pre_c"] is None else round(best_a["pre_c"], 3),
            "a_vol_avg": round(best_a["a_avg"], 2),
            "c_len": c_len,
            "c_vol_avg": round(c_avg, 2),
            "fund_low": round(fund["low"], 3),
            "fund_date": fund.get("date"),
            "stop_price": round(fund["low"], 3),
        }
    )
    st = {"a": best_a, "fund": fund, "c0": c0, "c_avg": c_avg}

    if open_pos:
        di = _latest_di(bars, best_a, c0)
        zone = {
            "fund_low": fund["low"],
            "a_pre_close": best_a.get("pre_c"),
            "a_high_close": best_a["hi_c"],
            "a_vol_avg": best_a["a_avg"],
            "di_low": None if not di else di["low"],
        }
        hit, section, detail = evaluate_exit_s1(bars, open_pos, zone)
        if hit:
            base["status"] = "取关"
            base["gate"] = "取关"
            base["summary_bucket"] = "取关"
            base["hit_rules"].append(f"取关已见：{detail}")
            return base
        base["status"] = "持有"
        base["gate"] = "持有"
        base["summary_bucket"] = "持有"
        if close > best_a["hi_c"]:
            base["hit_rules"].append("已到 B：收盘过 A 段最高，停止加仓，改为减")
        elif _vol(last) >= best_a["a_avg"] - 1e-12 and not _yang(last):
            base["missing_rules"].append("仓位：不在 C 段放量阴线上加")
        else:
            base["hit_rules"].append("仓位：C 区分批建。第一笔试仓，确认地量后再加一笔")
        return base

    di = _latest_di(bars, best_a, c0)
    if not di:
        base["status"] = "观察"
        base["gate"] = "观察"
        base["summary_bucket"] = "观察"
        base["missing_rules"].append("地量地价：1～4 已成立，地量还没走完")
        return base
    base["facts"]["di_low"] = round(di["low"], 3)
    base["facts"]["di_close"] = round(di["close"], 3)
    base["facts"]["di_date"] = di.get("date")
    last_i = n - 1
    vol_now = _vol(last)
    fangliang = vol_now >= best_a["a_avg"] - 1e-12
    is_di_day = last_i == di["idx"]
    after_di_yang = last_i > di["idx"] and _yang(last) and (not fangliang)
    daily_trial = (is_di_day or after_di_yang) and (not fangliang)
    if fangliang:
        base["status"] = "观察"
        base["gate"] = "观察"
        base["summary_bucket"] = "观察"
        base["hit_rules"].append("地量已见")
        base["missing_rules"].append("当日放量（量 ≥ A 段日均）= 不买")
        return base
    if not daily_trial:
        base["status"] = "观察"
        base["gate"] = "观察"
        base["summary_bucket"] = "观察"
        base["hit_rules"].append("地量已见")
        base["missing_rules"].append("试仓：不是地量日，也不是地量后仍缩量的阳线")
        return base
    has_fs = _has_intraday(code, str(last.get("date") or ""))
    if not has_fs:
        base["status"] = "观察"
        base["gate"] = "观察"
        base["summary_bucket"] = "观察"
        base["daily_proxy"] = True
        base["path_ready"] = False
        base["hit_rules"].append("日线代理观察：地量日或地量后缩量阳线（无分时，不得记为正式试仓）")
        base["missing_rules"].append("分时数据缺失：不得把日线代理标成完整低吸，回测不得记正式试仓成交")
        return base
    base["status"] = "试仓"
    base["gate"] = "试仓"
    base["summary_bucket"] = "试仓"
    base["path_ready"] = True
    base["hit_rules"].append("试仓：地量日或地量后仍缩量阳线，且分时条件已核")
    return base


def is_buy_s1(bars: list[dict], ctx: dict | None = None) -> bool:
    """回测开段：正式试仓，或无分时的日线代理。代理不得写入买入池。"""
    if not bars:
        return False
    ctx = ctx or {}
    code = ts_code(str(bars[-1].get("code") or ctx.get("code") or ""))
    name = bars[-1].get("name") or ctx.get("name") or code
    row = classify_s1(
        {
            "code": code,
            "name": name,
            "bars": bars,
            "industry": ctx.get("industry"),
            "pe": ctx.get("pe"),
            "float_mcap_yi": ctx.get("float_mcap_yi"),
        },
        {},
        None,
        {},
        None,
        apply_quote=False,
        hs=ctx.get("hs"),
    )
    if row.get("status") == "试仓":
        return True
    return bool(row.get("daily_proxy"))


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
        for item in load_buy_log("rules2").get("items") or []:
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
