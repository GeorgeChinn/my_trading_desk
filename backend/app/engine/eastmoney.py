"""Real A-share snapshot + daily bars. Sina list, East Money / Tencent kline. Ignore env proxy."""
from __future__ import annotations

import json
import time
from datetime import datetime

import requests

from ..config import POOL_AMOUNT_YI, POOL_FLOAT_MCAP_YI, POOL_MIN_PRICE
from .bars import load_bars, save_bars_csv, suffix_for, ts_code
from .clock import expected_close_date
from .pool import is_st_name, passes_pool, sort_pool

YI = 100_000_000.0
SINA_NODE = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
SINA_COUNT = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeStockCount"
KLINE_URLS = (
    "https://82.push2his.eastmoney.com/api/qt/stock/kline/get",
    "https://push2his.eastmoney.com/api/qt/stock/kline/get",
)
TENCENT_KLINE = "https://web.ifzq.gtimg.cn/appstock/app/kline/kline"


def _session() -> requests.Session:
    sess = requests.Session()
    sess.trust_env = False
    sess.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"})
    return sess


def _get_json(sess: requests.Session, url: str, params: dict, timeout: int = 20):
    last_exc = None
    for _ in range(3):
        try:
            resp = sess.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            text = resp.text.strip()
            if text.startswith("(") and text.endswith(")"):
                text = text[1:-1]
            return json.loads(text)
        except Exception as exc:
            last_exc = exc
            time.sleep(0.5)
    raise last_exc


def sina_symbol(code: str) -> str:
    c = ts_code(code)
    return f"{suffix_for(c).lower()}{c}"


def secid(code: str) -> str:
    c = ts_code(code)
    if c.startswith(("6", "9")):
        return f"1.{c}"
    return f"0.{c}"


def fetch_node(sess: requests.Session, node: str, page_size: int = 80) -> list[dict]:
    rows: list[dict] = []
    page = 1
    while page <= 160:
        payload = _get_json(
            sess,
            SINA_NODE,
            {"page": page, "num": page_size, "sort": "amount", "asc": "0", "node": node},
            timeout=25,
        )
        if not payload:
            break
        if isinstance(payload, dict):
            payload = payload.get("data") or []
        if not isinstance(payload, list) or not payload:
            break
        rows.extend(payload)
        if len(payload) < page_size:
            break
        page += 1
        time.sleep(0.08)
    return rows


def fetch_spot(log=None) -> list[dict]:
    talk = log or (lambda _m: None)
    sess = _session()
    # hs_a ≈ 全部沪深 A 股；失败则拆创业板/科创板再并。
    try:
        rows = fetch_node(sess, "hs_a", page_size=40)
        talk(f"新浪 hs_a 快照 {len(rows)} 只")
        if len(rows) >= 2000:
            return rows
    except Exception as exc:
        talk(f"hs_a 分页失败：{exc}")
        rows = []
    extra = []
    for node in ("cyb", "kcb"):
        try:
            extra.extend(fetch_node(sess, node, page_size=80))
            talk(f"新浪 {node} {len(extra)} 累计")
        except Exception as exc:
            talk(f"{node} 失败：{exc}")
    by_code = {}
    for rec in rows + extra:
        code = ts_code(str(rec.get("code") or rec.get("symbol") or ""))
        if code:
            by_code[code] = rec
    talk(f"合并快照 {len(by_code)} 只")
    return list(by_code.values())


def fetch_index_codes(node: str) -> set[str]:
    sess = _session()
    try:
        rows = fetch_node(sess, node, page_size=80)
    except Exception:
        return set()
    out = set()
    for rec in rows:
        code = ts_code(str(rec.get("code") or rec.get("symbol") or ""))
        if code:
            out.add(code)
    return out


def fetch_kline_em(code: str, limit: int = 180) -> list[dict]:
    sess = _session()
    sess.headers["Referer"] = "https://quote.eastmoney.com/"
    last_exc = None
    for url in KLINE_URLS:
        try:
            payload = _get_json(
                sess,
                url,
                {
                    "secid": secid(code),
                    "klt": "101",
                    "fqt": "0",
                    "lmt": str(limit),
                    "end": "20500101",
                    "fields1": "f1,f2,f3,f4,f5,f6",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57",
                },
                timeout=20,
            )
            klines = ((payload or {}).get("data") or {}).get("klines") or []
            rows = []
            for line in klines:
                parts = str(line).split(",")
                if len(parts) < 6:
                    continue
                amount = float(parts[6]) if len(parts) > 6 else 0.0
                rows.append(
                    {
                        "date": parts[0][:10],
                        "open": float(parts[1]),
                        "close": float(parts[2]),
                        "high": float(parts[3]),
                        "low": float(parts[4]),
                        "volume": float(parts[5]) * 100.0,
                        "amount": amount,
                    }
                )
            if rows:
                return rows
        except Exception as exc:
            last_exc = exc
            continue
    if last_exc:
        raise last_exc
    return []


def fetch_kline_sina(code: str, limit: int = 180) -> list[dict]:
    sess = _session()
    sess.headers["Referer"] = "https://finance.sina.com.cn/"
    payload = _get_json(
        sess,
        "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData",
        {"symbol": sina_symbol(code), "scale": "240", "ma": "no", "datalen": str(limit)},
        timeout=20,
    )
    if not isinstance(payload, list):
        return []
    rows = []
    for rec in payload:
        rows.append(
            {
                "date": str(rec.get("day") or rec.get("date") or "")[:10],
                "open": float(rec["open"]),
                "close": float(rec["close"]),
                "high": float(rec["high"]),
                "low": float(rec["low"]),
                "volume": float(rec.get("volume") or 0),
                "amount": 0.0,
            }
        )
    return [row for row in rows if row["date"]]


def fetch_kline_tencent(code: str, limit: int = 180) -> list[dict]:
    sess = _session()
    sess.headers["Referer"] = "https://gu.qq.com/"
    symbol = sina_symbol(code)
    payload = _get_json(
        sess,
        TENCENT_KLINE,
        {"param": f"{symbol},day,,,{limit}"},
        timeout=20,
    )
    data = ((payload or {}).get("data") or {}).get(symbol) or {}
    day = data.get("day") or data.get("qfqday") or data.get("hfqday") or []
    rows = []
    for item in day:
        if not item or len(item) < 5:
            continue
        vol = float(item[5]) if len(item) > 5 else 0.0
        rows.append(
            {
                "date": str(item[0])[:10],
                "open": float(item[1]),
                "close": float(item[2]),
                "high": float(item[3]),
                "low": float(item[4]),
                "volume": vol * 100.0,
                "amount": 0.0,
            }
        )
    return rows


def fetch_index_kline(symbol: str, limit: int = 8) -> list[dict]:
    """Index kline. symbol like sh000300 / sh000001, not a stock ts_code."""
    sess = _session()
    sess.headers["Referer"] = "https://gu.qq.com/"
    payload = _get_json(sess, TENCENT_KLINE, {"param": f"{symbol},day,,,{limit}"}, timeout=20)
    data = ((payload or {}).get("data") or {}).get(symbol) or {}
    day = data.get("day") or data.get("qfqday") or data.get("hfqday") or []
    rows = []
    for item in day:
        if not item or len(item) < 5:
            continue
        rows.append(
            {
                "date": str(item[0])[:10],
                "open": float(item[1]),
                "close": float(item[2]),
                "high": float(item[3]),
                "low": float(item[4]),
            }
        )
    return rows


def fetch_industry_boards() -> list[dict]:
    """East Money 行业板块. f127 is 近3日涨跌幅 %."""
    sess = _session()
    sess.headers["Referer"] = "https://quote.eastmoney.com/"
    hosts = (
        "https://push2.eastmoney.com/api/qt/clist/get",
        "https://82.push2.eastmoney.com/api/qt/clist/get",
    )
    out: list[dict] = []
    for host in hosts:
        try:
            page = 1
            while page <= 20:
                payload = _get_json(
                    sess,
                    host,
                    {
                        "pn": page,
                        "pz": 40,
                        "po": 1,
                        "np": 1,
                        "fltt": 2,
                        "invt": 2,
                        "fid": "f3",
                        "fs": "m:90+t:2",
                        "fields": "f12,f14,f3,f127",
                    },
                    timeout=15,
                )
                chunk = ((payload or {}).get("data") or {}).get("diff") or []
                if not chunk:
                    break
                for rec in chunk:
                    name = str(rec.get("f14") or "")
                    if not name:
                        continue
                    ret = rec.get("f127")
                    try:
                        ret_f = float(ret) if ret not in (None, "-", "") else None
                    except (TypeError, ValueError):
                        ret_f = None
                    out.append({"code": str(rec.get("f12") or ""), "name": name, "ret_3d_pct": ret_f})
                if len(chunk) < 40:
                    break
                page += 1
                time.sleep(0.05)
            if out:
                return out
        except Exception:
            out = []
            continue
    return out


def fetch_sina_industry_map() -> dict[str, str]:
    """code -> 新浪行业名. One request per industry node."""
    sess = _session()
    try:
        payload = _get_json(sess, "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodes", {}, timeout=20)
    except Exception:
        return {}
    nodes: list[tuple[str, str]] = []

    def walk(node, path):
        if not isinstance(node, list):
            return
        if (
            len(node) >= 3
            and isinstance(node[0], str)
            and isinstance(node[2], str)
            and str(node[2]).startswith("new_")
        ):
            if path and path[-1] == "新浪行业":
                nodes.append((node[0], node[2]))
            return
        for item in node:
            if isinstance(item, list):
                nxt = path + [node[0]] if isinstance(node[0], str) else path
                walk(item, nxt)

    walk(payload, [])
    mapping: dict[str, str] = {}
    for name, node_id in nodes:
        try:
            rows = fetch_node(sess, node_id, page_size=80)
        except Exception:
            continue
        for rec in rows:
            code = ts_code(str(rec.get("code") or rec.get("symbol") or ""))
            if code:
                mapping[code] = name
        time.sleep(0.04)
    return mapping


def fetch_stock_industry(code: str) -> str | None:
    sess = _session()
    sess.headers["Referer"] = "https://quote.eastmoney.com/"
    try:
        resp = sess.get(
            "https://push2.eastmoney.com/api/qt/stock/get",
            params={"secid": secid(code), "invt": 2, "fltt": 2, "fields": "f57,f58,f127"},
            timeout=6,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception:
        return None
    data = (payload or {}).get("data") or {}
    name = data.get("f127")
    if isinstance(name, str) and name and name not in ("-", "None"):
        return name
    return None


KLINE_CHAIN = (
    ("tencent", fetch_kline_tencent),
    ("sina", fetch_kline_sina),
    ("eastmoney", fetch_kline_em),
)


def fetch_kline(code: str, limit: int = 180) -> list[dict]:
    rows, _name = fetch_kline_with_source(code, limit=limit)
    return rows


def fetch_kline_with_source(code: str, limit: int = 180) -> tuple[list[dict], str]:
    errors = []
    for name, fn in KLINE_CHAIN:
        try:
            rows = fn(code, limit=limit)
            if rows:
                return rows, name
            errors.append(f"{name}:空")
        except Exception as exc:
            errors.append(f"{name}:{exc}")
    raise RuntimeError("日线源全部失败 " + " | ".join(errors))


def probe_sources() -> list[dict]:
    ping = "600519"
    out = []
    for name, fn in KLINE_CHAIN:
        started = time.time()
        try:
            rows = fn(ping, limit=5)
            last = rows[-1] if rows else {}
            out.append(
                {
                    "name": name,
                    "role": "日线后备",
                    "ok": bool(rows),
                    "bars": len(rows),
                    "last_date": last.get("date"),
                    "last_close": last.get("close"),
                    "ms": int((time.time() - started) * 1000),
                }
            )
        except Exception as exc:
            out.append(
                {
                    "name": name,
                    "role": "日线后备",
                    "ok": False,
                    "error": str(exc)[:160],
                    "ms": int((time.time() - started) * 1000),
                }
            )
    started = time.time()
    try:
        sess = _session()
        rows = _get_json(
            sess,
            SINA_NODE,
            {"page": 1, "num": 3, "sort": "amount", "asc": "0", "node": "hs_a"},
            timeout=15,
        )
        out.append(
            {
                "name": "sina-spot",
                "role": "全市场快照",
                "ok": bool(rows),
                "bars": len(rows) if isinstance(rows, list) else 0,
                "ms": int((time.time() - started) * 1000),
            }
        )
    except Exception as exc:
        out.append(
            {
                "name": "sina-spot",
                "role": "全市场快照",
                "ok": False,
                "error": str(exc)[:160],
                "ms": int((time.time() - started) * 1000),
            }
        )
    return out


def _num(rec: dict, *keys) -> float | None:
    for key in keys:
        if key in rec and rec[key] not in (None, ""):
            try:
                val = float(rec[key])
                if val == val:
                    return val
            except (TypeError, ValueError):
                continue
    return None


def _round_or_none(val: float | None, ndigits: int = 3) -> float | None:
    if val is None:
        return None
    return round(val, ndigits)


def build_pool(log=None) -> tuple[list[dict], dict]:
    talk = log or (lambda _m: None)
    spot = fetch_spot(log=talk)
    hs300 = fetch_index_codes("hs300")
    sse50 = fetch_index_codes("zhishu_000016")
    hgt = fetch_index_codes("hgt")
    talk(f"优先样本核对：沪深300 {len(hs300)} / 上证50 {len(sse50)} / 沪股通 {len(hgt)}")
    funnel = {
        "listed": len(spot),
        "quote_rows": len(spot),
        "non_st": 0,
        "price_ok": 0,
        "mcap_ok": 0,
        "amount_ok": 0,
        "pool": 0,
        "preferred": 0,
        "pe_ok": 0,
        "trade_date": datetime.now().strftime("%Y-%m-%d"),
        "source": "sina+eastmoney",
        "rules": {
            "流通市值": f"≥ {POOL_FLOAT_MCAP_YI:.0f} 亿",
            "日成交额": f"≥ {POOL_AMOUNT_YI:.0f} 亿",
            "股价": f"≥ {POOL_MIN_PRICE:.0f} 元",
            "动态市盈": "> 0（亏损票排除）",
            "ST": "非 ST、非 *ST",
            "优先样本": "沪股通 / 沪深300 / 上证50；其他过池股也保留",
        },
    }
    pool: list[dict] = []
    for rec in spot:
        code = ts_code(str(rec.get("code") or rec.get("symbol") or ""))
        name = str(rec.get("name") or code)
        if not code:
            continue
        st = is_st_name(name)
        close = _num(rec, "trade")
        amount = _num(rec, "amount")
        nmc = _num(rec, "nmc")  # 万元
        pe = _num(rec, "per", "pe", "pe_ttm")
        amount_yi = amount / YI if amount is not None else None
        float_mcap_yi = nmc / 10_000.0 if nmc is not None else None
        if not st:
            funnel["non_st"] += 1
        if close is not None and close >= POOL_MIN_PRICE:
            funnel["price_ok"] += 1
        if float_mcap_yi is not None and float_mcap_yi >= POOL_FLOAT_MCAP_YI:
            funnel["mcap_ok"] += 1
        if amount_yi is not None and amount_yi >= POOL_AMOUNT_YI:
            funnel["amount_ok"] += 1
        if pe is not None and pe > 0:
            funnel["pe_ok"] += 1
        if not passes_pool(close=close, amount_yi=amount_yi, float_mcap_yi=float_mcap_yi, is_st=st, pe=pe):
            continue
        members = []
        if code in hs300:
            members.append("沪深300")
        if code in sse50:
            members.append("上证50")
        if code in hgt:
            members.append("沪股通")
        pool.append(
            {
                "code": code,
                "ts_code": f"{code}.{suffix_for(code)}",
                "name": name,
                "float_mcap_yi": round(float_mcap_yi, 2) if float_mcap_yi is not None else None,
                "amount_yi": round(amount_yi, 2) if amount_yi is not None else None,
                "close": close,
                "pe": _round_or_none(pe),
                "is_st": False,
                "index_member": members,
                "tags": [],
                "trade_date": funnel["trade_date"],
                "source": "sina",
            }
        )
        funnel["pool"] += 1
        if members:
            funnel["preferred"] += 1
    pool = sort_pool(pool)
    talk(f"第3条 入池 {len(pool)} 只（优先样本 {funnel['preferred']}）")
    return pool, funnel


def pull_history(pool: list[dict], log=None, progress=None) -> dict:
    talk = log or (lambda _m: None)
    ok = skip = fail = 0
    total = len(pool)
    last_dates = []
    for i, item in enumerate(pool, start=1):
        code = item["code"]
        existing = load_bars(code)
        expect = expected_close_date().isoformat()
        if existing and len(existing) >= 40 and existing[-1]["date"] >= expect:
            skip += 1
            if progress:
                progress(i, total)
            continue
        try:
            rows, used = fetch_kline_with_source(code, limit=180)
            item["bar_source"] = used
            time.sleep(0.05)
        except Exception as exc:
            fail += 1
            talk(f"{code} 日线失败：{exc}")
            if progress:
                progress(i, total)
            continue
        if len(rows) < 40:
            fail += 1
            if progress:
                progress(i, total)
            continue
        if rows[-1].get("amount", 0) <= 0 and item.get("amount_yi"):
            rows[-1]["amount"] = float(item["amount_yi"]) * YI
        save_bars_csv(code, rows, name=item.get("name"))
        last_dates.append(rows[-1]["date"])
        ok += 1
        if progress:
            progress(i, total)
        if i % 25 == 0:
            talk(f"日线已写 {ok}，跳过 {skip}，失败 {fail} / {total}")
    return {"ok": ok, "skip": skip, "fail": fail, "total": total, "last_bar": max(last_dates) if last_dates else ""}
