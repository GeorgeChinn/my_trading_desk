from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from ..config import BUILTIN_CONDITIONS
from .bars import attach_indicators, load_bars, ts_code
from .scanner import FACT_NOTE, classify_stock

CONDITION_MAP = {item["id"]: item["text"] for item in BUILTIN_CONDITIONS}


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def condition_text(condition_id: str, custom: str | None = None) -> str:
    if custom:
        return custom.strip()
    return CONDITION_MAP.get(condition_id, condition_id)


def _gap_pct(close: Optional[float], ma: Optional[float]) -> Optional[float]:
    if close is None or ma in (None, 0):
        return None
    return (close - ma) / ma * 100.0


def evaluate_condition(code: str, condition_id: str, custom_text: str | None = None) -> dict:
    bars = attach_indicators(load_bars(code))
    text = condition_text(condition_id, custom_text)
    result = {
        "triggered": False,
        "evidence": "证据不足",
        "condition_id": condition_id,
        "condition_text": text,
        "fact_note": FACT_NOTE,
        "snapshot": None,
        "latest": None,
    }
    if len(bars) < 6:
        return result

    last, prev = bars[-1], bars[-2]
    latest = {
        "date": last["date"],
        "close": last["close"],
        "ma5": last.get("ma5"),
        "hist": last.get("hist"),
        "dif": last.get("dif"),
        "dea": last.get("dea"),
        "ma5_gap_pct": _gap_pct(last["close"], last.get("ma5")),
        "note": "最新CSV日线仅供对照，不改写已确认收盘事实",
    }
    result["latest"] = latest

    if condition_id == "ma5_reclaim":
        if last.get("ma5") is None or prev.get("ma5") is None:
            return result
        triggered = prev["close"] < prev["ma5"] and last["close"] >= last["ma5"]
        snapshot = {
            "date": last["date"],
            "close": last["close"],
            "ma5": last["ma5"],
            "ma5_gap_pct": _gap_pct(last["close"], last["ma5"]),
            "prev_date": prev["date"],
            "prev_close": prev["close"],
            "prev_ma5": prev["ma5"],
        }
        result.update(
            {
                "triggered": bool(triggered),
                "evidence": "确认收盘站上5日均线" if triggered else "确认收盘未重新站上5日均线",
                "snapshot": snapshot,
            }
        )
        return result

    if condition_id == "macd_green_shrink":
        h0, h1 = prev.get("hist"), last.get("hist")
        window = [row.get("hist") for row in bars[-21:-1] if row.get("hist") is not None]
        if h0 is None or h1 is None:
            return result
        green = h1 < 0
        shrink = h0 < 0 and abs(h1) < abs(h0)
        not_new_low = bool(window) and h1 > min(window)
        triggered = bool(green and shrink and not_new_low)
        snapshot = {
            "date": last["date"],
            "close": last["close"],
            "hist": h1,
            "prev_hist": h0,
            "dif": last.get("dif"),
            "dea": last.get("dea"),
            "ma5": last.get("ma5"),
            "ma5_gap_pct": _gap_pct(last["close"], last.get("ma5")),
        }
        result.update(
            {
                "triggered": triggered,
                "evidence": "绿柱缩短且不创新低" if triggered else "MACD绿柱条件未齐",
                "snapshot": snapshot,
            }
        )
        return result

    # Custom text is recorded, not auto-matched. No invented indicator.
    result["evidence"] = "自定义条件需人工核对，系统不发明新指标"
    result["snapshot"] = {
        "date": last["date"],
        "close": last["close"],
        "ma5": last.get("ma5"),
        "ma5_gap_pct": _gap_pct(last["close"], last.get("ma5")),
        "hist": last.get("hist"),
    }
    return result


def refresh_watch(item: dict, universe_by_code: dict, settings: dict) -> dict:
    code = ts_code(item["code"])
    eval_result = evaluate_condition(code, item.get("condition_id") or "", item.get("condition_text"))
    item["name"] = item.get("name") or (universe_by_code.get(code) or {}).get("name") or code
    item["last_eval_date"] = (eval_result.get("latest") or {}).get("date")
    item["latest"] = eval_result.get("latest")
    item["evidence"] = eval_result.get("evidence")
    item["fact_note"] = FACT_NOTE

    if eval_result["triggered"]:
        snap_date = (eval_result.get("snapshot") or {}).get("date")
        frozen = item.get("trigger") or {}
        # Freeze the first confirmed snapshot; later bars do not rewrite it.
        if not frozen.get("frozen"):
            item["trigger"] = {
                "frozen": True,
                "recorded_at": _now(),
                "condition_text": eval_result["condition_text"],
                "snapshot": eval_result["snapshot"],
                "fact_note": FACT_NOTE,
            }
            item["viewed"] = False
        elif frozen.get("snapshot", {}).get("date") != snap_date:
            # New confirmed day still matching: keep old freeze, attach latest separately.
            item["trigger"]["still_true_on"] = snap_date
        item["triggered"] = True
    else:
        item["triggered"] = bool(item.get("trigger"))
    return item


def queue_counts(watches: list[dict]) -> dict:
    pending_view = sum(1 for item in watches if not item.get("viewed", True))
    pending_judge = sum(
        1
        for item in watches
        if item.get("triggered") and not item.get("judgement")
    )
    monitor = sum(1 for item in watches if item.get("monitoring", True))
    triggered = sum(1 for item in watches if item.get("triggered"))
    return {
        "pending_view": pending_view,
        "pending_judge": pending_judge,
        "monitor": monitor,
        "triggered": triggered,
    }


def scan_row_for_watch(item: dict, universe_by_code: dict, settings: dict, trades: list[dict]) -> dict | None:
    meta = universe_by_code.get(ts_code(item["code"]))
    if not meta:
        meta = {"code": item["code"], "name": item.get("name")}
    return classify_stock(meta, settings, trades)
