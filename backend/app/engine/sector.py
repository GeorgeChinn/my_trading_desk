"""RULES §2 板块近 3 个交易日相对大盘。弱则不得进入等待/买入。不编造。"""
from __future__ import annotations

from datetime import datetime

from ..config import DATA_DIR, SECTOR_PATH
from ..store import read_json, write_json
from .bars import load_bars, ts_code
from .eastmoney import fetch_index_kline, fetch_industry_boards, fetch_sina_industry_map


def _norm(name: str) -> str:
    s = (name or "").strip()
    for token in ("Ⅰ", "Ⅱ", "Ⅲ", "IV", "III", "II", "I", "行业", "板块", "概念", "指数", " "):
        s = s.replace(token, "")
    return s


def ret_nd(closes: list[float], n: int = 3) -> float | None:
    if len(closes) < n + 1:
        return None
    a, b = closes[-(n + 1)], closes[-1]
    if not a:
        return None
    return (b / a - 1.0) * 100.0


def match_board(industry: str, boards: list[dict]) -> dict | None:
    raw = (industry or "").strip()
    if not raw or not boards:
        return None
    want = _norm(raw)
    if not want:
        return None
    exact = [b for b in boards if _norm(b.get("name") or "") == want]
    if exact:
        return exact[0]
    hits = []
    for b in boards:
        bn = _norm(b.get("name") or "")
        if not bn:
            continue
        if bn in want or want in bn:
            hits.append(b)
    if not hits:
        return None
    hits.sort(key=lambda b: len(_norm(b.get("name") or "")), reverse=True)
    return hits[0]


def load_sector_snap() -> dict:
    data = read_json(SECTOR_PATH, {})
    return data if isinstance(data, dict) else {}


def sector_of(code: str) -> dict | None:
    snap = load_sector_snap()
    stocks = snap.get("stocks") or {}
    return stocks.get(ts_code(code))


def refresh_sector_snap(pool: list[dict], log=None) -> dict:
    talk = log or (lambda _m: None)
    market_rows = fetch_index_kline("sh000300", limit=8)
    market_closes = [r["close"] for r in market_rows]
    market_ret = ret_nd(market_closes, 3)
    market_asof = market_rows[-1]["date"] if market_rows else ""
    talk(f"沪深300 近3日 {market_ret if market_ret is None else round(market_ret, 2)}% 确认 {market_asof}")

    boards = fetch_industry_boards()
    talk(f"行业板块 {len(boards)} 条")

    industry_map = fetch_sina_industry_map()
    talk(f"新浪行业归属 {len(industry_map)} 只")
    write_json(DATA_DIR / "industry_map.json", {"codes": industry_map, "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
    for item in pool:
        code = ts_code(str(item.get("code") or ""))
        if not code:
            continue
        item["industry"] = industry_map.get(code) or item.get("industry")

    stocks: dict[str, dict] = {}
    for item in pool:
        code = ts_code(str(item.get("code") or ""))
        if not code:
            continue
        industry = item.get("industry")
        board = match_board(str(industry or ""), boards) if industry else None
        board_ret = board.get("ret_3d_pct") if board else None
        if board_ret is None and industry:
            board_ret = _peer_ret(code, str(industry), pool)
        weak = None
        vs = None
        if board_ret is not None and market_ret is not None:
            vs = board_ret - market_ret
            weak = board_ret < market_ret
        stocks[code] = {
            "industry": industry,
            "board": (board or {}).get("name") if board else None,
            "board_ret_3d_pct": None if board_ret is None else round(float(board_ret), 2),
            "market_ret_3d_pct": None if market_ret is None else round(float(market_ret), 2),
            "vs_market": None if vs is None else round(float(vs), 2),
            "weak": weak,
        }
    payload = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "asof": market_asof,
        "market": {
            "symbol": "sh000300",
            "name": "沪深300",
            "ret_3d_pct": None if market_ret is None else round(float(market_ret), 2),
        },
        "board_count": len(boards),
        "stocks": stocks,
        "note": "板块近3日涨跌幅相对沪深300。弱于大盘不得进入等待/买入。这是事实记录。",
    }
    write_json(SECTOR_PATH, payload)
    weak_n = sum(1 for s in stocks.values() if s.get("weak") is True)
    talk(f"板块快照完成 {len(stocks)} 只，弱于大盘 {weak_n}")
    return payload


def _peer_ret(code: str, industry: str, pool: list[dict]) -> float | None:
    """Fallback: 池内同行业近3日平均，证据不足则 None。"""
    want = _norm(industry)
    if not want:
        return None
    rets = []
    for item in pool:
        other = ts_code(str(item.get("code") or ""))
        ind = item.get("industry") or ""
        if other == code or _norm(str(ind)) != want:
            continue
        bars = load_bars(other)
        if len(bars) < 4:
            continue
        closes = [b["close"] for b in bars[-4:]]
        r = ret_nd(closes, 3)
        if r is not None:
            rets.append(r)
    if not rets:
        return None
    return sum(rets) / len(rets)
