"""RULES path cycles: 买入日 → 清仓条件日. Live log + history backtest. Not an order."""
from __future__ import annotations

import hashlib
import threading
from datetime import datetime

from ..config import CYCLE_CACHE_DIR, CYCLES_PATH, POOL_AMOUNT_YI, POOL_MIN_PRICE, ensure_dirs
from ..store import read_json, write_json
from .bars import attach_indicators, bar_amount, csv_path_for, load_bars, ts_code
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
    """与规则扫描同一套买入：池子外的否决 + 当日金叉 + 零轴 + 低位。不含观察用的绿柱/KDJ附加项。"""
    last = s["last"]
    if last.get("dif") is None or last.get("hist") is None or last.get("k") is None:
        return False
    close = last.get("close")
    if close is None or close < POOL_MIN_PRICE:
        return False
    from .pool import is_st_name

    if is_st_name(str(last.get("name") or "")):
        return False
    amt = bar_amount(last)
    if amt is None or amt < POOL_AMOUNT_YI * 100_000_000.0:
        return False
    from .scanner import detect_limit_streak

    if detect_limit_streak(s.get("bars") or [], last.get("code") or "") >= 2:
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
    buy_cross, _, cross_idx = recent_dif_golden_cross(s["dif"], s["dea"], within_two_days=False)
    if not buy_cross:
        return False
    near_low, _ = nearer_to_window_low(s["dif"], last.get("dif"), "DIF")
    px_low, _ = nearer_to_window_low(s["c"], last.get("close"), "最新价")
    zero_ok, _ = zero_axis_golden(s["dif"], s["dea"], cross_idx)
    return bool(near_low is True and px_low is True and zero_ok is True)


def is_exit_signal(s: dict, entry_idx: int | None = None) -> bool:
    if entry_idx is None:
        return False
    hit, _, _ = evaluate_exit(s, entry_idx)
    return hit


def walk_cycles_s1(bars: list[dict], ctx: dict | None = None) -> tuple[list[dict], dict | None]:
    from .structure_one import evaluate_exit_s1, find_structure, is_buy_s1

    ctx = dict(ctx or {})
    if len(bars) < 30:
        return [], None
    n = len(bars)
    cycles = []
    open_i = None
    open_zone = None
    for i in range(25, n):
        sl = bars[: i + 1]
        if open_i is None:
            if is_buy_s1(sl, ctx):
                open_i = i
                st = find_structure(sl, ctx.get("hs"))
                zone = {}
                if st:
                    a, fund = st["a"], st["fund"]
                    zone = {
                        "fund_low": fund["low"],
                        "a_pre_close": a.get("pre_c"),
                        "a_high_close": a["hi_c"],
                        "a_vol_avg": a["a_avg"],
                    }
                open_zone = zone
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


def walk_cycles(
    bars: list[dict],
    flags: dict | None = None,
    engine: str = "low_golden",
    ctx: dict | None = None,
) -> tuple[list[dict], dict | None]:
    if engine == "pullback_restart":
        return walk_cycles_s1(bars, ctx)
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
        exit_label = {
            "止损": "止损",
            "失败": "失败离场",
            "获利": "获利卖",
            "7.1": "止损",
            "7.1b": "连续跌破均线",
            "7.2": "高潮卖",
            "高潮卖": "高潮卖",
            "取关": "取关",
            "高潮走": "高潮走",
        }.get(exit_section)
        if exit_label:
            result = f"{result} · {exit_label}"
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


def walk_stock_segments(
    code: str,
    name: str,
    flags: dict,
    engine: str = "low_golden",
    last_n: int | None = None,
    ctx: dict | None = None,
) -> list[dict]:
    ctx = dict(ctx or {})
    ctx.setdefault("code", ts_code(code))
    ctx.setdefault("name", name)
    if engine == "pullback_restart":
        n = 160 if last_n is None else last_n
        bars = load_bars(code) if n <= 0 else load_bars(code, last_n=n)
        closed, live = walk_cycles_s1(bars, ctx)
    else:
        bars = attach_indicators(load_bars(code))
        closed, live = walk_cycles(bars, flags, engine=engine, ctx=ctx)
    out = []
    for i, item in enumerate(closed, start=1):
        out.append(_segment_row(code, name, item, i))
    if live:
        out.append(_segment_row(code, name, live, len(closed) + 1))
    return out


def _cache_path(ruleset_id: str):
    ensure_dirs()
    return CYCLE_CACHE_DIR / f"{ruleset_id}.json"


def _engine_fingerprint() -> str:
    """改 structure_one / cycles 后缓存自动作废，不必 SSH 删文件。"""
    from pathlib import Path

    here = Path(__file__).resolve().parent
    parts = []
    for name in ("cycles.py", "structure_one.py", "scanner.py", "exits.py", "boards.py"):
        path = here / name
        if path.exists():
            parts.append(path.read_bytes())
    return hashlib.sha256(b"".join(parts)).hexdigest()[:16]


def _rules_hash(text: str) -> str:
    raw = f"{text or ''}|{_engine_fingerprint()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


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


def cached_stock_segments(
    code: str,
    name: str,
    flags: dict,
    ruleset_id: str,
    rules_hash: str,
    engine: str = "low_golden",
) -> list[dict]:
    path = _cache_path(ruleset_id)
    store = read_json(path, {}) if path.exists() else {}
    codes = store.get("codes") if isinstance(store.get("codes"), dict) else {}
    last = _last_date(code)
    hit = codes.get(code) or {}
    if store.get("rules_hash") == rules_hash and hit.get("last_date") == last and isinstance(hit.get("segments"), list):
        return [{**dict(seg), "name": name} for seg in hit["segments"]]
    return walk_stock_segments(code, name, flags, engine=engine)


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


def _warm_cycles(scan_uni: list[dict], flags: dict, engine: str, rules_hash: str, ruleset_id: str, ctx: dict | None = None) -> None:
    path = _cache_path(ruleset_id)
    store = read_json(path, {}) if path.exists() else {}
    codes = store.get("codes") if isinstance(store.get("codes"), dict) else {}
    if store.get("rules_hash") != rules_hash:
        codes = {}
    asof = _asof()
    total = len(scan_uni)
    base_ctx = dict(ctx or {})
    for i, meta in enumerate(scan_uni, start=1):
        code = ts_code(str(meta.get("code") or ""))
        name = meta.get("name") or code
        if not code:
            continue
        item_ctx = dict(base_ctx)
        item_ctx["code"] = code
        item_ctx["name"] = name
        if meta.get("industry"):
            item_ctx["industry"] = meta.get("industry")
        if meta.get("pe") is not None:
            item_ctx["pe"] = meta.get("pe")
        if meta.get("float_mcap_yi") is not None:
            item_ctx["float_mcap_yi"] = meta.get("float_mcap_yi")
        segs = walk_stock_segments(code, name, flags, engine=engine, ctx=item_ctx)
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
    from .structure_one import _load_industry_map, list_s1_cycle_universe, need_mainline
    from ..store import load_quotes

    want_ml = need_mainline()
    daily = None
    if want_ml:
        from .boards import build_board_daily

        daily = build_board_daily()
    quotes = load_quotes()
    imap = _load_industry_map()
    warm_ctx = {"board_daily": daily, "require_mainline": want_ml}

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

    def _uni():
        rows = list_s1_cycle_universe()
        for item in rows:
            code = ts_code(str(item.get("code") or ""))
            q = quotes.get(code) or {}
            item["industry"] = item.get("industry") or imap.get(code)
            if item.get("pe") is None:
                item["pe"] = q.get("pe")
            if item.get("float_mcap_yi") is None:
                item["float_mcap_yi"] = q.get("float_mcap_yi")
        return rows

    if warm and not complete:
        uni = _uni()
        _warm_cycles(uni, flags, "pullback_restart", rules_hash, ruleset_id, ctx=warm_ctx)
        store = read_json(path, {}) if path.exists() else {}
        codes = store.get("codes") if isinstance(store.get("codes"), dict) else {}
        complete = True
        warming = False

    if not complete and not in_flight:
        def boot():
            try:
                uni = _uni()
                _warm_cycles(uni, flags, "pullback_restart", rules_hash, ruleset_id, ctx=warm_ctx)
            finally:
                with _warm_lock:
                    _warming.discard(ruleset_id)

        with _warm_lock:
            if ruleset_id not in _warming:
                _warming.add(ruleset_id)
                threading.Thread(target=boot, daemon=True, name=f"cycles-{ruleset_id}").start()
        warming = True

    segments = _codes_to_segments(codes)
    from .buy_log import overlay_cycles

    segments = overlay_cycles(segments, ruleset_id)
    page_rows, total, size, cur, pages = _paginate(segments, tab, q, sort, order, page, page_size, warm)
    done = int(store.get("done") or 0)
    all_n = int(store.get("total") or 0)
    payload = {
        "fact_note": "这是事实记录",
        "note": (
            f"RULES2 规则回测中 {done}/{all_n or '?'}，完成后自动刷新。买入不是成交指令。"
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
    note = "规则回测只含规则扫描列入过买入/试仓池的票。列入日期=扫描列入日。记录从昨天起。买入/试仓不是成交指令。"
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
    from .buy_log import overlay_cycles

    segments = overlay_cycles([], ruleset_id)
    page_rows, total, size, cur, pages = _paginate(segments, tab, q, sort, order, page, page_size, False)
    payload = {
        "fact_note": "这是事实记录",
        "note": note,
        "ruleset": pub,
        "segments": page_rows,
        "summary": summarize_segments(segments),
        "page": cur,
        "page_size": size,
        "pages": pages,
        "filtered": total,
        "cached": True,
        "warming": False,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    save_cycles(payload, ruleset_id)
    return payload


def _stamp_segments(segments: list[dict], rs: dict) -> list[dict]:
    rid = (rs or {}).get("id") or "rules"
    eng = (rs or {}).get("engine") or ""
    out = []
    for seg in segments or []:
        if not isinstance(seg, dict):
            continue
        if seg.get("ruleset") and seg["ruleset"] != rid:
            continue
        seg["ruleset"] = rid
        seg["engine"] = eng
        out.append(seg)
    return out


def cycles_for_stock(code: str, name: str, ruleset: dict | None) -> dict:
    from .rulesets import public_ruleset

    pub = public_ruleset(ruleset) if ruleset else None
    engine = (ruleset or {}).get("engine") or "low_golden"
    note = "规则回测只含扫描列入过买入/试仓池的段。列入日期=扫描列入日。"
    if engine not in ("low_golden", "pullback_restart"):
        return {
            "code": ts_code(code),
            "name": name,
            "ruleset": pub,
            "segments": [],
            "summary": _empty_summary(),
            "note": (ruleset or {}).get("engine_note") or "本规则尚未写成扫描器，没有轨迹。",
            "fact_note": "这是事实记录",
        }
    from .buy_log import log_as_segments

    rid = (ruleset or {}).get("id") or "rules"
    segs = [s for s in log_as_segments(rid) if ts_code(str(s.get("code") or "")) == ts_code(code)]
    segs = _stamp_segments(segs, ruleset or {})
    return {
        "code": ts_code(code),
        "name": name,
        "ruleset": pub,
        "segments": segs,
        "summary": summarize_segments(segs),
        "note": note,
        "fact_note": "这是事实记录",
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def cycles_for_pool(items: list[dict], ruleset: dict | None) -> dict:
    from .rulesets import public_ruleset

    pub = public_ruleset(ruleset) if ruleset else None
    engine = (ruleset or {}).get("engine") or "low_golden"
    note = "只回放这些代码里、扫描列入过买入/试仓池的段。"
    if engine not in ("low_golden", "pullback_restart"):
        return {
            "ruleset": pub,
            "segments": [],
            "summary": _empty_summary(),
            "note": (ruleset or {}).get("engine_note") or "本规则尚未写成扫描器，没有轨迹。",
            "fact_note": "这是事实记录",
            "pool_count": len(items or []),
        }
    from .buy_log import log_as_segments

    want = {ts_code(str((item or {}).get("code") or "")) for item in items or []}
    want.discard("")
    rid = (ruleset or {}).get("id") or "rules"
    segments = [s for s in log_as_segments(rid) if ts_code(str(s.get("code") or "")) in want]
    segments = _stamp_segments(segments, ruleset or {})
    closed = [s for s in segments if s.get("closed")]
    opened = [s for s in segments if not s.get("closed")]
    closed.sort(key=lambda s: (s.get("sell_date") or "", s.get("code") or ""), reverse=True)
    opened.sort(key=lambda s: (s.get("buy_date") or "", s.get("code") or ""), reverse=True)
    ordered = closed + opened
    return {
        "ruleset": pub,
        "segments": ordered,
        "summary": summarize_segments(segments),
        "note": note,
        "fact_note": "这是事实记录",
        "pool_count": len(items or []),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


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
