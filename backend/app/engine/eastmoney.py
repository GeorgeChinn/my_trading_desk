"""Real A-share snapshot + daily bars. Sina list, East Money / Tencent kline. Ignore env proxy."""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta

import requests

from ..config import POOL_AMOUNT_YI, POOL_FLOAT_MCAP_YI, POOL_MIN_PRICE
from ..store import load_quotes, load_universe, save_pool_snapshot, save_quotes, save_universe
from .bars import load_bars, save_bars_csv, suffix_for, ts_code
from .clock import asof_date, expected_close_date, is_weekend_date
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
                        "fields": "f12,f14,f3,f104,f105,f127",
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
                    up = rec.get("f104")
                    down = rec.get("f105")
                    chg = rec.get("f3")
                    try:
                        up_n = int(up) if up not in (None, "-", "") else None
                    except (TypeError, ValueError):
                        up_n = None
                    try:
                        down_n = int(down) if down not in (None, "-", "") else None
                    except (TypeError, ValueError):
                        down_n = None
                    try:
                        chg_f = float(chg) if chg not in (None, "-", "") else None
                    except (TypeError, ValueError):
                        chg_f = None
                    out.append(
                        {
                            "code": str(rec.get("f12") or ""),
                            "name": name,
                            "ret_3d_pct": ret_f,
                            "ret_1d_pct": chg_f,
                            "up_n": up_n,
                            "down_n": down_n,
                        }
                    )
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


def fetch_sina_industry_map() -> dict:
    """code -> 申万二级优先，否则一级 / 新浪行业。返回 {codes, sw1, sw2}。"""
    sess = _session()
    try:
        payload = _get_json(sess, "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodes", {}, timeout=20)
    except Exception:
        return {}
    sw1: list[tuple[str, str]] = []
    sw2: list[tuple[str, str]] = []
    sina: list[tuple[str, str]] = []

    def walk(node, path):
        if not isinstance(node, list):
            return
        if len(node) >= 3 and isinstance(node[0], str) and isinstance(node[2], str):
            nid = str(node[2])
            parent = path[-1] if path else ""
            if nid.startswith("sw1_") and parent == "申万一级":
                sw1.append((node[0], nid))
                return
            if nid.startswith("sw2_") and parent == "申万二级":
                sw2.append((node[0], nid))
                return
            if nid.startswith("new_") and parent == "新浪行业":
                sina.append((node[0], nid))
                return
        for item in node:
            if isinstance(item, list):
                nxt = path + [node[0]] if isinstance(node[0], str) else path
                walk(item, nxt)

    walk(payload, [])
    mapping: dict[str, str] = {}
    map_sw1: dict[str, str] = {}
    map_sw2: dict[str, str] = {}

    def fill(nodes: list[tuple[str, str]], target: dict[str, str], overwrite: bool = False) -> None:
        for name, node_id in nodes:
            try:
                rows = fetch_node(sess, node_id, page_size=80)
            except Exception:
                continue
            for rec in rows:
                code = ts_code(str(rec.get("code") or rec.get("symbol") or ""))
                if code and (overwrite or code not in target):
                    target[code] = name
            time.sleep(0.04)

    fill(sw1, map_sw1, overwrite=True)
    fill(sw2, map_sw2, overwrite=True)
    fill(sina, mapping, overwrite=False)
    for code, name in map_sw1.items():
        mapping.setdefault(code, name)
    for code, name in map_sw2.items():
        mapping[code] = name
    return {"codes": mapping, "sw1": map_sw1, "sw2": map_sw2}


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


def _em_pool(url: str, date: str) -> list[dict]:
    sess = _session()
    sess.headers["Referer"] = "https://quote.eastmoney.com/"
    ymd = str(date or "").replace("-", "")[:8]
    try:
        payload = _get_json(
            sess,
            url,
            {
                "ut": "7eea3edcaed734bea9cbfc24409ed989",
                "dpt": "wz.ztzt",
                "Pageindex": 0,
                "pagesize": 200,
                "sort": "fbt:asc",
                "date": ymd,
            },
            timeout=15,
        )
    except Exception:
        return []
    rows = ((payload or {}).get("data") or {}).get("pool") or []
    out = []
    for rec in rows:
        if not isinstance(rec, dict):
            continue
        code = ts_code(str(rec.get("c") or rec.get("code") or ""))
        if not code:
            continue
        out.append(
            {
                "code": code,
                "name": str(rec.get("n") or rec.get("name") or code),
                "lbc": rec.get("lbc") or rec.get("height") or 0,
                "zbc": rec.get("zbc") or rec.get("zbtimes") or 0,
                "industry": rec.get("hybk") or rec.get("industry") or "",
            }
        )
    return out


def fetch_limit_pool(date: str) -> list[dict]:
    return _em_pool("https://push2ex.eastmoney.com/getTopicZTPool", date)


def fetch_fail_pool(date: str) -> list[dict]:
    return _em_pool("https://push2ex.eastmoney.com/getTopicZBPool", date)


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


EM_CLIST_URLS = (
    "https://push2delay.eastmoney.com/api/qt/clist/get",
    "https://push2.eastmoney.com/api/qt/clist/get",
    "https://82.push2.eastmoney.com/api/qt/clist/get",
)
EM_CLIST_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"


def fetch_em_clist(log=None) -> list[dict]:
    """东财全 A 列表：最新价 / 动态市盈 / 流通市值 / 成交额。不按 300 亿截断。"""
    talk = log or (lambda _m: None)
    sess = _session()
    sess.headers["Referer"] = "https://quote.eastmoney.com/"
    last_exc = None
    for url in EM_CLIST_URLS:
        codes: dict[str, dict] = {}
        total = None
        try:
            for page in range(1, 90):
                payload = _get_json(
                    sess,
                    url,
                    {
                        "pn": page,
                        "pz": 100,
                        "po": 1,
                        "np": 1,
                        "fltt": 2,
                        "invt": 2,
                        "fid": "f12",
                        "fs": EM_CLIST_FS,
                        "fields": "f12,f14,f2,f3,f9,f20,f21,f6,f15,f16,f17,f18",
                    },
                    timeout=20,
                )
                data = (payload or {}).get("data") or {}
                if total is None:
                    total = int(data.get("total") or 0)
                    talk(f"东财列表 {total} 只 · {url.split('/')[2]}")
                diff = data.get("diff") or []
                if isinstance(diff, dict):
                    diff = list(diff.values())
                if not diff:
                    break
                for rec in diff:
                    if not isinstance(rec, dict):
                        continue
                    code = ts_code(str(rec.get("f12") or ""))
                    if code:
                        codes[code] = rec
                if total and len(codes) >= total:
                    break
                if len(diff) < 100:
                    break
                time.sleep(0.04)
            if len(codes) >= 200:
                talk(f"东财列表完成 {len(codes)} 只")
                return list(codes.values())
            last_exc = RuntimeError(f"{url} 仅 {len(codes)} 只")
        except Exception as exc:
            last_exc = exc
            talk(f"东财列表失败 {url.split('/')[2]}：{exc}")
            continue
    talk(f"东财列表不可用：{last_exc}")
    return []


def quotes_from_em_clist(rows: list[dict]) -> dict:
    asof = expected_close_date().isoformat()
    codes = {}
    for rec in rows or []:
        code = ts_code(str(rec.get("f12") or rec.get("code") or ""))
        if not code:
            continue
        pe = _fnum(rec.get("f9"))
        circ = _fnum(rec.get("f21"))
        amount = _fnum(rec.get("f6"))
        close = _fnum(rec.get("f2"))
        float_mcap_yi = circ / YI if circ and circ > 0 else None
        amount_yi = amount / YI if amount and amount > 0 else None
        codes[code] = {
            "code": code,
            "name": str(rec.get("f14") or rec.get("name") or code),
            "pe": _round_or_none(pe),
            "amount": amount if amount and amount > 0 else None,
            "amount_yi": round(amount_yi, 2) if amount_yi is not None else None,
            "float_mcap_yi": round(float_mcap_yi, 2) if float_mcap_yi is not None else None,
            "close": close,
            "open": _fnum(rec.get("f17")),
            "high": _fnum(rec.get("f15")),
            "low": _fnum(rec.get("f16")),
            "preclose": _fnum(rec.get("f18")),
            "pct": _round_or_none(_fnum(rec.get("f3")), 3),
            "trade_date": asof,
        }
    return {"trade_date": asof, "source": "eastmoney-clist", "codes": codes}


def quotes_from_spot(spot: list[dict]) -> dict:
    asof = expected_close_date().isoformat()
    codes = {}
    for rec in spot or []:
        code = ts_code(str(rec.get("code") or rec.get("symbol") or ""))
        if not code:
            continue
        amount = _num(rec, "amount")
        nmc = _num(rec, "nmc")
        pe = _num(rec, "per", "pe", "pe_ttm")
        amount_yi = amount / YI if amount is not None and amount > 0 else None
        float_mcap_yi = nmc / 10_000.0 if nmc is not None and nmc > 0 else None
        if pe is not None and pe != pe:
            pe = None
        close = _num(rec, "trade")
        preclose = _num(rec, "settlement", "pretrade", "preclose")
        high = _num(rec, "high")
        low = _num(rec, "low")
        open_px = _num(rec, "open")
        pct = _num(rec, "changepercent", "pct")
        turnover = _num(rec, "turnoverratio", "turnover")
        amp = None
        if high is not None and low is not None and preclose:
            amp = (high - low) / preclose * 100.0
        codes[code] = {
            "code": code,
            "name": str(rec.get("name") or code),
            "pe": _round_or_none(pe),
            "amount": amount if amount is not None and amount > 0 else None,
            "amount_yi": round(amount_yi, 2) if amount_yi is not None else None,
            "float_mcap_yi": round(float_mcap_yi, 2) if float_mcap_yi is not None else None,
            "close": close,
            "open": open_px,
            "high": high,
            "low": low,
            "preclose": preclose,
            "pct": _round_or_none(pct, 3),
            "turnover": _round_or_none(turnover, 3),
            "amplitude": _round_or_none(amp, 3),
            "trade_date": asof,
        }
    return {"trade_date": asof, "source": "sina", "codes": codes}


def _fmt_ymd(raw: str) -> str:
    raw = str(raw or "").replace("-", "")
    if len(raw) < 8:
        return ""
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"


def _fnum(val):
    try:
        if val is None:
            return None
        num = float(val)
        if num != num:
            return None
        return num
    except (TypeError, ValueError):
        return None


def _quotes_from_tushare(log=None) -> dict:
    """PE / 流通市值 / 成交额。Tushare daily_basic 一次拉全市场，不按 300 亿截断。"""
    talk = log or (lambda _m: None)
    from ..store import load_settings

    token = (load_settings().get("tushare_token") or "").strip()
    if not token:
        return {"trade_date": "", "source": "tushare", "codes": {}}
    try:
        import tushare as ts  # type: ignore

        pro = ts.pro_api(token)
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=20)).strftime("%Y%m%d")
        cal = pro.trade_cal(exchange="SSE", start_date=start, end_date=end, is_open="1")
        if cal is None or cal.empty:
            talk("Tushare 交易日历为空")
            return {"trade_date": "", "source": "tushare", "codes": {}}
        trade_date = str(cal["cal_date"].iloc[-1])
        talk(f"Tushare 快照日 {_fmt_ymd(trade_date)}")
        basic = pro.stock_basic(exchange="", list_status="L", fields="ts_code,symbol,name")
        daily_basic = pro.daily_basic(
            trade_date=trade_date, fields="ts_code,trade_date,close,circ_mv,pe,pe_ttm"
        )
        daily = pro.daily(
            trade_date=trade_date, fields="ts_code,open,high,low,close,vol,amount"
        )
        if daily_basic is None or daily_basic.empty:
            talk("Tushare daily_basic 为空")
            return {"trade_date": "", "source": "tushare", "codes": {}}
        basic = basic.set_index("ts_code") if basic is not None and not basic.empty else None
        daily_basic = daily_basic.set_index("ts_code")
        if daily is not None and not daily.empty:
            daily = daily.set_index("ts_code")
            merged = daily_basic.join(daily[["amount", "vol", "open", "high", "low"]], how="left")
        else:
            merged = daily_basic
        if basic is not None:
            merged = merged.join(basic[["symbol", "name"]], how="left")
        asof = _fmt_ymd(trade_date)
        codes = {}
        for ts_full, rec in merged.iterrows():
            code = ts_code(str(rec.get("symbol") or str(ts_full).split(".")[0]))
            if not code:
                continue
            pe = _fnum(rec.get("pe_ttm"))
            if pe is None:
                pe = _fnum(rec.get("pe"))
            circ = _fnum(rec.get("circ_mv"))
            amount = _fnum(rec.get("amount"))
            close = _fnum(rec.get("close"))
            float_mcap_yi = circ / 10_000.0 if circ and circ > 0 else None
            amount_yi = amount / 100_000.0 if amount and amount > 0 else None
            codes[code] = {
                "code": code,
                "name": str(rec.get("name") or code),
                "pe": round(pe, 3) if pe is not None else None,
                "amount": amount * 1000.0 if amount and amount > 0 else None,
                "amount_yi": round(amount_yi, 2) if amount_yi is not None else None,
                "float_mcap_yi": round(float_mcap_yi, 2) if float_mcap_yi is not None else None,
                "close": close,
                "open": _fnum(rec.get("open")),
                "high": _fnum(rec.get("high")),
                "low": _fnum(rec.get("low")),
                "trade_date": asof,
            }
        talk(f"Tushare 快照 {len(codes)} 只")
        return {"trade_date": asof, "source": "tushare", "codes": codes}
    except Exception as exc:
        talk(f"Tushare 快照失败：{exc}")
        return {"trade_date": "", "source": "tushare", "codes": {}}


def apply_quote_fields(meta: dict, quotes: dict | None = None) -> dict:
    """把快照里的 PE / 流通市值补进单票 meta。不编造。"""
    out = dict(meta or {})
    code = ts_code(str(out.get("code") or ""))
    qmap = quotes if quotes is not None else load_quotes()
    q = (qmap or {}).get(code) or {}
    if q.get("pe") is not None and out.get("pe") is None:
        out["pe"] = q["pe"]
    if q.get("float_mcap_yi") is not None and (
        out.get("float_mcap_yi") is None or float(out.get("float_mcap_yi") or 0) <= 0
    ):
        out["float_mcap_yi"] = q["float_mcap_yi"]
    if q.get("amount_yi") is not None and out.get("amount_yi") is None:
        out["amount_yi"] = q["amount_yi"]
    if q.get("name") and (not out.get("name") or out.get("name") == code):
        out["name"] = q["name"]
    return out


def hydrate_universe(universe: list[dict] | None = None, log=None) -> list[dict]:
    """全 A 底池 + 快照 PE/市值。规则池子由各 RULES 自己记排除，这里不截断。"""
    talk = log or (lambda _m: None)
    quotes = ensure_quotes(log=talk)
    items = list(universe) if universe is not None else list(load_universe())
    if not items:
        from .pool import build_universe_from_csv

        talk("universe 空，改用本地日线重建全 A 底池")
        items, _ = build_universe_from_csv()
    asof = expected_close_date().isoformat()
    funnel = {
        "listed": 0,
        "quote_rows": len(quotes or {}),
        "non_st": 0,
        "price_ok": 0,
        "mcap_ok": 0,
        "amount_ok": 0,
        "pool": 0,
        "preferred": 0,
        "pe_ok": 0,
        "trade_date": asof,
        "source": "quotes+csv",
        "rules": {
            "底池": "本地日线全 A，不按单条规则截断",
            "流通市值": f"RULES 入池 ≥ {POOL_FLOAT_MCAP_YI:.0f} 亿",
            "日成交额": f"RULES 入池 ≥ {POOL_AMOUNT_YI:.0f} 亿",
        },
    }
    out = []
    for item in items:
        meta = apply_quote_fields(item, quotes)
        code = ts_code(str(meta.get("code") or ""))
        if not code:
            continue
        name = str(meta.get("name") or code)
        st = bool(meta.get("is_st")) or is_st_name(name)
        meta["is_st"] = st
        close = _fnum(meta.get("close"))
        if close is None:
            close = _fnum((quotes or {}).get(code, {}).get("close"))
            if close is not None:
                meta["close"] = close
        mcap = _fnum(meta.get("float_mcap_yi"))
        amount_yi = _fnum(meta.get("amount_yi"))
        pe = _fnum(meta.get("pe"))
        funnel["listed"] += 1
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
            if meta.get("index_member"):
                funnel["preferred"] += 1
        out.append(meta)
    funnel["listed"] = len(out)
    if out:
        save_universe(out)
        save_pool_snapshot(funnel)
    talk(f"底池 {len(out)} 只 · 快照 {len(quotes or {})} · RULES 入池 {funnel['pool']}")
    return out


def ensure_quotes(log=None, force: bool = False) -> dict:
    """All-A quote map for PE / 成交额 / 流通市值. Not the RULES.md 300亿池。"""
    talk = log or (lambda _m: None)
    asof = expected_close_date().isoformat()
    cached = load_quotes()
    from ..store import read_json
    from ..config import QUOTES_PATH

    store = read_json(QUOTES_PATH, {}) if QUOTES_PATH.exists() else {}
    if (
        not force
        and isinstance(store, dict)
        and store.get("trade_date") == asof
        and isinstance(cached, dict)
        and len(cached) >= 200
    ):
        return cached
    talk("正在补全市盈 / 成交额 / 流通市值（东财列表 / Tushare / 新浪）…")
    payload = {"trade_date": "", "source": "", "codes": {}}
    em_rows = fetch_em_clist(log=talk)
    if em_rows:
        payload = quotes_from_em_clist(em_rows)
    if len(payload.get("codes") or {}) < 200:
        ts_payload = _quotes_from_tushare(log=talk)
        if len(ts_payload.get("codes") or {}) > len(payload.get("codes") or {}):
            payload = ts_payload
    if len(payload.get("codes") or {}) < 200:
        talk("改拉新浪全市场")
        spot = fetch_spot(log=talk)
        sina_payload = quotes_from_spot(spot)
        if len(sina_payload.get("codes") or {}) > len(payload.get("codes") or {}):
            payload = sina_payload
    save_quotes(payload)
    talk(f"行情快照 {len(payload.get('codes') or {})} 只 · 最新 {payload.get('trade_date')}")
    return payload.get("codes") or load_quotes() or {}


def build_pool(log=None) -> tuple[list[dict], dict]:
    talk = log or (lambda _m: None)
    spot = fetch_spot(log=talk)
    save_quotes(quotes_from_spot(spot))
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
        "trade_date": expected_close_date().isoformat(),
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
        if amount_yi is not None and amount_yi <= 0:
            amount_yi = None
        if float_mcap_yi is not None and float_mcap_yi <= 0:
            float_mcap_yi = None
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
        members = []
        if code in hs300:
            members.append("沪深300")
        if code in sse50:
            members.append("上证50")
        if code in hgt:
            members.append("沪股通")
        if passes_pool(close=close, amount_yi=amount_yi, float_mcap_yi=float_mcap_yi, is_st=st, pe=pe):
            funnel["pool"] += 1
            if members:
                funnel["preferred"] += 1
        pool.append(
            {
                "code": code,
                "ts_code": f"{code}.{suffix_for(code)}",
                "name": name,
                "float_mcap_yi": round(float_mcap_yi, 2) if float_mcap_yi is not None else None,
                "amount_yi": round(amount_yi, 2) if amount_yi is not None else None,
                "close": close,
                "pe": _round_or_none(pe),
                "is_st": st,
                "index_member": members,
                "tags": [],
                "trade_date": funnel["trade_date"],
                "source": "sina",
            }
        )
    pool = sort_pool(pool)
    talk(f"底池 {len(pool)} 只 · RULES 入池 {funnel['pool']}（优先样本 {funnel['preferred']}）")
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
        last_date = existing[-1]["date"] if existing else ""
        if existing and len(existing) >= 40 and last_date > expect:
            skip += 1
            if progress:
                progress(i, total)
            continue
        refresh_only = bool(existing and len(existing) >= 40 and last_date >= expect)
        try:
            rows, used = fetch_kline_with_source(code, limit=8 if refresh_only else 180)
            item["bar_source"] = used
            time.sleep(0.04)
        except Exception as exc:
            fail += 1
            talk(f"{code} 日线失败：{exc}")
            if progress:
                progress(i, total)
            continue
        rows = [r for r in rows if not is_weekend_date(r.get("date"))]
        if not rows:
            fail += 1
            if progress:
                progress(i, total)
            continue
        if refresh_only:
            by_date = {str(r.get("date"))[:10]: dict(r) for r in existing}
            for row in rows:
                d = str(row.get("date") or "")[:10]
                if d:
                    by_date[d] = dict(row)
            merged = [by_date[k] for k in sorted(by_date)]
            save_bars_csv(code, merged, name=item.get("name"))
            last_dates.append(merged[-1]["date"])
            ok += 1
            if progress:
                progress(i, total)
            continue
        if len(rows) < 40:
            fail += 1
            if progress:
                progress(i, total)
            continue
        save_bars_csv(code, rows, name=item.get("name"))
        last_dates.append(rows[-1]["date"])
        ok += 1
        if progress:
            progress(i, total)
        if i % 25 == 0:
            talk(f"日线已写 {ok}，跳过 {skip}，失败 {fail} / {total}")
    last_bar = asof_date(max(last_dates) if last_dates else "")
    return {"ok": ok, "skip": skip, "fail": fail, "total": total, "last_bar": last_bar}
