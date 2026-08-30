"""One-shot confirmed-close sync. Used by Task Scheduler and `python -m app.jobs`."""
from __future__ import annotations

import json

from .engine.live import sync_live


def main() -> None:
    result = sync_live(force_bars=False)
    print(json.dumps({k: result.get(k) for k in ("state", "message", "source", "trade_date", "pool_size", "bars")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
