"""One-shot: re-read RULES.md, pull confirmed bars, run RULES scan."""
from __future__ import annotations

import json

from .config import LAST_SCAN_PATH
from .engine.live import sync_live
from .engine.rules_bind import refresh_bind
from .engine.scanner import scan_universe, summarize
from .store import load_settings, load_trades, load_universe, write_json


def run_rules_scan() -> dict:
    bind = refresh_bind()
    rows = scan_universe(load_universe(), load_settings(), load_trades())
    summary = summarize(rows)
    buys = [{"code": r["code"], "name": r["name"]} for r in rows if r["status"] == "买入"]
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
    bind = refresh_bind()
    print("RULES flags", json.dumps(bind.get("flags"), ensure_ascii=False))
    if bind.get("unimplemented"):
        print("unimplemented", bind["unimplemented"])
    result = sync_live(force_bars=False)
    print(json.dumps({k: result.get(k) for k in ("state", "message", "source", "trade_date", "pool_size", "bars")}, ensure_ascii=False, indent=2))
    scan = run_rules_scan()
    print("scan", json.dumps(scan.get("by_gate"), ensure_ascii=False), "买入", scan.get("buy_count"))
    from .engine.cycles import cycles_page

    page = cycles_page(scan.get("buys") or [])
    print("cycles open", len((page.get("live") or {}).get("open") or []), "rank", len(page.get("ranking") or []))


if __name__ == "__main__":
    main()
