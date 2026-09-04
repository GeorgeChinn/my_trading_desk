"""One-shot: re-read RULES.md, pull confirmed bars, run RULES scan."""
from __future__ import annotations

import json

from .config import LAST_SCAN_PATH
from .engine.live import sync_live
from .engine.rules_bind import refresh_bind
from .engine.scanner import scan_universe, summarize
from .store import load_settings, load_trades, load_universe, write_json


def run_rules_scan() -> dict:
    from .engine.rulesets import get_ruleset

    rs = get_ruleset("rules")
    text = rs["text"] if rs else None
    bind = refresh_bind(text, "rules")
    rows = scan_universe(
        load_universe(),
        load_settings(),
        load_trades(),
        flags=bind.get("flags"),
        engine=(rs or {}).get("engine") or "low_golden",
    )
    summary = summarize(rows)
    buys = [
        {"code": r["code"], "name": r["name"]}
        for r in rows
        if r.get("path_ready") or r.get("status") == "买入"
    ]
    payload = {
        "bind": bind,
        "by_gate": summary["by_gate"],
        "summary": summary["summary"],
        "buy_count": len(buys),
        "buys": buys,
    }
    write_json(LAST_SCAN_PATH, payload)
    return payload


def main() -> None:
    from .engine.cycles import cycles_page
    from .engine.rulesets import get_ruleset, list_rulesets
    from .engine.rules_bind import parse_flags

    bind = refresh_bind()
    print("RULES flags", json.dumps(bind.get("flags"), ensure_ascii=False))
    if bind.get("unimplemented"):
        print("unimplemented", bind["unimplemented"])
    result = sync_live(force_bars=False)
    print(json.dumps({k: result.get(k) for k in ("state", "message", "source", "trade_date", "pool_size", "bars")}, ensure_ascii=False, indent=2))
    scan = run_rules_scan()
    print("scan", json.dumps(scan.get("by_gate"), ensure_ascii=False), "买入", scan.get("buy_count"))
    for item in list_rulesets():
        if not item.get("engine_ok"):
            continue
        flags = parse_flags(item["text"])
        page = cycles_page(load_universe(), flags=flags, ruleset=item)
        summary = page.get("summary") or {}
        print("cycles", item["id"], "open", summary.get("open"), "closed", summary.get("closed"))


if __name__ == "__main__":
    main()
