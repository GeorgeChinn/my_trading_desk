"""RULES §7 离场。7.1 失败离场优先于 7.2 波段减仓/清仓。

本波 = 买入日之后（含买入当日已在红柱里）这一截由绿转红的红柱。
前一波 = 本波开始之前最近一座已结束的红柱。不要拿更早另一轮来比。
"""
from __future__ import annotations

from ..config import HHV_LOOKBACK, KDJ_HIGH


def _recent(series: list, n: int, skip_last: int = 0):
    end = len(series) - skip_last
    start = max(0, end - n)
    return [item for item in series[start:end] if item is not None]


def _cross_down(fast: list, slow: list) -> bool:
    if len(fast) < 2 or len(slow) < 2:
        return False
    a0, a1 = fast[-2], fast[-1]
    b0, b1 = slow[-2], slow[-1]
    if None in (a0, a1, b0, b1):
        return False
    return a0 >= b0 and a1 < b1


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
    """§7.1：收盘跌破买入日最低价，或跌破金叉阳线最低价。"""
    if entry_idx < 0 or entry_idx >= len(bars):
        return False, ""
    last = bars[-1]
    close = last.get("close")
    if close is None:
        return False, ""
    entry = bars[entry_idx]
    entry_low = entry.get("low")
    if entry_low is not None and close < entry_low:
        return True, f"收盘 {close:.2f} 跌破买入日最低价 {entry_low:.2f}"
    # 当前实现里买入日即金叉确认日，同一根 K 再比一次，避免以后买入日错位。
    yang_low = entry.get("low")
    if yang_low is not None and close < yang_low:
        return True, f"收盘 {close:.2f} 跌破金叉阳线最低价 {yang_low:.2f}"
    return False, ""


def fail_hist_5d(hist: list, entry_idx: int) -> tuple[bool, str]:
    """§7.1：金叉后 5 个交易日内红柱没有放大，或再次绿柱拉长。"""
    last = len(hist) - 1
    if last <= entry_idx:
        return False, ""
    h0, h1 = hist[last - 1] if last - 1 >= 0 else None, hist[last]
    if h0 is not None and h1 is not None and h0 < 0 and h1 < 0 and abs(h1) > abs(h0):
        return True, f"金叉后再次绿柱拉长（{h0:.4f} → {h1:.4f}）"
    elapsed = last - entry_idx
    if elapsed < 5:
        return False, ""
    window = hist[entry_idx : entry_idx + 6]
    reds = [h for h in window if h is not None and h > 0]
    if not reds:
        return True, "金叉后 5 个交易日内未见红柱放大（一直未转红）"
    if max(reds) <= reds[0] + 1e-12:
        return True, f"金叉后 5 个交易日内红柱没有放大（首根红柱 {reds[0]:.4f} / 最大 {max(reds):.4f}）"
    return False, ""


def section72_wave(hist: list, entry_idx: int) -> tuple[bool, str]:
    this, prev = this_and_prev_wave(hist, entry_idx)
    if this is None or prev is None:
        return False, "本波或前一波红柱不足，不拿更早另一轮比"
    if this["peak"] < prev["peak"]:
        return True, f"本波红柱 {this['peak']:.4f} < 前一波 {prev['peak']:.4f}"
    return False, f"本波红柱 {this['peak']:.4f} 尚未小于前一波 {prev['peak']:.4f}"


def section72_kdj(s: dict) -> tuple[bool, str]:
    k_line, d_line, j_line = s["k"], s["d"], s["j"]
    kd_dead = _cross_down(k_line, d_line)
    k0, d0 = k_line[-1], d_line[-1]
    kd_high = k0 is not None and d0 is not None and min(k0, d0) >= KDJ_HIGH
    j_prev = _recent(j_line, 20, skip_last=1)
    kdj_not_new_high = bool(j_prev) and j_line[-1] is not None and j_line[-1] < max(j_prev)
    if kd_dead and kd_high:
        return True, f"K/D 在 {KDJ_HIGH:.0f} 以上死叉"
    if kdj_not_new_high:
        return True, "KDJ 不创新高"
    return False, "KDJ 未见高位死叉，也未见不创新高"


def evaluate_exit(s: dict, entry_idx: int) -> tuple[bool, str, str]:
    """Return (hit, section, detail). section is 7.1 / 7.2 / ''."""
    bars = s.get("bars") or []
    last, prev = s.get("last"), s.get("prev")
    if not bars or not last or entry_idx is None or entry_idx < 0:
        return False, "", ""
    if len(bars) - 1 <= entry_idx:
        return False, "", ""

    broken, why = fail_broken_lows(bars, entry_idx)
    if broken:
        return True, "7.1", why
    hist_fail, why = fail_hist_5d(s["hist"], entry_idx)
    if hist_fail:
        return True, "7.1", why

    if not prev or last.get("dif") is None or prev.get("dif") is None:
        return False, "", ""
    wave_ok, wave_detail = section72_wave(s["hist"], entry_idx)
    dif_down = last["dif"] < prev["dif"]
    hhv_win = _recent(s["h"], HHV_LOOKBACK) or [last.get("high")]
    hhv = max(hhv_win)
    new_high = last.get("high") is not None and last["high"] >= hhv
    kdj_ok, kdj_detail = section72_kdj(s)
    if wave_ok and dif_down and new_high and kdj_ok:
        detail = "；".join(
            [
                wave_detail,
                f"DIF 下行 {last['dif']:.4f} < {prev['dif']:.4f}",
                f"股价创近{HHV_LOOKBACK}日新高",
                kdj_detail,
            ]
        )
        return True, "7.2", detail
    return False, "", ""
