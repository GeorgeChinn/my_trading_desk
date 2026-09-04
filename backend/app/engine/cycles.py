"""RULES path cycles: 买入日 → 清仓条件日. Live log + history backtest. Not an order."""
from __future__ import annotations

from datetime import datetime

from ..config import CYCLES_PATH, HHV_LOOKBACK, KDJ_HIGH, KDJ_LOW
from ..store import read_json, write_json
from .bars import attach_indicators, load_bars, ts_code
from .rules_bind import parse_flags
from .scanner import (
    _cross_down,
    _cross_up,
    _green_shrink_not_new_low,
    _just_red,
    _recent,
    kdj_overbought,
    macd_section5,
    ma30_down_veto,
    nearer_to_window_low,
    pullback_60_below_zero,
    recent_dif_golden_cross,
    zero_axis_golden,
)


def _prefix_series(bars: list[dict], end_idx: int) -> dict:
    sl = bars[: end_idx + 1]
    return {
        "bars": sl,
        "hist": [b.get("hist") for b in sl],
        "dif": [b.get("dif") for b in sl],
        "dea": [b.get("dea") for b in sl],
        "k": [b.get("k") for b in sl],
        "d": [b.get("d") for b in sl],
        "j": [b.get("j") for b in sl],
        "h": [b.get("high") for b in sl],
        "c": [b.get("close") for b in sl],
        "last": sl[-1],
        "prev": sl[-2] if len(sl) > 1 else None,
    }


def red_wave_peaks(hist: list) -> list[float]:
    peaks: list[float] = []
    cur = None
    in_red = False
    for h in hist:
        if h is not None and h > 0:
            if not in_red:
                in_red = True
                cur = h
            else:
                cur = max(cur, h)
        elif in_red:
            peaks.append(cur)
            in_red = False
            cur = None
    if in_red and cur is not None:
        peaks.append(cur)
    return peaks


def is_buy_signal(s: dict, flags: dict) -> bool:
    last = s["last"]
    if last.get("dif") is None or last.get("hist") is None or last.get("k") is None:
        return False
    if flags.get("veto_kdj_overbought", True):
        ob, _ = kdj_overbought(last.get("k"), last.get("j"))
        if ob is not False:
            return False
    if flags.get("veto_pullback_60", True):
        pb, _, _ = pullback_60_below_zero(s["h"], last.get("close"), last.get("dif"))
        if pb is True:
            return False
    if flags.get("veto_ma30_down", True):
        m30, _, _ = ma30_down_veto(s["c"])
        if m30 is True:
            return False
    macd_ok, _ = macd_section5(s["hist"])
    kd_cross = _cross_up(s["k"], s["d"])
    k0, d0 = s["k"][-1], s["d"][-1]
    kd_le_20 = k0 is not None and d0 is not None and max(k0, d0) <= KDJ_LOW
    j_prev = _recent(s["j"], 20, skip_last=1)
    kdj_ok = bool((kd_cross and kd_le_20) or (j_prev and s["j"][-1] is not None and s["j"][-1] > min(j_prev)))
    if flags.get("wait_need_kdj_band", True):
        k_last, j_last = last.get("k"), last.get("j")
        if k_last is None or j_last is None or not (j_last < 80 and k_last <= 50):
            return False
    if flags.get("wait_need_low_zone", True):
        dif_low, _ = nearer_to_window_low(s["dif"], last.get("dif"), "DIF")
        px_low, _ = nearer_to_window_low(s["c"], last.get("close"), "收盘")
        low_ok = dif_low is True and px_low is True
    else:
        low_ok = True
    if not (macd_ok and kdj_ok and low_ok):
        return False
    buy_cross, _, cross_idx = recent_dif_golden_cross(
        s["dif"], s["dea"], within_two_days=bool(flags.get("cross_within_two_days", False))
    )
    h0, h1 = (s["hist"][-2], s["hist"][-1]) if len(s["hist"]) > 1 else (None, s["hist"][-1])
    hist_green_to_red = _just_red(s["hist"], len(s["hist"]) - 1) or _just_red(s["hist"], len(s["hist"]) - 2)
    already_gold = last.get("dif") is not None and last.get("dea") is not None and last["dif"] > last["dea"]
    cont = False
    hist = s["hist"]
    if len(hist) >= 3 and all(hist[i] is not None and hist[i] < 0 for i in (-3, -2, -1)):
        cont = abs(hist[-1]) < abs(hist[-2]) < abs(hist[-3])
    near_zero = h1 is not None and h1 < 0 and h0 is not None and abs(h1) < abs(h0)
    still_green_ok = _green_shrink_not_new_low(hist, len(hist) - 1)
    buy_hist = bool(cont and near_zero and still_green_ok) or hist_green_to_red or (buy_cross and already_gold)
    near_low = True
    if flags.get("buy_need_dif_near_min", True):
        near_low, _ = nearer_to_window_low(s["dif"], last.get("dif"), "DIF")
        near_low = near_low is True
    zero_ok = True
    if flags.get("buy_need_zero_axis", True):
        z, _ = zero_axis_golden(s["dif"], s["dea"], cross_idx)
        zero_ok = z is True
    px6 = True
    if flags.get("buy_need_price_low", True):
        p, _ = nearer_to_window_low(s["c"], last.get("close"), "收盘")
        px6 = p is True
    return bool(buy_cross and buy_hist and near_low and zero_ok and px6)


def is_exit_signal(s: dict) -> bool:
    last, prev = s["last"], s["prev"]
    if not prev or last.get("dif") is None or prev.get("dif") is None:
        return False
    peaks = red_wave_peaks(s["hist"])
    wave_ok = len(peaks) >= 2 and peaks[-1] < peaks[-2]
    dif_down = last["dif"] < prev["dif"]
    hhv = max(_recent(s["h"], HHV_LOOKBACK) or [last["high"]])
    new_high = last["high"] >= hhv
    kd_dead = _cross_down(s["k"], s["d"])
    k0, d0 = s["k"][-1], s["d"][-1]
    kd_high = k0 is not None and d0 is not None and min(k0, d0) >= KDJ_HIGH
    j_prev = _recent(s["j"], 20, skip_last=1)
    kdj_not_new_high = bool(j_prev) and s["j"][-1] is not None and s["j"][-1] < max(j_prev)
    kdj_exit = bool((kd_dead and kd_high) or kdj_not_new_high)
    return bool(wave_ok and dif_down and new_high and kdj_exit)


def walk_cycles(bars: list[dict], flags: dict | None = None) -> tuple[list[dict], dict | None]:
    flags = flags or parse_flags()
    if len(bars) < 50:
        return [], None
    n = len(bars)
    cycles = []
    open_i = None
    for i in range(40, n):
        s = _prefix_series(bars, i)
        buy = is_buy_signal(s, flags)
        ex = is_exit_signal(s)
        if open_i is None:
            if buy:
                open_i = i
            continue
        if i > open_i and ex:
            cycles.append(_cycle_stats(bars, open_i, i))
            open_i = None
    live = None
    if open_i is not None:
        live = _cycle_stats(bars, open_i, n - 1, closed=False)
    return cycles, live


def _cycle_stats(bars: list[dict], a: int, b: int, closed: bool = True) -> dict:
    entry = bars[a]
    last = bars[b]
    path = bars[a : b + 1]
    closes = [x["close"] for x in path]
    entry_px = float(entry["close"])
    exit_px = float(last["close"])
    ret = (exit_px / entry_px - 1.0) if entry_px else 0.0
    peak = closes[0]
    mdd = 0.0
    for px in closes:
        peak = max(peak, px)
        if peak:
            mdd = min(mdd, px / peak - 1.0)
    pnl_pct = round(ret * 100, 2)
    pnl_ps = round(exit_px - entry_px, 4)
    if closed:
        result = "盈利" if ret > 0 else ("亏损" if ret < 0 else "持平")
        status = "已结束"
    else:
        result = "浮动"
        status = "进行中"
    return {
        "start_date": entry["date"],
        "end_date": last["date"] if closed else None,
        "asof_date": last["date"],
        "start_close": round(entry_px, 4),
        "end_close": round(exit_px, 4) if closed else None,
        "last_close": round(exit_px, 4),
        "buy_date": entry["date"],
        "buy_price": round(entry_px, 4),
        "sell_date": last["date"] if closed else None,
        "sell_price": round(exit_px, 4) if closed else None,
        "mark_price": round(exit_px, 4),
        "pnl_pct": pnl_pct,
        "pnl_per_share": pnl_ps,
        "status": status,
        "result": result,
        "return_pct": pnl_pct if closed else None,
        "open_return_pct": pnl_pct,
        "max_drawdown_pct": round(mdd * 100, 2),
        "bars": len(path),
        "closed": closed,
        "win": bool(closed and ret > 0),
    }


def _segment_row(code: str, name: str, stats: dict, seq: int) -> dict:
    return {
        "id": f"{code}-{stats.get('buy_date')}-{seq}",
        "seq": seq,
        "code": ts_code(code),
        "name": name,
        "buy_date": stats.get("buy_date"),
        "buy_price": stats.get("buy_price"),
        "sell_date": stats.get("sell_date"),
        "sell_price": stats.get("sell_price"),
        "mark_price": stats.get("mark_price"),
        "pnl_pct": stats.get("pnl_pct"),
        "pnl_per_share": stats.get("pnl_per_share"),
        "max_drawdown_pct": stats.get("max_drawdown_pct"),
        "bars": stats.get("bars"),
        "status": stats.get("status"),
        "result": stats.get("result"),
        "closed": bool(stats.get("closed")),
        "win": bool(stats.get("win")),
    }


def walk_stock_segments(code: str, name: str, flags: dict) -> list[dict]:
    bars = attach_indicators(load_bars(code))
    closed, live = walk_cycles(bars, flags)
    out = []
    for i, item in enumerate(closed, start=1):
        out.append(_segment_row(code, name, item, i))
    if live:
        out.append(_segment_row(code, name, live, len(closed) + 1))
    return out


def _empty_summary() -> dict:
    return {
        "open": 0,
        "closed": 0,
        "wins": 0,
        "losses": 0,
        "flat": 0,
        "win_rate": None,
        "avg_pnl_pct": None,
        "total": 0,
    }


def summarize_segments(segments: list[dict]) -> dict:
    closed = [s for s in segments if s.get("closed")]
    open_n = sum(1 for s in segments if not s.get("closed"))
    wins = sum(1 for s in closed if s.get("result") == "盈利")
    losses = sum(1 for s in closed if s.get("result") == "亏损")
    flat = sum(1 for s in closed if s.get("result") == "持平")
    n = len(closed)
    avg = sum(s.get("pnl_pct") or 0 for s in closed) / n if n else None
    return {
        "open": open_n,
        "closed": n,
        "wins": wins,
        "losses": losses,
        "flat": flat,
        "win_rate": round(wins / n * 100, 1) if n else None,
        "avg_pnl_pct": round(avg, 2) if avg is not None else None,
        "total": len(segments),
    }


def cycles_page(universe: list[dict], flags: dict | None = None, ruleset: dict | None = None) -> dict:
    """One segment = listed in 买入 pool → §7 sell. Same stock may have many segments."""
    from .rulesets import ENGINE_LOW_GOLDEN, public_ruleset

    flags = flags or parse_flags()
    pub = public_ruleset(ruleset) if ruleset else None
    engine = (ruleset or {}).get("engine") or ENGINE_LOW_GOLDEN
    note = "一段轨迹 = 列入买入池（确认收盘）→ 卖出条件日。买入价/卖出价用当日收盘。这是事实记录，不是成交指令。"
    if engine != ENGINE_LOW_GOLDEN:
        payload = {
            "fact_note": "这是事实记录",
            "note": (ruleset or {}).get("engine_note") or note,
            "ruleset": pub,
            "segments": [],
            "summary": _empty_summary(),
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        save_cycles(payload, (ruleset or {}).get("id") or "rules")
        return payload
    segments: list[dict] = []
    for meta in universe:
        code = ts_code(str(meta.get("code") or ""))
        name = meta.get("name") or code
        if not code:
            continue
        segments.extend(walk_stock_segments(code, name, flags))
    open_rows = [s for s in segments if not s.get("closed")]
    closed_rows = [s for s in segments if s.get("closed")]
    open_rows.sort(key=lambda s: (s.get("buy_date") or "", s.get("code") or ""), reverse=True)
    closed_rows.sort(key=lambda s: (s.get("sell_date") or "", s.get("code") or ""), reverse=True)
    segments = open_rows + closed_rows
    payload = {
        "fact_note": "这是事实记录",
        "note": note,
        "ruleset": pub,
        "segments": segments,
        "summary": summarize_segments(segments),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    save_cycles(payload, (ruleset or {}).get("id") or "rules")
    return payload


def save_cycles(payload: dict, ruleset_id: str = "rules") -> None:
    store = read_json(CYCLES_PATH, {})
    if not isinstance(store, dict):
        store = {}
    store.pop("segments", None)
    store.pop("summary", None)
    store["cleared_at"] = store.get("cleared_at") or datetime.now().strftime("%Y-%m-%d")
    store[ruleset_id] = {
        "updated_at": payload.get("updated_at"),
        "summary": payload.get("summary"),
        "segment_count": len(payload.get("segments") or []),
    }
    write_json(CYCLES_PATH, store)
