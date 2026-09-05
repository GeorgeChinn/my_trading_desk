"""Backfill ~3 years of confirmed daily bars for all A-share names.

Does not change the RULES §3 scan pool. Does not invent bars.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

from ..config import HISTORY_BARS, HISTORY_YEARS
from ..store import load_sync_status, save_sync_status
from .bars import load_bars, merge_bars, save_bars_csv, ts_code
from .clock import expected_close_date
from .eastmoney import fetch_kline_with_source, fetch_spot


def history_start_date(years: int = HISTORY_YEARS) -> str:
    return (datetime.now() - timedelta(days=365 * years + 20)).strftime("%Y-%m-%d")


def _enough(existing: list[dict], need_start: str, expect: str) -> bool:
    if not existing:
        return False
    first = existing[0].get("date") or ""
    last = existing[-1].get("date") or ""
    return bool(first <= need_start and last >= expect and len(existing) >= 60)


def backfill_all_ashare(years: int = HISTORY_YEARS, limit: int = HISTORY_BARS) -> dict:
    need_start = history_start_date(years)
    expect = expected_close_date().isoformat()
    started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    messages: list[str] = []

    def log(msg: str) -> None:
        messages.append(msg)
        save_sync_status(
            {
                **(load_sync_status() or {}),
                "state": "running",
                "step": "history",
                "message": msg,
                "started_at": started,
                "log": messages[-12:],
                "need_start": need_start,
                "history_years": years,
            }
        )

    log(f"开始补全全 A 近 {years} 年确认日线（约 {limit} 根），不改规则扫描池")
    spot = fetch_spot(log=log)
    items = []
    seen = set()
    for rec in spot:
        code = ts_code(str(rec.get("code") or rec.get("symbol") or ""))
        if not code or code in seen:
            continue
        seen.add(code)
        items.append({"code": code, "name": str(rec.get("name") or code)})
    total = len(items)
    log(f"全市场名单 {total} 只，目标起点 {need_start}")

    ok = skip = fail = 0
    last_dates: list[str] = []
    for i, item in enumerate(items, start=1):
        code = item["code"]
        existing = load_bars(code)
        if _enough(existing, need_start, expect):
            skip += 1
        else:
            rows, used = [], ""
            try:
                rows, used = fetch_kline_with_source(code, limit=limit)
                time.sleep(0.05)
            except Exception as exc:
                rows = []
                if fail <= 8 or fail % 50 == 0:
                    log(f"{code} 失败：{exc}")
            if not rows:
                if existing:
                    skip += 1
                else:
                    fail += 1
            else:
                merged = merge_bars(existing, rows)
                if len(merged) < 20:
                    fail += 1
                else:
                    save_bars_csv(code, merged, name=item.get("name"))
                    last_dates.append(merged[-1]["date"])
                    ok += 1
                    item["bar_source"] = used
        if i == 1 or i % 25 == 0 or i == total:
            msg = f"全A近{years}年 {i}/{total} 写入{ok} 已够跳过{skip} 失败{fail}"
            save_sync_status(
                {
                    "state": "running",
                    "step": "history",
                    "message": msg,
                    "started_at": started,
                    "bars_done": i,
                    "bars_total": total,
                    "history_ok": ok,
                    "history_skip": skip,
                    "history_fail": fail,
                    "need_start": need_start,
                    "history_years": years,
                    "log": messages[-12:] + [msg],
                }
            )
            if i % 100 == 0:
                log(msg)

    last_bar = max(last_dates) if last_dates else ""
    done = {
        "state": "done",
        "step": "history",
        "message": f"全A近{years}年完成：写入 {ok}，已够跳过 {skip}，失败 {fail} / {total}。规则扫描池未改。",
        "started_at": started,
        "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "bars_done": total,
        "bars_total": total,
        "history_ok": ok,
        "history_skip": skip,
        "history_fail": fail,
        "need_start": need_start,
        "history_years": years,
        "last_bar": last_bar,
        "log": messages[-12:],
        "pool_size": total,
    }
    save_sync_status(done)
    return done
