"""Read RULES.md into scanner switches. Markdown is the spec; this file only
turns sentences we already coded into on/off flags. New prose still needs a
human to add a switch — the daily job will flag unimplemented lines."""
from __future__ import annotations

import hashlib
from datetime import datetime

from ..config import DATA_DIR, RULES_PATH
from ..store import read_json, write_json

BIND_PATH = DATA_DIR / "rules_bind.json"

# Phrases the scanner does not execute yet.
UNIMPLEMENTED = (
    ("板块近3日相对大盘", ("相对大盘", "近 3 个交易日", "近3个交易日")),
)


def _text() -> str:
    if not RULES_PATH.exists():
        return ""
    return RULES_PATH.read_text(encoding="utf-8")


def _section(text: str, num: str) -> str:
    key = f"## {num}."
    start = text.find(key)
    if start < 0:
        return ""
    nxt = text.find("\n## ", start + len(key))
    return text[start:] if nxt < 0 else text[start:nxt]


def parse_flags(text: str | None = None) -> dict:
    raw = text if text is not None else _text()
    s3 = _section(raw, "3")
    s4 = _section(raw, "4")
    s5 = _section(raw, "5")
    s6 = _section(raw, "6")
    return {
        "wait_need_low_zone": "低位" in s5 or "中高位" in s5,
        "buy_need_dif_near_min": "最小值" in s6 and "DIF" in s6,
        "buy_need_zero_axis": "零轴" in s6,
        "buy_need_price_low": "低位区" in s6 or "第二段" in s6,
        # §6 写「当日完成金叉」时关掉近两日窗口；§5「近一两日」只管绿柱，不管金叉。
        "cross_within_two_days": "近一两日" in s6,
        "pool_need_pe_positive": "动态市盈" in s3,
        "veto_kdj_overbought": "J" in s4 and "80" in s4 and "K" in s4 and "50" in s4,
        "veto_pullback_60": "60" in s4 and "15%" in s4,
        "veto_ma30_down": "MA30" in s4,
        "wait_need_kdj_band": ("J < 80" in s5 or "J<80" in s5)
        and ("K ≤ 50" in s5 or "K <= 50" in s5 or "K≤50" in s5),
    }


def unimplemented_hits(text: str | None = None) -> list[str]:
    raw = text if text is not None else _text()
    found = []
    for label, keys in UNIMPLEMENTED:
        if any(k in raw for k in keys):
            found.append(label + "：扫描器尚未实现，不挡筛选")
    return found


def refresh_bind(text: str | None = None, ruleset_id: str = "rules") -> dict:
    raw = text if text is not None else _text()
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    prev = read_json(BIND_PATH, {}) if BIND_PATH.exists() else {}
    flags = parse_flags(raw)
    payload = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ruleset": ruleset_id,
        "rules_hash": digest,
        "changed": prev.get("rules_hash") != digest,
        "flags": flags,
        "unimplemented": unimplemented_hits(raw),
        "note": "改对应 RULES 文件后刷新扫描页即可（已支持的开关）。扫描器不会自己改 Python。",
    }
    if ruleset_id == "rules":
        write_json(BIND_PATH, payload)
    return payload


def load_flags() -> dict:
    bind = refresh_bind()
    return bind.get("flags") or parse_flags()
