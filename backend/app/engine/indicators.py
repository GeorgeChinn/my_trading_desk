from __future__ import annotations

from typing import Optional

from ..config import MACD_FAST, MACD_SIGNAL, MACD_SLOW


def sma(values: list[Optional[float]], n: int) -> list[Optional[float]]:
    out: list[Optional[float]] = [None] * len(values)
    if n <= 0:
        return out
    acc = 0.0
    filled = 0
    for i, raw in enumerate(values):
        if raw is None:
            filled = 0
            acc = 0.0
            continue
        acc += raw
        filled += 1
        if filled > n:
            prev = values[i - n]
            if prev is None:
                filled = 0
                acc = 0.0
                continue
            acc -= prev
            filled -= 1
        if filled == n:
            out[i] = acc / n
    return out


def ema(values: list[Optional[float]], n: int) -> list[Optional[float]]:
    """EMA seeded by the first complete SMA(n). A-share terminal convention."""
    out: list[Optional[float]] = [None] * len(values)
    if n <= 0 or len(values) < n:
        return out
    k = 2.0 / (n + 1)
    seed_vals: list[float] = []
    start = None
    for i, raw in enumerate(values):
        if raw is None:
            seed_vals = []
            continue
        seed_vals.append(raw)
        if len(seed_vals) == n:
            start = i
            break
    if start is None:
        return out
    prev = sum(seed_vals) / n
    out[start] = prev
    for i in range(start + 1, len(values)):
        raw = values[i]
        if raw is None or prev is None:
            out[i] = None
            prev = None
            continue
        prev = raw * k + prev * (1.0 - k)
        out[i] = prev
    return out


def macd_7428(
    close: list[Optional[float]],
    fast: int = MACD_FAST,
    slow: int = MACD_SLOW,
    signal: int = MACD_SIGNAL,
) -> dict[str, list[Optional[float]]]:
    """MACD(7,28,4). Histogram uses 2*(DIF-DEA), the common A-share 红绿柱."""
    dif_line = []
    ema_fast = ema(close, fast)
    ema_slow = ema(close, slow)
    for a, b in zip(ema_fast, ema_slow):
        if a is None or b is None:
            dif_line.append(None)
        else:
            dif_line.append(a - b)
    dea_line = ema(dif_line, signal)
    hist = []
    for d, e in zip(dif_line, dea_line):
        if d is None or e is None:
            hist.append(None)
        else:
            hist.append(2.0 * (d - e))
    return {"dif": dif_line, "dea": dea_line, "hist": hist}


def kdj(
    high: list[Optional[float]],
    low: list[Optional[float]],
    close: list[Optional[float]],
    n: int = 9,
    m1: int = 3,
    m2: int = 3,
) -> dict[str, list[Optional[float]]]:
    """Standard KDJ(9,3,3). RULES names KDJ but does not change these defaults."""
    length = len(close)
    k_line: list[Optional[float]] = [None] * length
    d_line: list[Optional[float]] = [None] * length
    j_line: list[Optional[float]] = [None] * length
    k_prev, d_prev = 50.0, 50.0
    for i in range(length):
        window = range(max(0, i - n + 1), i + 1)
        hs = [high[j] for j in window if high[j] is not None]
        ls = [low[j] for j in window if low[j] is not None]
        c = close[i]
        if not hs or not ls or c is None:
            continue
        hh, ll = max(hs), min(ls)
        rsv = 50.0 if hh == ll else (c - ll) / (hh - ll) * 100.0
        k_prev = (m1 - 1) / m1 * k_prev + 1 / m1 * rsv
        d_prev = (m2 - 1) / m2 * d_prev + 1 / m2 * k_prev
        k_line[i] = k_prev
        d_line[i] = d_prev
        j_line[i] = 3 * k_prev - 2 * d_prev
    return {"k": k_line, "d": d_line, "j": j_line}


def last_number(series: list[Optional[float]]) -> Optional[float]:
    for value in reversed(series):
        if value is not None:
            return value
    return None


def value_at(series: list[Optional[float]], index: int) -> Optional[float]:
    if index < 0 or index >= len(series):
        return None
    return series[index]
