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
    macd_section5,
    nearer_to_window_low,
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
    macd_ok, _ = macd_section5(s["hist"])
    kd_cross = _cross_up(s["k"], s["d"])
    k0, d0 = s["k"][-1], s["d"][-1]
    kd_le_20 = k0 is not None and d0 is not None and max(k0, d0) <= KDJ_LOW
    j_prev = _recent(s["j"], 20, skip_last=1)
    kdj_ok = bool((kd_cross and kd_le_20) or (j_prev and s["j"][-1] is not None and s["j"][-1] > min(j_prev)))
    if flags.get("wait_need_low_zone", True):
        dif_low, _ = nearer_to_window_low(s["dif"], last.get("dif"), "DIF")
        px_low, _ = nearer_to_window_low(s["c"], last.get("close"), "收盘")
        low_ok = dif_low is True and px_low is True
    else:
        low_ok = True
    if not (macd_ok and kdj_ok and low_ok):
        return False
    buy_cross, _, cross_idx = recent_dif_golden_cross(s["dif"], s["dea"])
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
    return {
        "start_date": entry["date"],
        "end_date": last["date"] if closed else None,
        "asof_date": last["date"],
        "start_close": round(entry_px, 4),
        "end_close": round(exit_px, 4) if closed else None,
        "last_close": round(exit_px, 4),
        "return_pct": round(ret * 100, 2) if closed else None,
        "open_return_pct": round(ret * 100, 2),
        "max_drawdown_pct": round(mdd * 100, 2),
        "bars": len(path),
        "closed": closed,
        "win": bool(closed and ret > 0),
        "path": [{"date": x["date"], "close": x["close"], "hist": x.get("hist"), "dif": x.get("dif")} for x in path],
    }


def rank_stock(code: str, name: str, flags: dict | None = None) -> dict:
    bars = attach_indicators(load_bars(code))
    cycles, live = walk_cycles(bars, flags)
    closed = [c for c in cycles if c["closed"]]
    wins = sum(1 for c in closed if c["win"])
    n = len(closed)
    avg_ret = sum(c["return_pct"] or 0 for c in closed) / n if n else None
    avg_mdd = sum(c["max_drawdown_pct"] for c in closed) / n if n else None
    return {
        "code": ts_code(code),
        "name": name,
        "samples": n,
        "wins": wins,
        "win_rate": round(wins / n * 100, 1) if n else None,
        "avg_return_pct": round(avg_ret, 2) if avg_ret is not None else None,
        "avg_drawdown_pct": round(avg_mdd, 2) if avg_mdd is not None else None,
        "cycles": [{k: c[k] for k in c if k != "path"} for c in closed],
        "live": {k: live[k] for k in live if k != "path"} if live else None,
        "evidence": "证据不足" if n == 0 else "这是事实记录（确认收盘回放）",
    }


def rank_buy_pool(buy_rows: list[dict]) -> list[dict]:
    flags = parse_flags()
    ranked = [rank_stock(r["code"], r.get("name") or r["code"], flags) for r in buy_rows]
    ranked.sort(
        key=lambda x: (
            x["win_rate"] is None,
            -(x["win_rate"] or -1),
            -(x["avg_return_pct"] or -999),
            x["avg_drawdown_pct"] if x["avg_drawdown_pct"] is not None else 0,
        )
    )
    for i, row in enumerate(ranked, start=1):
        row["rank"] = i
    return ranked


def load_cycles() -> dict:
    data = read_json(CYCLES_PATH, {"open": [], "closed": []})
    if not isinstance(data, dict):
        return {"open": [], "closed": []}
    data.setdefault("open", [])
    data.setdefault("closed", [])
    return data


def save_cycles(payload: dict) -> None:
    write_json(CYCLES_PATH, payload)


def update_live_episodes(buy_rows: list[dict]) -> dict:
    """Open an episode when a name first appears in 买入; close when §7 hits."""
    flags = parse_flags()
    store = load_cycles()
    open_map = {item["code"]: item for item in store.get("open", [])}
    closed = list(store.get("closed", []))
    today_buys = {ts_code(r["code"]): r for r in buy_rows}
    asof = None
    for code, row in today_buys.items():
        bars = attach_indicators(load_bars(code))
        if not bars:
            continue
        asof = bars[-1]["date"]
        s = _prefix_series(bars, len(bars) - 1)
        if code not in open_map:
            if is_buy_signal(s, flags):
                stats = _cycle_stats(bars, len(bars) - 1, len(bars) - 1, closed=False)
                open_map[code] = {
                    "code": code,
                    "name": row.get("name") or code,
                    "listed_date": bars[-1]["date"],
                    "new_yesterday": True,
                    **{k: stats[k] for k in stats if k != "path"},
                    "path": stats["path"],
                }
        else:
            open_map[code]["new_yesterday"] = False
            if is_exit_signal(s):
                start = open_map[code]["start_date"]
                idxs = [i for i, b in enumerate(bars) if b["date"] == start]
                a = idxs[0] if idxs else 0
                fin = _cycle_stats(bars, a, len(bars) - 1, closed=True)
                closed.insert(
                    0,
                    {
                        "code": code,
                        "name": open_map[code].get("name") or code,
                        "listed_date": open_map[code].get("listed_date"),
                        **{k: fin[k] for k in fin if k != "path"},
                        "path": fin["path"],
                    },
                )
                del open_map[code]
            else:
                start = open_map[code]["start_date"]
                idxs = [i for i, b in enumerate(bars) if b["date"] == start]
                a = idxs[0] if idxs else 0
                cur = _cycle_stats(bars, a, len(bars) - 1, closed=False)
                open_map[code].update({k: cur[k] for k in cur})
                open_map[code]["name"] = row.get("name") or open_map[code].get("name")
    # still track names that left 买入 but have not met 清仓
    for code, item in list(open_map.items()):
        if code in today_buys:
            continue
        bars = attach_indicators(load_bars(code))
        if not bars:
            continue
        s = _prefix_series(bars, len(bars) - 1)
        if is_exit_signal(s):
            start = item["start_date"]
            idxs = [i for i, b in enumerate(bars) if b["date"] == start]
            a = idxs[0] if idxs else 0
            fin = _cycle_stats(bars, a, len(bars) - 1, closed=True)
            closed.insert(0, {**item, **{k: fin[k] for k in fin}})
            del open_map[code]
        else:
            start = item["start_date"]
            idxs = [i for i, b in enumerate(bars) if b["date"] == start]
            a = idxs[0] if idxs else 0
            cur = _cycle_stats(bars, a, len(bars) - 1, closed=False)
            item.update({k: cur[k] for k in cur})
            item["new_yesterday"] = False
    if asof is None and buy_rows:
        asof = datetime.now().strftime("%Y-%m-%d")
    payload = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "asof": asof,
        "note": "启动=首次列入买入（确认收盘）。结束=RULES §7 减仓/清仓条件。这是事实记录，不是成交指令。",
        "open": list(open_map.values()),
        "closed": closed[:200],
    }
    save_cycles(payload)
    return payload


def cycles_page(buy_rows: list[dict]) -> dict:
    live = update_live_episodes(buy_rows)
    ranking = rank_buy_pool(buy_rows)
    return {
        "fact_note": "这是事实记录",
        "live": live,
        "ranking": ranking,
        "buy_count": len(buy_rows),
    }
