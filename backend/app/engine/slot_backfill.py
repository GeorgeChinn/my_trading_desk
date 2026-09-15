"""Backfill slot snapshots for configured 数据与设置 times. Never writes data/csv."""
from __future__ import annotations

import time
from datetime import datetime

from ..config import CSV_DIR, CYCLE_CACHE_DIR, SCAN_CACHE_DIR
from ..store import load_sync_status, load_universe, save_sync_status
from .bars import bar_amount, load_bars, ts_code
from .clock import MARKET_CLOSE, normalize_times, parse_hhmm
from .eastmoney import (
    INDEX_SPOT_SECIDS,
    fetch_bars_em,
    fetch_down_pool,
    fetch_fail_pool,
    fetch_index_bars_em,
    fetch_limit_pool,
)
from .pool import is_st_name
from .snapshots import (
    compute_stats,
    empty_index,
    load_snapshot,
    write_events_file,
    write_snapshot_file,
)

END_DEFAULT = "2026-09-14"
DAYS_DEFAULT = 30
MIN_KLT = 30
MIN_LIMIT = 280


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _log(messages: list[str], started: str, **extra) -> None:
    msg = extra.get("message") or (messages[-1] if messages else "")
    cur = {
        "state": "running",
        "step": "snapshots",
        "message": msg,
        "log": messages[-12:],
        "started_at": started,
        "bars_done": extra.get("bars_done", 0),
        "bars_total": extra.get("bars_total", 0),
    }
    prev = load_sync_status() or {}
    cur["bars_done"] = extra.get("bars_done", prev.get("bars_done") or 0)
    cur["bars_total"] = extra.get("bars_total", prev.get("bars_total") or 0)
    save_sync_status(cur)


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


def trading_days_ending(end: str = END_DEFAULT, n: int = DAYS_DEFAULT) -> list[str]:
    bars = load_bars("600519", last_n=80) or load_bars("000001", last_n=80)
    dates = [str(b.get("date") or "")[:10] for b in bars if str(b.get("date") or "")[:10] <= end]
    dates = [d for d in dates if d]
    if end not in dates:
        try:
            day = datetime.strptime(end, "%Y-%m-%d")
        except ValueError:
            day = None
        if day and day.weekday() < 5:
            dates.append(end)
    dates = sorted(set(dates))
    return dates[-n:]


def _limit_pct(code: str, name: str = "") -> float:
    if is_st_name(name):
        return 0.05
    c = ts_code(code)
    if c.startswith(("3", "68")):
        return 0.20
    if c.startswith(("8", "4")):
        return 0.30
    return 0.10


def _compact(code: str, name: str, bar: dict, preclose, mcap=None) -> dict:
    close = _num(bar.get("close"))
    pre = _num(preclose)
    pct = None
    if close and pre:
        pct = round((close / pre - 1.0) * 100.0, 3)
    band = _limit_pct(code, name)
    lu = round(pre * (1.0 + band), 2) if pre else None
    ld = round(pre * (1.0 - band), 2) if pre else None
    amt = _num(bar.get("amount"))
    if amt is None:
        amt = bar_amount(bar)
    return {
        "code": ts_code(code),
        "name": name or code,
        "open": _num(bar.get("open")),
        "high": _num(bar.get("high")),
        "low": _num(bar.get("low")),
        "close": close,
        "pct": pct,
        "volume": _num(bar.get("volume")),
        "amount": amt,
        "turnover": None,
        "float_mcap_yi": _num(mcap),
        "limit_up": lu,
        "limit_down": ld,
        "preclose": pre,
    }


def _write_one(day: str, slot: str, quotes: dict, index: dict, events: dict) -> None:
    existing = load_snapshot(day, slot)
    if existing and int(existing.get("quote_n") or len(existing.get("quotes") or {})) >= 1000:
        return
    stats = compute_stats(
        quotes,
        zt=events.get("limit_up"),
        zb=events.get("fail"),
        dt=events.get("limit_down"),
    )
    payload = {
        "trade_date": day,
        "slot": slot,
        "captured_at": f"{day} {slot}:00",
        "market_closed": parse_hhmm(slot) >= MARKET_CLOSE,
        "quote_n": len(quotes),
        "index": index or empty_index(),
        "stats": stats,
        "quotes": quotes,
        "source": "backfill",
    }
    write_snapshot_file(payload)


def _events_for(day: str) -> dict:
    zt = zb = dt = []
    try:
        zt = fetch_limit_pool(day) or []
    except Exception:
        zt = []
    try:
        zb = fetch_fail_pool(day) or []
    except Exception:
        zb = []
    try:
        dt = fetch_down_pool(day) or []
    except Exception:
        dt = []
    payload = {
        "trade_date": day,
        "slot": "backfill",
        "updated_at": _now(),
        "limit_up": zt,
        "fail": zb,
        "limit_down": dt,
    }
    write_events_file(payload)
    return payload


def _daily_maps(days: set[str], uni: dict) -> dict[str, dict[str, dict]]:
    """date -> code -> compact quote from csv daily bar."""
    out: dict[str, dict[str, dict]] = {d: {} for d in days}
    for path in CSV_DIR.glob("*.csv"):
        code = ts_code(path.stem)
        if not code:
            continue
        meta = uni.get(code) or {}
        name = str(meta.get("name") or "")
        mcap = meta.get("float_mcap_yi")
        bars = load_bars(code, last_n=50)
        if not bars:
            continue
        if not name:
            name = str(bars[-1].get("name") or code)
        for i, row in enumerate(bars):
            d = str(row.get("date") or "")[:10]
            if d not in days:
                continue
            pre = bars[i - 1].get("close") if i >= 1 else None
            out[d][code] = _compact(code, name, row, pre, mcap)
    return out


def _index_min_map(days: set[str], slots: tuple[str, ...]) -> dict[tuple[str, str], dict]:
    packed: dict[tuple[str, str], dict] = {}
    for key, secid_s in INDEX_SPOT_SECIDS:
        try:
            rows = fetch_index_bars_em(secid_s, limit=MIN_LIMIT, klt=MIN_KLT) or []
            time.sleep(0.05)
        except Exception:
            rows = []
        daily = []
        try:
            daily = fetch_index_bars_em(secid_s, limit=80, klt=101) or []
        except Exception:
            daily = []
        for day in days:
            for slot in slots:
                closed = parse_hhmm(slot) >= MARKET_CLOSE
                row = None
                if closed:
                    for rec in daily:
                        if rec.get("date") == day:
                            row = rec
                    if row is None:
                        for rec in reversed(rows):
                            if rec.get("date") == day:
                                row = rec
                                break
                else:
                    for rec in rows:
                        if rec.get("date") == day and rec.get("slot") == slot:
                            row = rec
                            break
                bucket = packed.setdefault((day, slot), empty_index())
                if not row:
                    continue
                close = _num(row.get("close"))
                open_px = _num(row.get("open"))
                pct = None
                if close and open_px:
                    pct = round((close / open_px - 1.0) * 100.0, 3)
                bucket[key] = {
                    "open": open_px,
                    "high": _num(row.get("high")),
                    "low": _num(row.get("low")),
                    "close": close,
                    "pct": pct,
                }
    return packed


def backfill_slot_snapshots(end: str = END_DEFAULT, days: int = DAYS_DEFAULT) -> dict:
    from ..store import load_settings

    started = _now()
    messages: list[str] = []

    def talk(msg: str, **extra) -> None:
        messages.append(msg)
        extra["message"] = msg
        _log(messages, started, **extra)

    times = normalize_times(load_settings().get("schedule_times"))
    day_list = trading_days_ending(end, days)
    dayset = set(day_list)
    talk(f"补到点快照 {day_list[0] if day_list else end} … {day_list[-1] if day_list else end} · {len(day_list)} 个交易日 · 档位 {' / '.join(times)}")
    if not day_list or not times:
        done = {"state": "error", "message": "没有交易日或未选更新时间点", "started_at": started, "finished_at": _now()}
        save_sync_status(done)
        return done

    uni = {ts_code(str(x.get("code") or "")): x for x in load_universe()}
    events_by_day = {}
    for i, day in enumerate(day_list, start=1):
        talk(f"东财涨停/炸板/跌停 {day}（{i}/{len(day_list)}）")
        try:
            events_by_day[day] = _events_for(day)
        except Exception as exc:
            events_by_day[day] = {"limit_up": [], "fail": [], "limit_down": []}
            talk(f"{day} 事件池失败：{exc}")
        time.sleep(0.08)

    talk("按日线 CSV 写 15:00 后已选档（不改 csv）")
    daily = _daily_maps(dayset, uni)
    idx_map = _index_min_map(dayset, times)
    close_slots = [s for s in times if parse_hhmm(s) >= MARKET_CLOSE]
    intra_slots = [s for s in times if parse_hhmm(s) < MARKET_CLOSE]
    written = 0
    for day in day_list:
        quotes = daily.get(day) or {}
        if len(quotes) < 50:
            continue
        for slot in close_slots:
            _write_one(day, slot, quotes, idx_map.get((day, slot)) or empty_index(), events_by_day.get(day) or {})
            written += 1
    talk(f"收盘档已写 {written} 份")

    if intra_slots:
        codes = [ts_code(p.stem) for p in CSV_DIR.glob("*.csv")]
        codes = [c for c in codes if c]
        total = len(codes)
        talk(f"拉 30 分钟 K 线补盘中档 {' / '.join(intra_slots)}，共 {total} 只", bars_done=0, bars_total=total)
        buckets: dict[tuple[str, str], dict[str, dict]] = {(d, s): {} for d in day_list for s in intra_slots}
        ok = fail = 0
        for i, code in enumerate(codes, start=1):
            meta = uni.get(code) or {}
            name = str(meta.get("name") or code)
            mcap = meta.get("float_mcap_yi")
            try:
                rows = fetch_bars_em(code, limit=MIN_LIMIT, klt=MIN_KLT) or []
                time.sleep(0.04)
            except Exception:
                rows = []
                fail += 1
            if not rows:
                fail += 1
            else:
                ok += 1
                by_day_pre = {}
                for rec in rows:
                    d = rec.get("date")
                    if d in dayset:
                        by_day_pre.setdefault(d, rec.get("open"))
                daily_rows = daily
                for rec in rows:
                    d = rec.get("date")
                    stamp = rec.get("slot")
                    if d not in dayset or stamp not in intra_slots:
                        continue
                    pre = None
                    dq = (daily_rows.get(d) or {}).get(code)
                    if dq:
                        pre = dq.get("preclose")
                    if pre is None:
                        pre = rec.get("open")
                    buckets[(d, stamp)][code] = _compact(code, name, rec, pre, mcap)
            if i % 40 == 0 or i == total:
                talk(f"30 分钟 K {i}/{total}，成功 {ok}，空 {fail}", bars_done=i, bars_total=total)
        for day in day_list:
            for slot in intra_slots:
                quotes = buckets.get((day, slot)) or {}
                if len(quotes) < 50:
                    talk(f"{day} {slot} 盘中档不足 {len(quotes)} 只，跳过")
                    continue
                _write_one(day, slot, quotes, idx_map.get((day, slot)) or empty_index(), events_by_day.get(day) or {})
                written += 1
                talk(f"已写 {day} {slot} · {len(quotes)} 只")

    try:
        from .theme_sos import _SNAP

        _SNAP.clear()
    except Exception:
        pass
    for path in (SCAN_CACHE_DIR / "rules5.json", CYCLE_CACHE_DIR / "rules5.json"):
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass

    talk("快照已补。开始 RULES5 扫描（回测缓存已清，打开规则回测会按新档重算）")
    try:
        from ..main import _scan_bundle

        _scan_bundle("rules5")
        talk("RULES5 扫描已更新")
    except Exception as exc:
        talk(f"RULES5 扫描未完成：{exc}")
    try:
        from .cycles import cycles_page
        from .rulesets import get_ruleset

        rs = get_ruleset("rules5")
        if rs:
            cycles_page([], None, rs, warm=True)
            talk("RULES5 回测已按观察/试仓名单预热")
    except Exception as exc:
        talk(f"RULES5 回测预热未启动：{exc}")

    done = {
        "state": "done",
        "step": "snapshots",
        "message": f"到点快照补完 {len(day_list)} 日 × {len(times)} 档。未写 data/csv。RULES5 已重扫。",
        "started_at": started,
        "finished_at": _now(),
        "log": messages[-12:],
        "days": day_list,
        "slots": list(times),
        "files": written,
    }
    save_sync_status(done)
    return done
