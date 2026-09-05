"""Discover RULES*.md files. One file = one trading rule. Do not invent engines."""
from __future__ import annotations

import re

from ..config import ROOT

ENGINE_LOW_GOLDEN = "low_golden"
ENGINE_PULLBACK = "pullback_restart"
ENGINE_UNIMPLEMENTED = "unimplemented"
ENGINE_OK = (ENGINE_LOW_GOLDEN, ENGINE_PULLBACK)

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
    if "回调后的重新启动" in title:
        return ENGINE_PULLBACK
    if "低位金叉" in title:
        return ENGINE_LOW_GOLDEN
    raw = text or ""
    if "回调后的重新启动" in raw:
        return ENGINE_PULLBACK
    if "低位金叉" in raw:
        return ENGINE_LOW_GOLDEN
    return ENGINE_UNIMPLEMENTED


def _engine_note(engine: str) -> str:
    if engine == ENGINE_LOW_GOLDEN:
        return "扫描器已执行本结构（低位金叉波段）。买入不是成交指令。"
    if engine == ENGINE_PULLBACK:
        return "扫描器已执行本结构（回调后的重新启动）。买入不是成交指令。"
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
    }
