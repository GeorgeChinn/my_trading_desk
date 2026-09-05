"""RULES path cycles: 买入日 → 清仓条件日. Live log + history backtest. Not an order."""
from __future__ import annotations

import hashlib
import threading
from datetime import datetime

from ..config import CYCLE_CACHE_DIR, CYCLES_PATH, KDJ_LOW, ensure_dirs
from ..store import read_json, write_json
from .bars import attach_indicators, csv_path_for, load_bars, ts_code
from .rules_bind import parse_flags
from .exits import evaluate_exit
from .scanner import (
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
    """保留给对照。离场请用 exits.this_and_prev_wave，不要拿全历史最后两峰。"""
    from .exits import red_wave_spans

    return [w["peak"] for w in red_wave_spans(hist)]


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


def is_exit_signal(s: dict, entry_idx: int | None = None) -> bool:
    if entry_idx is None:
        return False
    hit, _, _ = evaluate_exit(s, entry_idx)
    return hit


def walk_cycles_s1(bars: list[dict]) -> tuple[list[dict], dict | None]:
    from .structure_one import _choose_kind, _key_zone, evaluate_exit_s1, find_structure, is_buy_s1

    if len(bars) < 30:
        return [], None
    n = len(bars)
    cycles = []
    open_i = None
    open_zone = None
    for i in range(25, n):
        sl = bars[: i + 1]
        if open_i is None:
            if is_buy_s1(sl):
                open_i = i
                st = find_structure(sl)
                zone = _key_zone(sl, st) if st else {}
                kind, px, _n = _choose_kind(zone) if zone else (None, None, None)
                open_zone = {**(zone or {}), "kind": kind, "stop": px, "price": px}
            continue
        hit, section, detail = evaluate_exit_s1(sl, {"date": bars[open_i]["date"]}, open_zone)
        if i > open_i and hit:
            cycles.append(_cycle_stats(bars, open_i, i, exit_section=section, exit_detail=detail))
            open_i = None
            open_zone = None
    live = None
    if open_i is not None:
        live = _cycle_stats(bars, open_i, n - 1, closed=False)
    return cycles, live


def walk_cycles(bars: list[dict], flags: dict | None = None, engine: str = "low_golden") -> tuple[list[dict], dict | None]:
    if engine == "pullback_restart":
        return walk_cycles_s1(bars)
    flags = flags or parse_flags()
    if len(bars) < 50:
        return [], None
    n = len(bars)
    cycles = []
    open_i = None
    for i in range(40, n):
        s = _prefix_series(bars, i)
        if open_i is None:
            if is_buy_signal(s, flags):
                open_i = i
            continue
        hit, section, detail = evaluate_exit(s, open_i)
        if i > open_i and hit:
            cycles.append(_cycle_stats(bars, open_i, i, exit_section=section, exit_detail=detail))
            open_i = None
    live = None
    if open_i is not None:
        live = _cycle_stats(bars, open_i, n - 1, closed=False)
    return cycles, live


def _cycle_stats(
    bars: list[dict],
    a: int,
    b: int,
    closed: bool = True,
    exit_section: str = "",
    exit_detail: str = "",
) -> dict:
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
        if exit_section == "7.1":
            result = f"{result} · 第7条 失败离场"
        elif exit_section == "7.2":
            result = f"{result} · 第7条 波段离场"
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
        "exit_section": exit_section or None,
        "exit_detail": exit_detail or None,
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
        "exit_section": stats.get("exit_section"),
        "exit_detail": stats.get("exit_detail"),
    }


def walk_stock_segments(code: str, name: str, flags: dict, engine: str = "low_golden") -> list[dict]:
    if engine == "pullback_restart":
        bars = load_bars(code, last_n=160)
        closed, live = walk_cycles_s1(bars)
    else:
        bars = attach_indicators(load_bars(code))
        closed, live = walk_cycles(bars, flags, engine=engine)
    out = []
    for i, item in enumerate(closed, start=1):
        out.append(_segment_row(code, name, item, i))
    if live:
        out.append(_segment_row(code, name, live, len(closed) + 1))
    return out


def _cache_path(ruleset_id: str):
    ensure_dirs()
    return CYCLE_CACHE_DIR / f"{ruleset_id}.json"


def _rules_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def _last_date(code: str) -> str:
    path = csv_path_for(code)
    if not path.exists():
        return ""
    with path.open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(max(0, size - 480))
        tail = handle.read().decode("utf-8", errors="ignore").strip().splitlines()
    if not tail:
        return ""
    parts = tail[-1].split(",")
    if len(parts) > 1 and len(parts[1]) >= 8:
        return parts[1][:10]
    bars = load_bars(code)
    return str(bars[-1]["date"]) if bars else ""


def cached_stock_segments(code: str, name: str, flags: dict, ruleset_id: str, rules_hash: str) -> list[dict]:
    path = _cache_path(ruleset_id)
    store = read_json(path, {}) if path.exists() else {}
    codes = store.get("codes") if isinstance(store.get("codes"), dict) else {}
    last = _last_date(code)
    hit = codes.get(code) or {}
    if store.get("rules_hash") == rules_hash and hit.get("last_date") == last and isinstance(hit.get("segments"), list):
        return [{**dict(seg), "name": name} for seg in hit["segments"]]
    return walk_stock_segments(code, name, flags)


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
    wins = sum(1 for s in closed if s.get("win"))
    losses = sum(1 for s in closed if not s.get("win") and (s.get("pnl_pct") or 0) < 0)
    flat = sum(1 for s in closed if (s.get("pnl_pct") or 0) == 0)
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


def _sort_segments(rows: list[dict], sort: str, order: str) -> list[dict]:
    rev = (order or "desc").lower() != "asc"
    key = sort or "buy_date"
    numeric = key in ("pnl_pct", "pnl_per_share", "buy_price", "sell_price", "seq", "bars")

    def sk(item: dict):
        val = item.get(key)
        if numeric:
            try:
                return float(val)
            except (TypeError, ValueError):
                return 0.0
        return val or ""

    if key == "status":
        rows = sorted(rows, key=lambda s: (1 if s.get("closed") else 0, s.get("buy_date") or ""), reverse=rev)
        return rows
    return sorted(rows, key=sk, reverse=rev)


_warm_lock = threading.Lock()
_warming: set[str] = set()


def _asof() -> str:
    from ..store import load_settings

    return str((load_settings() or {}).get("last_trade_date") or "")


def _codes_to_segments(codes: dict) -> list[dict]:
    segments: list[dict] = []
    for code, hit in (codes or {}).items():
        segs = hit.get("segments") if isinstance(hit, dict) else None
        if not isinstance(segs, list):
            continue
        for seg in segs:
            row = dict(seg)
            row.setdefault("code", code)
            row.setdefault("name", row.get("name") or code)
            segments.append(row)
    return segments


def _paginate(segments: list[dict], tab: str, q: str, sort: str, order: str, page: int, page_size: int, warm: bool) -> tuple[list[dict], int, int, int]:
    query = (q or "").strip()
    filtered = []
    for s in segments:
        if tab == "open" and s.get("closed"):
            continue
        if tab == "done" and not s.get("closed"):
            continue
        if query and query not in (s.get("code") or "") and query not in (s.get("name") or ""):
            continue
        filtered.append(s)
    if sort in ("", "default"):
        open_rows = [s for s in filtered if not s.get("closed")]
        closed_rows = [s for s in filtered if s.get("closed")]
        open_rows.sort(key=lambda s: (s.get("buy_date") or "", s.get("code") or ""), reverse=True)
        closed_rows.sort(key=lambda s: (s.get("sell_date") or "", s.get("code") or ""), reverse=True)
        filtered = open_rows + closed_rows
    else:
        filtered = _sort_segments(filtered, sort, order)
    total = len(filtered)
    size = max(1, min(int(page_size or 40), 200))
    pages = max(1, (total + size - 1) // size)
    cur = max(1, min(int(page or 1), pages))
    start = (cur - 1) * size
    page_rows = filtered if warm else filtered[start : start + size]
    return page_rows, total, size, (1 if warm else cur), (1 if warm else pages)


def _warm_cycles(scan_uni: list[dict], flags: dict, engine: str, rules_hash: str, ruleset_id: str) -> None:
    path = _cache_path(ruleset_id)
    store = read_json(path, {}) if path.exists() else {}
    codes = store.get("codes") if isinstance(store.get("codes"), dict) else {}
    if store.get("rules_hash") != rules_hash:
        codes = {}
    asof = _asof()
    total = len(scan_uni)
    for i, meta in enumerate(scan_uni, start=1):
        code = ts_code(str(meta.get("code") or ""))
        name = meta.get("name") or code
        if not code:
            continue
        segs = walk_stock_segments(code, name, flags, engine=engine)
        codes[code] = {"last_date": _last_date(code), "segments": segs}
        if i == 1 or i % 20 == 0 or i == total:
            write_json(
                path,
                {
                    "rules_hash": rules_hash,
                    "asof": asof,
                    "warming": i < total,
                    "done": i,
                    "total": total,
                    "codes": codes,
                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                },
            )
    write_json(
        path,
        {
            "rules_hash": rules_hash,
            "asof": asof,
            "warming": False,
            "done": total,
            "total": total,
            "codes": codes,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
    )


def _cycles_page_s1(
    flags: dict,
    ruleset: dict,
    pub: dict | None,
    rules_hash: str,
    ruleset_id: str,
    note: str,
    tab: str,
    q: str,
    sort: str,
    order: str,
    page: int,
    page_size: int,
    warm: bool,
) -> dict:
    from .structure_one import list_s1_cycle_universe

    path = _cache_path(ruleset_id)
    store = read_json(path, {}) if path.exists() else {}
    if not isinstance(store, dict):
        store = {}
    asof = _asof()
    hash_ok = store.get("rules_hash") == rules_hash
    asof_ok = (not asof) or store.get("asof") == asof
    codes = store.get("codes") if isinstance(store.get("codes"), dict) else {}
    with _warm_lock:
        in_flight = ruleset_id in _warming
    warming = bool(store.get("warming")) or in_flight
    complete = bool(hash_ok and asof_ok and codes and not warming)

    if warm and not complete:
        uni = list_s1_cycle_universe()
        _warm_cycles(uni, flags, "pullback_restart", rules_hash, ruleset_id)
        store = read_json(path, {}) if path.exists() else {}
        codes = store.get("codes") if isinstance(store.get("codes"), dict) else {}
        complete = True
        warming = False

    if not complete and not in_flight:
        def boot():
            try:
                uni = list_s1_cycle_universe()
                _warm_cycles(uni, flags, "pullback_restart", rules_hash, ruleset_id)
            finally:
                with _warm_lock:
                    _warming.discard(ruleset_id)

        with _warm_lock:
            if ruleset_id not in _warming:
                _warming.add(ruleset_id)
                threading.Thread(target=boot, daemon=True, name=f"cycles-{ruleset_id}").start()
        warming = True

    segments = _codes_to_segments(codes)
    page_rows, total, size, cur, pages = _paginate(segments, tab, q, sort, order, page, page_size, warm)
    done = int(store.get("done") or 0)
    all_n = int(store.get("total") or 0)
    payload = {
        "fact_note": "这是事实记录",
        "note": (
            f"RULES2 轨迹回放中 {done}/{all_n or '?'}，完成后自动刷新。买入不是成交指令。"
            if warming
            else note
        ),
        "ruleset": pub,
        "segments": page_rows,
        "summary": summarize_segments(segments),
        "page": cur,
        "page_size": size,
        "pages": pages,
        "filtered": total,
        "cached": bool(codes) and not warming,
        "warming": warming,
        "warm_done": done,
        "warm_total": all_n,
        "updated_at": store.get("updated_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    save_cycles(payload, ruleset_id)
    return payload


def cycles_page(
    universe: list[dict],
    flags: dict | None = None,
    ruleset: dict | None = None,
    tab: str = "all",
    q: str = "",
    sort: str = "buy_date",
    order: str = "desc",
    page: int = 1,
    page_size: int = 40,
    warm: bool = False,
) -> dict:
    """One segment = 买入条件日 → 卖出条件日. Cache by code + rules hash + last bar date."""
    from .rulesets import ENGINE_LOW_GOLDEN, public_ruleset

    flags = flags or parse_flags()
    pub = public_ruleset(ruleset) if ruleset else None
    engine = (ruleset or {}).get("engine") or ENGINE_LOW_GOLDEN
    ruleset_id = (ruleset or {}).get("id") or "rules"
    rules_hash = _rules_hash((ruleset or {}).get("text") or "")
    note = "一段轨迹 = 路径到达买入的确认收盘 → 卖出条件日。买入价/卖出价用当日收盘。这是事实记录，不是成交指令。"
    if engine not in ("low_golden", "pullback_restart"):
        payload = {
            "fact_note": "这是事实记录",
            "note": (ruleset or {}).get("engine_note") or note,
            "ruleset": pub,
            "segments": [],
            "summary": _empty_summary(),
            "page": 1,
            "page_size": page_size,
            "pages": 1,
            "cached": False,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        save_cycles(payload, ruleset_id)
        return payload
    if engine == "pullback_restart":
        return _cycles_page_s1(
            flags,
            ruleset or {},
            pub,
            rules_hash,
            ruleset_id,
            note,
            tab,
            q,
            sort,
            order,
            page,
            page_size,
            warm,
        )
    segments: list[dict] = []
    scan_uni = universe
    pe_map = {ts_code(str(m.get("code") or "")): m.get("pe") for m in universe}
    path = _cache_path(ruleset_id)
    store = read_json(path, {}) if path.exists() else {}
    if not isinstance(store, dict):
        store = {}
    codes = store.get("codes") if isinstance(store.get("codes"), dict) else {}
    hash_ok = store.get("rules_hash") == rules_hash
    dirty = not hash_ok
    if not hash_ok:
        codes = {}
    for meta in scan_uni:
        code = ts_code(str(meta.get("code") or ""))
        name = meta.get("name") or code
        if not code:
            continue
        last = _last_date(code)
        hit = codes.get(code) or {}
        if hash_ok and hit.get("last_date") == last and isinstance(hit.get("segments"), list):
            segs = [{**dict(seg), "name": name} for seg in hit["segments"]]
        else:
            segs = walk_stock_segments(code, name, flags, engine=engine)
            codes[code] = {"last_date": last, "segments": segs}
            dirty = True
        for seg in segs:
            if seg.get("pe") is None:
                seg["pe"] = pe_map.get(code)
        segments.extend(segs)
    if dirty:
        write_json(
            path,
            {
                "rules_hash": rules_hash,
                "codes": codes,
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            },
        )
    query = (q or "").strip()
    filtered = []
    for s in segments:
        if tab == "open" and s.get("closed"):
            continue
        if tab == "done" and not s.get("closed"):
            continue
        if query and query not in (s.get("code") or "") and query not in (s.get("name") or ""):
            continue
        filtered.append(s)
    if sort in ("", "default"):
        open_rows = [s for s in filtered if not s.get("closed")]
        closed_rows = [s for s in filtered if s.get("closed")]
        open_rows.sort(key=lambda s: (s.get("buy_date") or "", s.get("code") or ""), reverse=True)
        closed_rows.sort(key=lambda s: (s.get("sell_date") or "", s.get("code") or ""), reverse=True)
        filtered = open_rows + closed_rows
    else:
        filtered = _sort_segments(filtered, sort, order)
    total = len(filtered)
    size = max(1, min(int(page_size or 40), 200))
    pages = max(1, (total + size - 1) // size)
    cur = max(1, min(int(page or 1), pages))
    start = (cur - 1) * size
    page_rows = filtered if warm else filtered[start : start + size]
    payload = {
        "fact_note": "这是事实记录",
        "note": note,
        "ruleset": pub,
        "segments": page_rows,
        "summary": summarize_segments(segments),
        "page": 1 if warm else cur,
        "page_size": size,
        "pages": 1 if warm else pages,
        "filtered": total,
        "cached": True,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    save_cycles(payload, ruleset_id)
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
