"""RULES.md 卖出：止损/失败优先于获利。本波 = 这次绿转红后的这一截红柱。"""
from __future__ import annotations


def red_wave_spans(hist: list) -> list[dict]:
    spans: list[dict] = []
    start = None
    peak = None
    for i, h in enumerate(hist):
        if h is not None and h > 0:
            if start is None:
                start = i
                peak = h
            else:
                peak = max(peak, h)
        elif start is not None:
            spans.append({"start": start, "end": i - 1, "peak": peak, "closed": True})
            start = None
            peak = None
    if start is not None and peak is not None:
        spans.append({"start": start, "end": len(hist) - 1, "peak": peak, "closed": False})
    return spans


def this_and_prev_wave(hist: list, entry_idx: int) -> tuple[dict | None, dict | None]:
    spans = red_wave_spans(hist)
    after = [w for w in spans if w["end"] >= entry_idx]
    if not after:
        return None, None
    this = after[-1]
    prevs = [w for w in spans if w["end"] < this["start"]]
    prev = prevs[-1] if prevs else None
    return this, prev


def fail_broken_lows(bars: list[dict], entry_idx: int) -> tuple[bool, str]:
    """最新价跌破买入日最新价×0.97（3%止损），或连续 2 日最新价低于买入日最低价。"""
    if entry_idx < 0 or entry_idx >= len(bars):
        return False, ""
    last = bars[-1]
    close = last.get("close")
    if close is None:
        return False, ""
    entry = bars[entry_idx]
    entry_close = entry.get("close")
    entry_low = entry.get("low")
    if entry_close is not None:
        stop = entry_close * 0.97
        if close <= stop:
            return True, f"止损：最新价 {close:.2f} ≤ 买入价×0.97（{stop:.2f}）"
    if entry_low is not None and len(bars) - 1 >= entry_idx + 2:
        c0 = bars[-2].get("close")
        if c0 is not None and c0 < entry_low and close < entry_low:
            return True, f"失败：连续 2 个更新日最新价低于买入日最低价 {entry_low:.2f}"
    return False, ""


def fail_hist_5d(hist: list, entry_idx: int) -> tuple[bool, str]:
    """金叉后 5 日内从未转红 → 当天走。"""
    last = len(hist) - 1
    if last <= entry_idx:
        return False, ""
    elapsed = last - entry_idx
    if elapsed < 5:
        return False, ""
    after = hist[entry_idx : last + 1]
    reds = [h for h in after if h is not None and h > 0]
    if not reds:
        return True, "金叉后 5 个交易日内从未转红"
    return False, ""


def take_profit(s: dict, entry_idx: int) -> tuple[bool, str]:
    """获利：已浮盈、本波已转红，且相邻两拍红柱从陡缩短到平（|s1|≤0.4|s0|）。"""
    bars = s.get("bars") or []
    hist = s.get("hist") or []
    last = s.get("last") or (bars[-1] if bars else None)
    if not bars or not last or entry_idx is None or entry_idx < 0 or entry_idx >= len(bars):
        return False, ""
    entry_close = bars[entry_idx].get("close")
    close = last.get("close")
    if entry_close is None or close is None or close <= entry_close:
        return False, ""
    if len(hist) < 3:
        return False, ""
    hm2, hm1, h1 = hist[-3], hist[-2], hist[-1]
    if None in (hm2, hm1, h1) or h1 <= 0:
        return False, ""
    this, _ = this_and_prev_wave(hist, entry_idx)
    if this is None:
        return False, ""
    s0 = hm1 - hm2
    s1 = h1 - hm1
    if not (s0 < 0 and s1 < 0):
        return False, ""
    if abs(s1) > 0.4 * abs(s0) + 1e-12:
        return False, ""
    return (
        True,
        (
            f"获利·平缓见顶：最新价 {close:.2f} > 买入价 {entry_close:.2f}；"
            f"本波红柱 H={h1:.4f}；s0={s0:.4f}、s1={s1:.4f}（|s1|≤0.4|s0|）"
        ),
    )


def evaluate_exit(s: dict, entry_idx: int) -> tuple[bool, str, str]:
    """Return (hit, kind, detail). kind: 止损 / 失败 / 获利。止损、失败优先于获利。"""
    bars = s.get("bars") or []
    last = s.get("last")
    if not bars or not last or entry_idx is None or entry_idx < 0:
        return False, "", ""
    if len(bars) - 1 <= entry_idx:
        return False, "", ""

    broken, why = fail_broken_lows(bars, entry_idx)
    if broken:
        kind = "止损" if "止损" in why else "失败"
        return True, kind, why
    hist_fail, why = fail_hist_5d(s["hist"], entry_idx)
    if hist_fail:
        return True, "失败", why
    profit, why = take_profit(s, entry_idx)
    if profit:
        return True, "获利", why
    return False, "", ""
