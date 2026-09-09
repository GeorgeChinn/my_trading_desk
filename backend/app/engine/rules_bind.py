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
UNIMPLEMENTED = ()


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


def _section_named(text: str, *names: str) -> str:
    raw = text or ""
    for line in raw.splitlines():
        s = line.strip()
        if not s.startswith("## "):
            continue
        title = s[3:]
        if any(n in title for n in names):
            start = raw.find(line)
            nxt = raw.find("\n## ", start + len(line))
            return raw[start:] if nxt < 0 else raw[start:nxt]
    return ""


def parse_flags(text: str | None = None) -> dict:
    raw = text if text is not None else _text()
    s2 = _section(raw, "2") or _section_named(raw, "主线", "漏斗")
    s3 = _section(raw, "3") or _section_named(raw, "池子")
    s4 = _section(raw, "4") or _section_named(raw, "否决")
    s5 = _section(raw, "5") or _section_named(raw, "观察")
    s6 = _section(raw, "6") or _section_named(raw, "买入")
    s_veto = s4 or _section_named(raw, "否决")
    if not s_veto:
        s_veto = raw
    return {
        "wait_need_low_zone": "低位" in (s5 + s6) or "中高位" in s5,
        "buy_need_dif_near_min": ("最低" in s6 or "最小值" in s6) and "DIF" in s6,
        "buy_need_zero_axis": "零轴" in s6,
        "buy_need_price_low": "低位区" in s6 or "第二段" in s6,
        "cross_within_two_days": "近一两日" in s6,
        "pool_need_pe_positive": "动态市盈" in s3 or "PE" in s3,
        "veto_kdj_overbought": "J" in s_veto and "80" in s_veto and "K" in s_veto and "50" in s_veto,
        "veto_pullback_60": "60" in s_veto and "15%" in s_veto,
        "veto_ma30_down": "MA30" in s_veto,
        "wait_need_kdj_band": False,
        "wait_need_sector_vs_market": "相对大盘" in s2 or "近 3 个交易日" in s2 or "近3个交易日" in s2,
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
