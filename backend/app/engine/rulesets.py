"""Discover RULES*.md files. One file = one trading rule. Do not invent engines."""
from __future__ import annotations

import re

from ..config import ROOT

ENGINE_LOW_GOLDEN = "low_golden"
ENGINE_PULLBACK = "pullback_restart"
ENGINE_MA20 = "ma20_swing"
ENGINE_TRS = "test_repair"
ENGINE_UNIMPLEMENTED = "unimplemented"
ENGINE_OK = (ENGINE_LOW_GOLDEN, ENGINE_PULLBACK, ENGINE_MA20, ENGINE_TRS)
LATEST_QUOTE_ENGINES = (ENGINE_LOW_GOLDEN, ENGINE_PULLBACK, ENGINE_TRS)

_TITLE_MARK = re.compile(r"本规则只做\s*\*\*(.+?)\*\*")


def _title(text: str, fallback: str) -> str:
    hit = _TITLE_MARK.search(text or "")
    if hit:
        return hit.group(1).strip()
    for line in (text or "").splitlines():
        raw = line.strip()
        if raw.startswith("# "):
            return raw[2:].strip() or fallback
    return fallback


def _engine(text: str) -> str:
    title = _title(text, "")
    raw = text or ""
    if "Test+Repair" in title or "支撑测试后的修复试仓" in title or "支撑测试后的修复试仓" in raw:
        return ENGINE_TRS
    if "20日线波段" in title or "20日线波段" in raw:
        return ENGINE_MA20
    if "野人哥低吸" in title or "野人哥低吸" in raw:
        return ENGINE_PULLBACK
    if "回调后的重新启动" in title or "回调后的重新启动" in raw:
        return ENGINE_PULLBACK
    if "低位金叉" in title or "低位金叉" in raw:
        return ENGINE_LOW_GOLDEN
    return ENGINE_UNIMPLEMENTED


def uses_latest_quote(engine: str) -> bool:
    return engine in LATEST_QUOTE_ENGINES


def _engine_note(engine: str) -> str:
    if engine == ENGINE_LOW_GOLDEN:
        return "扫描器已执行本结构（低位金叉波段）。判定用数据与设置到点拉到的最新价。买入不是成交指令。"
    if engine == ENGINE_PULLBACK:
        return "扫描器已执行 RULES2 野人哥低吸：周期测压 → 7030 → 资金柱 → C≥8 缩量到地量。判定用数据与设置到点最新价。无分时只标日线代理观察，不得记正式试仓。"
    if engine == ENGINE_MA20:
        return "扫描器已执行 RULES3 野人哥 20日线波段：先强 A（5～12日、≥12%）→ 缩量回踩 C（4～8日）→ 收盘不破 20 日线。判定只用已收盘日线，不吃未收盘价。买入不是下单。"
    if engine == ENGINE_TRS:
        return "扫描器已执行 RULES4 Test+Repair：急杀/回撤 → 放量修复 → 缩量回踩不破 → 再放量离开时试仓。判定用数据与设置最新一次更新（未收盘时收盘=该次最新价）。总闸排除→观察→试仓→持有→卖出。不与金叉/低吸/20日线混池。试仓不是成交指令。"
    return "本规则结构尚未写成扫描器。证据不足，不编造信号。"


def list_rulesets() -> list[dict]:
    items = []
    for path in sorted(ROOT.iterdir(), key=lambda p: p.name.lower()):
        if not path.is_file():
            continue
        if not path.stem.upper().startswith("RULES"):
            continue
        if path.suffix.lower() != ".md":
            continue
        text = path.read_text(encoding="utf-8")
        engine = _engine(text)
        ident = path.stem.lower()
        items.append(
            {
                "id": ident,
                "file": path.name,
                "title": _title(text, ident),
                "engine": engine,
                "engine_ok": engine in ENGINE_OK,
                "engine_note": _engine_note(engine),
                "quote_mode": "最新价" if uses_latest_quote(engine) else "已收盘日线",
                "path": str(path),
                "text": text,
            }
        )
    items.sort(key=lambda x: (0 if x["id"] == "rules" else 1, x["id"]))
    return items


def get_ruleset(ruleset_id: str | None) -> dict | None:
    want = (ruleset_id or "rules").strip().lower() or "rules"
    for item in list_rulesets():
        if item["id"] == want:
            return item
    return None


def public_ruleset(item: dict | None) -> dict | None:
    if not item:
        return None
    return {
        "id": item["id"],
        "file": item["file"],
        "title": item["title"],
        "engine": item["engine"],
        "engine_ok": item["engine_ok"],
        "engine_note": item["engine_note"],
        "quote_mode": "最新价" if uses_latest_quote(item.get("engine") or "") else "已收盘日线",
    }
