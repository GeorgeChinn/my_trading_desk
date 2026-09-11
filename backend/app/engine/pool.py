"""RULES §3 pool filter. Numbers come only from config / RULES.md."""
from __future__ import annotations

from ..config import POOL_AMOUNT_YI, POOL_FLOAT_MCAP_YI, POOL_MIN_PRICE

PREFERRED = ("沪股通", "沪深300", "上证50")


def is_st_name(name: str) -> bool:
    compact = (name or "").replace(" ", "").upper()
    return "ST" in compact


def pool_fail_reasons(
    *,
    close: float | None,
    amount_yi: float | None,
    float_mcap_yi: float | None,
    is_st: bool,
    pe: float | None = None,
) -> list[str]:
    fail = []
    if is_st:
        fail.append("ST / *ST")
    if close is None:
        fail.append("股价证据不足")
    elif close < POOL_MIN_PRICE:
        fail.append(f"股价 {close:.2f} < {POOL_MIN_PRICE:.0f} 元")
    if float_mcap_yi is not None and float_mcap_yi <= 0:
        float_mcap_yi = None
    if amount_yi is not None and amount_yi <= 0:
        amount_yi = None
    if float_mcap_yi is None:
        fail.append("流通市值证据不足")
    elif float_mcap_yi < POOL_FLOAT_MCAP_YI:
        fail.append(f"流通市值 {float_mcap_yi:.0f} 亿 < {POOL_FLOAT_MCAP_YI:.0f} 亿")
    if amount_yi is None:
        fail.append("日成交额证据不足")
    elif amount_yi < POOL_AMOUNT_YI:
        fail.append(f"日成交额 {amount_yi:.2f} 亿 < {POOL_AMOUNT_YI:.0f} 亿")
    if pe is not None and pe <= 0:
        fail.append(f"动态市盈 {pe:.2f} ≤ 0（亏损票排除）")
    return fail


def passes_pool(**kwargs) -> bool:
    return not pool_fail_reasons(**kwargs)


def build_universe_from_csv() -> tuple[list[dict], dict]:
    """用本地日线重建全 A 底池。规则池子由各 RULES 自己筛，这里不按 300 亿截断。"""
    from ..config import CSV_DIR, DATA_DIR
    from ..store import load_quotes, read_json
    from .bars import bar_amount, peek_last_bar, suffix_for, ts_code
    from .clock import asof_date

    quotes = load_quotes() or {}
    blob = read_json(DATA_DIR / "industry_map.json", {})
    imap = {}
    if isinstance(blob, dict):
        raw = blob.get("sw2") or blob.get("codes") or blob
        if isinstance(raw, dict):
            imap = {str(k): str(v) for k, v in raw.items() if v and not str(k).startswith("_")}
    asof = asof_date()
    funnel = {
        "listed": 0,
        "quote_rows": 0,
        "non_st": 0,
        "price_ok": 0,
        "mcap_ok": 0,
        "amount_ok": 0,
        "pool": 0,
        "preferred": 0,
        "pe_ok": 0,
        "trade_date": asof,
        "source": "local-csv",
        "rules": {
            "底池": "data/csv 有效日线 = 总股池，所有规则的基准",
            "流通市值": f"RULES 入池 ≥ {POOL_FLOAT_MCAP_YI:.0f} 亿",
            "日成交额": f"RULES 入池 ≥ {POOL_AMOUNT_YI:.0f} 亿",
        },
    }
    out: list[dict] = []
    for path in CSV_DIR.glob("*.csv"):
        code = ts_code(path.stem)
        if not code:
            continue
        if not code.isdigit() or len(code) != 6:
            continue
        last = peek_last_bar(code)
        if not last or last.get("close") is None:
            continue
        q = quotes.get(code) or {}
        name = str(last.get("name") or q.get("name") or code).strip() or code
        st = is_st_name(name)
        close = last.get("close")
        if close is None:
            close = q.get("close")
        try:
            close = float(close) if close is not None else None
        except (TypeError, ValueError):
            close = None
        amt = bar_amount(last) if last else None
        if amt is None and q.get("amount"):
            amt = q.get("amount")
        amount_yi = None
        if amt:
            amount_yi = amt / 100_000_000.0
        elif q.get("amount_yi") is not None:
            try:
                amount_yi = float(q["amount_yi"])
            except (TypeError, ValueError):
                amount_yi = None
        mcap = q.get("float_mcap_yi")
        try:
            mcap = float(mcap) if mcap is not None else None
        except (TypeError, ValueError):
            mcap = None
        pe = q.get("pe")
        try:
            pe = float(pe) if pe is not None else None
        except (TypeError, ValueError):
            pe = None
        funnel["listed"] += 1
        funnel["quote_rows"] += 1
        if not st:
            funnel["non_st"] += 1
        if close is not None and close >= POOL_MIN_PRICE:
            funnel["price_ok"] += 1
        if mcap is not None and mcap >= POOL_FLOAT_MCAP_YI:
            funnel["mcap_ok"] += 1
        if amount_yi is not None and amount_yi >= POOL_AMOUNT_YI:
            funnel["amount_ok"] += 1
        if pe is not None and pe > 0:
            funnel["pe_ok"] += 1
        if passes_pool(close=close, amount_yi=amount_yi, float_mcap_yi=mcap, is_st=st, pe=pe):
            funnel["pool"] += 1
        out.append(
            {
                "code": code,
                "ts_code": f"{code}.{suffix_for(code)}",
                "name": name,
                "float_mcap_yi": round(mcap, 2) if mcap is not None else None,
                "amount_yi": round(amount_yi, 2) if amount_yi is not None else None,
                "close": close,
                "pe": round(pe, 3) if pe is not None else None,
                "is_st": st,
                "industry": imap.get(code) or "",
                "index_member": [],
                "tags": [],
                "trade_date": str(last.get("date") or asof)[:10],
                "source": "local-csv",
            }
        )
    out.sort(key=lambda x: x.get("code") or "")
    funnel["listed"] = len(out)
    return out, funnel


def ashare_pool_public() -> dict:
    """A 股全股池：所有规则的扫描基准。"""
    from ..config import CSV_DIR
    from ..store import load_pool_snapshot, load_quotes, load_quotes_meta, load_settings, load_sync_status, load_universe
    from .clock import asof_date

    uni = load_universe()
    quotes = load_quotes() or {}
    qmeta = load_quotes_meta()
    snap = load_pool_snapshot()
    settings = load_settings()
    sync = load_sync_status()
    csv_n = sum(1 for _ in CSV_DIR.glob("*.csv"))
    asof = asof_date(settings.get("last_trade_date") or qmeta.get("trade_date") or snap.get("trade_date") or "")
    return {
        "count": len(uni) or csv_n,
        "csv_count": csv_n,
        "quote_count": len(quotes),
        "non_st": snap.get("non_st"),
        "asof": asof,
        "updated_at": qmeta.get("updated_at") or sync.get("finished_at") or "",
        "sync_at": sync.get("finished_at") or "",
        "source": "data/csv",
        "schedule_last": settings.get("schedule_last_fired") or "",
        "note": "总股池 = data/csv 目录里有效日线。这是 A 股可扫描底池，所有 RULES 以此为基准再排除 / 观察 / 买入（试仓） / 卖出（取关）。",
        "funnel": snap,
    }


def sort_pool(items: list[dict]) -> list[dict]:
    def key(item: dict):
        preferred = 0 if item.get("index_member") else 1
        amount = -(float(item.get("amount_yi") or 0))
        return (preferred, amount, item.get("code") or "")

    return sorted(items, key=key)
