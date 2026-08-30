from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .config import JOURNAL_DIR, ensure_dirs

TEMPLATE = """# 复盘 {date}

## 环境
- 大盘：{market}
- 情绪：{mood}

## AI / 工作台给了什么
- 主线板块：{theme}
- 候选（池子 / 路径 / 状态）：{candidates}

## 我做了什么
- 开仓 / 等待 / 放弃：{action}
- 路径是否匹配：{path_fit}

## 规则
- 命中了哪条：{hit}
- 打破了哪条：{broke}
- 准备改哪一条（最多一条）：{change}

## 验收问题
1. 方向对不对：{q1}
2. 路径是不是我的：{q2}
"""


def _journal_path(day: str) -> Path:
    return JOURNAL_DIR / f"{day}.md"


def list_journals() -> list[dict]:
    ensure_dirs()
    items = []
    for path in sorted(JOURNAL_DIR.glob("*.md"), reverse=True):
        if path.name.upper() == "TEMPLATE.MD":
            continue
        items.append(
            {
                "date": path.stem,
                "file": path.name,
                "bytes": path.stat().st_size,
            }
        )
    return items


def read_journal(day: str) -> dict:
    path = _journal_path(day)
    if not path.exists():
        return {"date": day, "exists": False, "markdown": TEMPLATE.format(
            date=day,
            market="",
            mood="",
            theme="",
            candidates="",
            action="",
            path_fit="",
            hit="",
            broke="",
            change="",
            q1="",
            q2="",
        ), "fields": empty_fields(day)}
    text = path.read_text(encoding="utf-8")
    return {"date": day, "exists": True, "markdown": text, "fields": parse_fields(day, text)}


def empty_fields(day: str) -> dict:
    return {
        "date": day,
        "market": "",
        "mood": "",
        "theme": "",
        "candidates": "",
        "action": "",
        "path_fit": "",
        "hit": "",
        "broke": "",
        "change": "",
        "q1": "",
        "q2": "",
    }


def parse_fields(day: str, text: str) -> dict:
    fields = empty_fields(day)
    mapping = {
        "- 大盘：": "market",
        "- 情绪：": "mood",
        "- 主线板块：": "theme",
        "- 候选（池子 / 路径 / 状态）：": "candidates",
        "- 开仓 / 等待 / 放弃：": "action",
        "- 路径是否匹配：": "path_fit",
        "- 命中了哪条：": "hit",
        "- 打破了哪条：": "broke",
        "- 准备改哪一条（最多一条）：": "change",
        "1. 方向对不对：": "q1",
        "2. 路径是不是我的：": "q2",
    }
    for line in text.splitlines():
        for prefix, key in mapping.items():
            if line.startswith(prefix):
                fields[key] = line[len(prefix):].strip()
    return fields


def write_journal(day: str, fields: dict) -> dict:
    ensure_dirs()
    payload = empty_fields(day)
    payload.update({k: fields.get(k, "") for k in payload})
    payload["date"] = day
    markdown = TEMPLATE.format(**payload)
    _journal_path(day).write_text(markdown, encoding="utf-8")
    return read_journal(day)


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")
