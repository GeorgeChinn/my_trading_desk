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


def sort_pool(items: list[dict]) -> list[dict]:
    def key(item: dict):
        preferred = 0 if item.get("index_member") else 1
        amount = -(float(item.get("amount_yi") or 0))
        return (preferred, amount, item.get("code") or "")

    return sorted(items, key=key)
