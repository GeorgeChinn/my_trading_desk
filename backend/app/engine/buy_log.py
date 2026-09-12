"""买入池是信号源：只有扫描列入买入/试仓的票才开一段回测。"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from ..config import BUY_LOG_DIR, ensure_dirs
from ..store import read_json, write_json
from .bars import attach_indicators, load_bars, ts_code
from .exits import evaluate_exit


def _path(ruleset_id: str):
    ensure_dirs()
    return BUY_LOG_DIR / f"{ruleset_id or 'rules'}.json"


def load_buy_log(ruleset_id: str) -> dict:
    data = read_json(_path(ruleset_id), {})
    if not isinstance(data, dict):
        data = {}
    items = data.get("items")
    if not isinstance(items, list):
        items = []
    data["items"] = items
    data["ruleset"] = ruleset_id
    return data


def record_since() -> str:
    """买入池记录从昨天起。"""
    return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")


def save_buy_log(ruleset_id: str, payload: dict) -> None:
    payload = dict(payload or {})
    payload["ruleset"] = ruleset_id
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    prev = load_buy_log(ruleset_id) if _path(ruleset_id).exists() else {}
    if prev.get("record_mode") == "scan_pool" and prev.get("started_at"):
        started = str(prev.get("started_at") or "")[:10]
    else:
        started = record_since()
    payload["started_at"] = payload.get("started_at") or started
    payload["record_mode"] = "scan_pool"
    payload["updated_at"] = now
    write_json(_path(ruleset_id), payload)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _entry_idx(bars: list[dict], buy_date: str) -> int:
    if not buy_date:
        return 0
    for i, row in enumerate(bars):
        if str(row.get("date") or "") >= str(buy_date)[:10]:
            return i
    return max(0, len(bars) - 1)


def _close_item(item: dict, last: dict, section: str, detail: str) -> None:
    px = float(last.get("close") or 0)
    buy = float(item.get("buy_price") or 0)
    ret = (px / buy - 1.0) if buy else 0.0
    item["closed"] = True
    item["status"] = "已卖出"
    item["sell_date"] = last.get("date")
    item["sell_price"] = round(px, 4)
    item["mark_price"] = round(px, 4)
    item["pnl_pct"] = round(ret * 100, 2)
    item["pnl_per_share"] = round(px - buy, 4)
    item["exit_section"] = section
    item["exit_detail"] = detail
    if section == "获利":
        item["result"] = "获利卖"
    elif section == "止损":
        item["result"] = "止损"
    elif section in ("取关", "高潮走"):
        item["result"] = section
    else:
        item["result"] = "失败离场"
    item["closed_at"] = _now()


def _mark_open(item: dict, last: dict) -> None:
    px = float(last.get("close") or 0)
    buy = float(item.get("buy_price") or 0)
    ret = (px / buy - 1.0) if buy else 0.0
    item["mark_price"] = round(px, 4)
    item["pnl_pct"] = round(ret * 100, 2)
    item["pnl_per_share"] = round(px - buy, 4)
    item["asof_date"] = last.get("date")
    item["result"] = "浮动"


def _eval_item(item: dict, engine: str) -> None:
    code = ts_code(str(item.get("code") or ""))
    if not code or item.get("closed"):
        return
    raw = load_bars(code)
    if not raw:
        return
    buy_date = str(item.get("buy_date") or "")
    if engine == "pullback_restart":
        from .structure_one import evaluate_exit_s1

        zone = {
            "stop": item.get("stop_price"),
            "di_low": item.get("di_low"),
            "di_close": item.get("di_close"),
            "a_pre_close": item.get("a_pre_close"),
            "a_high_close": item.get("a_high_close"),
            "c_vol_avg": item.get("c_vol_avg"),
            "trial_close": item.get("buy_price"),
        }
        hit, section, detail = evaluate_exit_s1(
            raw, {"date": buy_date, "code": code, "buy_date": buy_date}, zone
        )
        last = raw[-1]
    else:
        bars = attach_indicators(raw)
        from .cycles import _prefix_series

        s = _prefix_series(bars, len(bars) - 1)
        hit, section, detail = evaluate_exit(s, _entry_idx(bars, buy_date))
        last = bars[-1]
    if hit:
        _close_item(item, last, section or "失败", detail or "")
    else:
        _mark_open(item, last)


def sync_buy_log(ruleset_id: str, engine: str, rows: list[dict]) -> dict:
    """扫描列入买入/试仓是唯一开仓来源。已开仓的票按对应规则核卖出/取关。"""
    store = load_buy_log(ruleset_id)
    if store.get("record_mode") != "scan_pool":
        store["started_at"] = record_since()
        store["record_mode"] = "scan_pool"
    since = str(store.get("started_at") or record_since())[:10]
    items = list(store.get("items") or [])
    by_code_open: dict[str, dict] = {}
    for item in items:
        if not item.get("closed"):
            by_code_open[ts_code(str(item.get("code") or ""))] = item

    for item in list(by_code_open.values()):
        _eval_item(item, engine)

    by_code_open = {ts_code(str(i.get("code") or "")): i for i in items if not i.get("closed")}

    for row in rows or []:
        st = row.get("status") or row.get("gate")
        if st not in ("买入", "试仓"):
            continue
        code = ts_code(str(row.get("code") or ""))
        if not code or code in by_code_open:
            continue
        facts = row.get("facts") or {}
        buy_date = str(facts.get("date") or "")[:10]
        try:
            buy_price = float(facts.get("close") or row.get("key_price") or 0)
        except (TypeError, ValueError):
            buy_price = 0.0
        if not buy_date or not buy_price:
            continue
        if buy_date < since:
            continue
        item = {
            "id": uuid.uuid4().hex[:12],
            "code": code,
            "name": row.get("name") or code,
            "ruleset": ruleset_id,
            "engine": engine,
            "buy_date": buy_date,
            "buy_price": round(buy_price, 4),
            "sell_date": None,
            "sell_price": None,
            "mark_price": round(buy_price, 4),
            "pnl_pct": 0.0,
            "pnl_per_share": 0.0,
            "status": "进行中",
            "result": "浮动",
            "closed": False,
            "exit_section": None,
            "exit_detail": None,
            "buy_ma20": (facts.get("buy_ma20") or row.get("key_price")),
            "stop_price": facts.get("stop_price") or row.get("stop_price"),
            "di_low": facts.get("di_low"),
            "di_close": facts.get("di_close"),
            "a_pre_close": facts.get("a_pre_close"),
            "a_high_close": facts.get("a_high_close"),
            "c_vol_avg": facts.get("c_vol_avg"),
            "logged_at": _now(),
            "from_pool": True,
        }
        items.append(item)
        by_code_open[code] = item

    store["items"] = items
    store["started_at"] = since
    save_buy_log(ruleset_id, store)
    return public_buy_log(ruleset_id)


def public_buy_log(ruleset_id: str) -> dict:
    store = load_buy_log(ruleset_id)
    items = list(store.get("items") or [])
    items.sort(
        key=lambda x: (
            0 if not x.get("closed") else 1,
            x.get("sell_date") or x.get("buy_date") or "",
            x.get("code") or "",
        ),
        reverse=True,
    )
    started = str(store.get("started_at") or record_since())[:10]
    open_n = sum(1 for x in items if not x.get("closed"))
    closed_n = sum(1 for x in items if x.get("closed"))
    return {
        "ruleset": ruleset_id,
        "items": items,
        "open": open_n,
        "closed": closed_n,
        "total": len(items),
        "updated_at": store.get("updated_at"),
        "started_at": started,
        "note": "买入池只记扫描列入的买入/试仓。日线代理观察不写入。列入日期=扫描列入日。",
    }


def log_as_segments(ruleset_id: str) -> list[dict]:
    payload = public_buy_log(ruleset_id)
    out = []
    seq_by_code: dict[str, int] = {}
    chronological = sorted(
        payload.get("items") or [],
        key=lambda x: (x.get("code") or "", x.get("buy_date") or "", x.get("logged_at") or ""),
    )
    for item in chronological:
        code = item.get("code") or ""
        seq_by_code[code] = seq_by_code.get(code, 0) + 1
        seq = seq_by_code[code]
        closed = bool(item.get("closed"))
        out.append(
            {
                "id": item.get("id") or f"{code}-{item.get('buy_date')}-{seq}",
                "seq": seq,
                "code": code,
                "name": item.get("name") or code,
                "buy_date": item.get("buy_date"),
                "buy_price": item.get("buy_price"),
                "sell_date": item.get("sell_date"),
                "sell_price": item.get("sell_price"),
                "mark_price": item.get("mark_price"),
                "pnl_pct": item.get("pnl_pct"),
                "pnl_per_share": item.get("pnl_per_share"),
                "status": "已结束" if closed else "进行中",
                "result": item.get("result"),
                "closed": closed,
                "win": bool(closed and (item.get("pnl_pct") or 0) > 0),
                "exit_section": item.get("exit_section"),
                "exit_detail": item.get("exit_detail"),
                "from_pool": True,
                "ruleset": ruleset_id,
            }
        )
    return out


def overlay_cycles(segments: list[dict], ruleset_id: str) -> list[dict]:
    """正式买入/试仓记录优先；其余用观察名单上的规则回放。"""
    log_segs = log_as_segments(ruleset_id)
    log_codes = {ts_code(str(s.get("code") or "")) for s in log_segs}
    log_codes.discard("")
    kept = [s for s in segments or [] if ts_code(str(s.get("code") or "")) not in log_codes]
    return list(log_segs) + kept
