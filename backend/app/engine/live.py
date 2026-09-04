"""Pull confirmed daily bars and rebuild the RULES §3 pool.

Tushare first (token). If a Tushare call is refused, fall back to East Money
via AKShare. CSV on disk is the local cache of those confirmed closes.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from typing import Callable

_run_lock = threading.Lock()

from ..config import CSV_DIR, POOL_AMOUNT_YI, POOL_FLOAT_MCAP_YI, POOL_MIN_PRICE
from ..store import (
    load_settings,
    save_pool_snapshot,
    save_settings,
    save_sync_status,
    save_universe,
)
from .bars import csv_path_for, load_bars, save_bars_csv, suffix_for, ts_code
from .pool import is_st_name, passes_pool, sort_pool

YI = 100_000_000.0
# Tushare daily.amount is 千元; daily_basic.circ_mv is 万元.
TUSHARE_AMOUNT_TO_YI = 100_000.0
TUSHARE_MCAP_TO_YI = 10_000.0


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _status(**kwargs) -> dict:
    current = {
        "state": "running",
        "message": "",
        "step": "",
        "funnel": {},
        "bars_done": 0,
        "bars_total": 0,
        "started_at": "",
        "finished_at": "",
        "source": "",
        "trade_date": "",
        "error": "",
    }
    current.update(kwargs)
    save_sync_status(current)
    return current


def _fmt_date(raw: str) -> str:
    raw = str(raw).replace("-", "")
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"


def _pro(token: str):
    import tushare as ts  # type: ignore

    return ts.pro_api(token)


def latest_open_day(token: str) -> str:
    pro = _pro(token)
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=20)).strftime("%Y%m%d")
    cal = pro.trade_cal(exchange="SSE", start_date=start, end_date=end, is_open="1")
    if cal is None or cal.empty:
        raise RuntimeError("Tushare 交易日历为空，证据不足")
    return str(cal["cal_date"].iloc[-1])


def _index_members(pro, index_code: str, end: str) -> set[str]:
    start = (datetime.strptime(end, "%Y%m%d") - timedelta(days=40)).strftime("%Y%m%d")
    try:
        frame = pro.index_weight(index_code=index_code, start_date=start, end_date=end)
    except Exception:
        return set()
    if frame is None or frame.empty:
        return set()
    latest = str(frame["trade_date"].max())
    col = "con_code" if "con_code" in frame.columns else "ts_code"
    return {str(x) for x in frame.loc[frame["trade_date"].astype(str) == latest, col].tolist()}


def _hs_members(pro) -> set[str]:
    out: set[str] = set()
    try:
        frame = pro.hs_const(hs_type="SH")
    except Exception:
        return out
    if frame is None or frame.empty:
        return out
    if "is_new" in frame.columns:
        frame = frame[frame["is_new"].astype(str) == "1"]
    out.update(str(x) for x in frame["ts_code"].tolist())
    return out


def build_pool_tushare(token: str, log: Callable[[str], None] | None = None) -> tuple[list[dict], dict]:
    talk = log or (lambda _m: None)
    pro = _pro(token)
    trade_date = latest_open_day(token)
    talk(f"确认收盘日 { _fmt_date(trade_date) }")

    basic = pro.stock_basic(
        exchange="",
        list_status="L",
        fields="ts_code,symbol,name,market,list_status",
    )
    if basic is None or basic.empty:
        raise RuntimeError("Tushare 股票列表为空")
    daily_basic = pro.daily_basic(
        trade_date=trade_date,
        fields="ts_code,trade_date,close,circ_mv,total_mv,pe,pe_ttm",
    )
    daily = pro.daily(
        trade_date=trade_date,
        fields="ts_code,trade_date,open,high,low,close,vol,amount",
    )
    if daily_basic is None or daily_basic.empty or daily is None or daily.empty:
        raise RuntimeError("Tushare 当日行情为空，积分或权限可能不够")

    hs = _hs_members(pro)
    hs300 = _index_members(pro, "000300.SH", trade_date)
    sse50 = _index_members(pro, "000016.SH", trade_date)
    talk(f"优先样本核对：沪股通 {len(hs)} / 沪深300 {len(hs300)} / 上证50 {len(sse50)}")

    basic = basic.set_index("ts_code")
    daily_basic = daily_basic.set_index("ts_code")
    daily = daily.set_index("ts_code")
    merged = daily_basic.join(daily[["amount", "vol"]], how="inner", rsuffix="_d")
    merged = merged.join(basic[["symbol", "name", "market"]], how="left")

    funnel = {
        "listed": int(len(basic)),
        "quote_rows": int(len(merged)),
        "non_st": 0,
        "price_ok": 0,
        "mcap_ok": 0,
        "amount_ok": 0,
        "pool": 0,
        "preferred": 0,
        "trade_date": _fmt_date(trade_date),
        "source": "tushare",
        "rules": {
            "流通市值": f"≥ {POOL_FLOAT_MCAP_YI:.0f} 亿",
            "日成交额": f"≥ {POOL_AMOUNT_YI:.0f} 亿",
            "股价": f"≥ {POOL_MIN_PRICE:.0f} 元",
            "动态市盈": "> 0（亏损票排除）",
            "ST": "非 ST、非 *ST",
            "优先样本": "沪股通 / 沪深300 / 上证50；其他过池股也保留",
        },
    }
    def _f(val):
        try:
            if val is None:
                return None
            num = float(val)
            if num != num:
                return None
            return num
        except (TypeError, ValueError):
            return None

    pool: list[dict] = []
    for ts, rec in merged.iterrows():
        name = str(rec.get("name") or ts)
        st = is_st_name(name)
        close = _f(rec.get("close"))
        circ = _f(rec.get("circ_mv"))
        amount = _f(rec.get("amount"))
        float_mcap_yi = circ / TUSHARE_MCAP_TO_YI if circ is not None else None
        amount_yi = amount / TUSHARE_AMOUNT_TO_YI if amount is not None else None
        if not st:
            funnel["non_st"] += 1
        if close is not None and close >= POOL_MIN_PRICE:
            funnel["price_ok"] += 1
        if float_mcap_yi is not None and float_mcap_yi >= POOL_FLOAT_MCAP_YI:
            funnel["mcap_ok"] += 1
        if amount_yi is not None and amount_yi >= POOL_AMOUNT_YI:
            funnel["amount_ok"] += 1
        if not passes_pool(close=close, amount_yi=amount_yi, float_mcap_yi=float_mcap_yi, is_st=st):
            continue
        members = []
        code_full = str(ts)
        if code_full in hs300:
            members.append("沪深300")
        if code_full in sse50:
            members.append("上证50")
        if code_full in hs:
            members.append("沪股通")
        symbol = ts_code(str(rec.get("symbol") or code_full.split(".")[0]))
        pe = _f(rec.get("pe_ttm"))
        if pe is None:
            pe = _f(rec.get("pe"))
        item = {
            "code": symbol,
            "ts_code": code_full,
            "name": name,
            "float_mcap_yi": round(float_mcap_yi, 2),
            "amount_yi": round(amount_yi, 2),
            "close": close,
            "pe": round(pe, 3) if pe is not None else None,
            "is_st": False,
            "index_member": members,
            "tags": [],
            "trade_date": _fmt_date(trade_date),
            "source": "tushare",
        }
        pool.append(item)
        funnel["pool"] += 1
        if members:
            funnel["preferred"] += 1

    pool = sort_pool(pool)
    talk(f"RULES §3 入池 {len(pool)} 只（优先样本 {funnel['preferred']}）")
    return pool, funnel


def pull_history_tushare(token: str, pool: list[dict], trade_date: str, log: Callable[[str], None] | None = None, progress: Callable[[int, int], None] | None = None) -> dict:
    talk = log or (lambda _m: None)
    pro = _pro(token)
    start = (datetime.strptime(trade_date.replace("-", ""), "%Y%m%d") - timedelta(days=420)).strftime("%Y%m%d")
    end = trade_date.replace("-", "")
    ok = skip = fail = 0
    total = len(pool)
    for i, item in enumerate(pool, start=1):
        code = item["code"]
        ts_full = item.get("ts_code") or f"{code}.{suffix_for(code)}"
        existing = load_bars(code)
        if existing and existing[-1]["date"] >= _fmt_date(end):
            skip += 1
            if progress:
                progress(i, total)
            continue
        try:
            frame = pro.daily(ts_code=ts_full, start_date=start, end_date=end)
            time.sleep(0.12)
        except Exception as exc:
            fail += 1
            talk(f"{code} 日线失败：{exc}")
            if progress:
                progress(i, total)
            continue
        if frame is None or frame.empty:
            fail += 1
            if progress:
                progress(i, total)
            continue
        frame = frame.sort_values("trade_date")
        rows = []
        for _, rec in frame.iterrows():
            rows.append(
                {
                    "date": _fmt_date(str(rec["trade_date"])),
                    "open": float(rec["open"]),
                    "high": float(rec["high"]),
                    "low": float(rec["low"]),
                    "close": float(rec["close"]),
                    "volume": float(rec["vol"]) * 100.0,
                    "amount": float(rec["amount"]) * 1000.0,
                }
            )
        save_bars_csv(code, rows, name=item.get("name"))
        ok += 1
        if progress:
            progress(i, total)
        if i % 20 == 0:
            talk(f"日线已写 {ok}，跳过 {skip}，失败 {fail} / {total}")
    return {"ok": ok, "skip": skip, "fail": fail, "total": total}


def build_pool_akshare(log: Callable[[str], None] | None = None) -> tuple[list[dict], dict]:
    talk = log or (lambda _m: None)
    import akshare as ak  # type: ignore

    talk("Tushare 池子不可用，改用东方财富快照（AKShare）")
    spot = ak.stock_zh_a_spot_em()
    if spot is None or spot.empty:
        raise RuntimeError("东方财富快照为空")
    hs300 = set()
    sse50 = set()
    try:
        hs300 = set(ak.index_stock_cons(symbol="000300")["品种代码"].astype(str).str.zfill(6).tolist())
    except Exception:
        try:
            hs300 = set(ak.index_stock_cons_csindex(symbol="000300")["成分券代码"].astype(str).str.zfill(6).tolist())
        except Exception:
            hs300 = set()
    try:
        sse50 = set(ak.index_stock_cons(symbol="000016")["品种代码"].astype(str).str.zfill(6).tolist())
    except Exception:
        sse50 = set()

    funnel = {
        "listed": int(len(spot)),
        "quote_rows": int(len(spot)),
        "non_st": 0,
        "price_ok": 0,
        "mcap_ok": 0,
        "amount_ok": 0,
        "pool": 0,
        "preferred": 0,
        "trade_date": datetime.now().strftime("%Y-%m-%d"),
        "source": "akshare",
        "rules": {
            "流通市值": f"≥ {POOL_FLOAT_MCAP_YI:.0f} 亿",
            "日成交额": f"≥ {POOL_AMOUNT_YI:.0f} 亿",
            "股价": f"≥ {POOL_MIN_PRICE:.0f} 元",
            "动态市盈": "> 0（亏损票排除）",
            "ST": "非 ST、非 *ST",
            "优先样本": "沪深300 / 上证50（沪股通名单此次未取到则不标）",
        },
    }
    pool: list[dict] = []
    for _, rec in spot.iterrows():
        code = ts_code(str(rec.get("代码") or ""))
        name = str(rec.get("名称") or code)
        st = is_st_name(name)
        close = rec.get("最新价")
        close = float(close) if close == close else None
        mcap = rec.get("流通市值")
        amt = rec.get("成交额")
        float_mcap_yi = float(mcap) / YI if mcap == mcap else None
        amount_yi = float(amt) / YI if amt == amt else None
        if not st:
            funnel["non_st"] += 1
        if close is not None and close >= POOL_MIN_PRICE:
            funnel["price_ok"] += 1
        if float_mcap_yi is not None and float_mcap_yi >= POOL_FLOAT_MCAP_YI:
            funnel["mcap_ok"] += 1
        if amount_yi is not None and amount_yi >= POOL_AMOUNT_YI:
            funnel["amount_ok"] += 1
        if not passes_pool(close=close, amount_yi=amount_yi, float_mcap_yi=float_mcap_yi, is_st=st):
            continue
        members = []
        if code in hs300:
            members.append("沪深300")
        if code in sse50:
            members.append("上证50")
        pe = None
        for key in ("市盈率-动态", "市盈率-TTM", "市盈率", "pe", "pe_ttm"):
            if key in rec.index:
                raw = rec.get(key)
                try:
                    val = float(raw)
                except (TypeError, ValueError):
                    continue
                if val == val:
                    pe = val
                    break
        pool.append(
            {
                "code": code,
                "ts_code": f"{code}.{suffix_for(code)}",
                "name": name,
                "float_mcap_yi": round(float_mcap_yi, 2) if float_mcap_yi is not None else None,
                "amount_yi": round(amount_yi, 2) if amount_yi is not None else None,
                "close": close,
                "pe": round(pe, 3) if pe is not None else None,
                "is_st": False,
                "index_member": members,
                "tags": [],
                "trade_date": funnel["trade_date"],
                "source": "akshare",
            }
        )
        funnel["pool"] += 1
        if members:
            funnel["preferred"] += 1
    pool = sort_pool(pool)
    talk(f"RULES §3 入池 {len(pool)} 只")
    return pool, funnel


def pull_history_akshare(pool: list[dict], log: Callable[[str], None] | None = None, progress: Callable[[int, int], None] | None = None) -> dict:
    talk = log or (lambda _m: None)
    import akshare as ak  # type: ignore

    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=420)).strftime("%Y%m%d")
    ok = skip = fail = 0
    total = len(pool)
    for i, item in enumerate(pool, start=1):
        code = item["code"]
        existing = load_bars(code)
        if existing and len(existing) >= 60:
            last = existing[-1]["date"].replace("-", "")
            if last >= (datetime.now() - timedelta(days=4)).strftime("%Y%m%d"):
                skip += 1
                if progress:
                    progress(i, total)
                continue
        try:
            frame = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start, end_date=end, adjust="")
            time.sleep(0.15)
        except Exception as exc:
            fail += 1
            talk(f"{code} 日线失败：{exc}")
            if progress:
                progress(i, total)
            continue
        if frame is None or frame.empty:
            fail += 1
            if progress:
                progress(i, total)
            continue
        rows = []
        for _, rec in frame.iterrows():
            vol = float(rec.get("成交量") or 0)
            # 东方财富 hist 成交量多为手
            rows.append(
                {
                    "date": str(rec["日期"])[:10],
                    "open": float(rec["开盘"]),
                    "high": float(rec["最高"]),
                    "low": float(rec["最低"]),
                    "close": float(rec["收盘"]),
                    "volume": vol * 100.0,
                    "amount": float(rec.get("成交额") or 0),
                }
            )
        save_bars_csv(code, rows, name=item.get("name"))
        ok += 1
        if progress:
            progress(i, total)
        if i % 20 == 0:
            talk(f"日线已写 {ok}，跳过 {skip}，失败 {fail} / {total}")
    return {"ok": ok, "skip": skip, "fail": fail, "total": total}


def sync_live(force_bars: bool = False) -> dict:
    if not _run_lock.acquire(blocking=False):
        return {"state": "running", "message": "同步已在进行"}
    try:
        return _sync_live(force_bars=force_bars)
    finally:
        _run_lock.release()


def _sync_live(force_bars: bool = False) -> dict:
    settings = load_settings()
    token = (settings.get("tushare_token") or "").strip()
    started = _now()
    messages: list[str] = []

    def log(msg: str) -> None:
        messages.append(msg)
        _status(
            state="running",
            message=msg,
            log=messages[-12:],
            started_at=started,
            bars_done=status_holder.get("bars_done", 0),
            bars_total=status_holder.get("bars_total", 0),
            step=status_holder.get("step", ""),
            funnel=status_holder.get("funnel", {}),
            source=status_holder.get("source", ""),
            trade_date=status_holder.get("trade_date", ""),
        )

    status_holder: dict = {"bars_done": 0, "bars_total": 0, "step": "pool", "funnel": {}, "source": "", "trade_date": ""}
    _status(state="running", message="开始按 RULES §3 筛池", started_at=started, step="pool")

    pool: list[dict] = []
    funnel: dict = {}
    source = ""
    err = None
    if token:
        try:
            pool, funnel = build_pool_tushare(token, log=log)
            source = "tushare"
        except Exception as exc:
            err = str(exc)
            log(f"Tushare 池子不可用（{exc}），改用新浪/腾讯/东财")
    if not pool:
        try:
            from .eastmoney import build_pool as build_pool_em

            pool, funnel = build_pool_em(log=log)
            source = funnel.get("source") or "eastmoney"
            err = None
        except Exception as exc:
            log(f"东方财富失败：{exc}，再试 AKShare")
            try:
                pool, funnel = build_pool_akshare(log=log)
                source = "akshare"
                err = None
            except Exception as exc2:
                finished = {
                    "state": "error",
                    "message": f"真实行情不可用：{err or exc2}",
                    "error": str(err or exc2),
                    "started_at": started,
                    "finished_at": _now(),
                    "log": messages[-12:],
                }
                save_sync_status(finished)
                return finished

    status_holder["funnel"] = funnel
    status_holder["source"] = source
    status_holder["trade_date"] = funnel.get("trade_date") or ""
    status_holder["step"] = "bars"
    status_holder["bars_total"] = len(pool)
    save_universe(pool)
    save_pool_snapshot(funnel)
    log(f"已写入股池 {len(pool)} 只，开始补日线")

    def progress(done: int, total: int) -> None:
        status_holder["bars_done"] = done
        status_holder["bars_total"] = total
        _status(
            state="running",
            message=f"日线 {done}/{total}",
            step="bars",
            bars_done=done,
            bars_total=total,
            funnel=funnel,
            source=source,
            trade_date=funnel.get("trade_date") or "",
            started_at=started,
            log=messages[-12:],
        )

    if force_bars:
        for item in pool:
            path = csv_path_for(item["code"])
            if path.exists():
                path.unlink()

    from .eastmoney import pull_history as pull_history_chain

    if source == "tushare" and token:
        try:
            bars_stat = pull_history_tushare(token, pool, funnel["trade_date"].replace("-", ""), log=log, progress=progress)
        except Exception as exc:
            log(f"Tushare 日线失败：{exc}，改腾讯/新浪/东财")
            bars_stat = pull_history_chain(pool, log=log, progress=progress)
            source = "tencent/sina/eastmoney"
    else:
        log("日线链：腾讯 → 新浪 → 东财")
        bars_stat = pull_history_chain(pool, log=log, progress=progress)
        source = source or "tencent/sina/eastmoney"

    if bars_stat.get("last_bar"):
        funnel["trade_date"] = bars_stat["last_bar"]
        for item in pool:
            item["trade_date"] = funnel["trade_date"]
        save_universe(pool)
        save_pool_snapshot(funnel)

    label = f"真实行情已连接 · {source} 确认收盘 {funnel.get('trade_date')}"
    save_settings(
        {
            "data_source": source,
            "data_label": label,
            "last_trade_date": funnel.get("trade_date") or "",
        }
    )
    done = {
        "state": "done",
        "message": f"池子 {len(pool)} 只，日线写入 {bars_stat.get('ok')}，跳过 {bars_stat.get('skip')}，失败 {bars_stat.get('fail')}",
        "step": "done",
        "funnel": funnel,
        "bars": bars_stat,
        "bars_done": bars_stat.get("total", 0),
        "bars_total": bars_stat.get("total", 0),
        "source": source,
        "trade_date": funnel.get("trade_date"),
        "started_at": started,
        "finished_at": _now(),
        "log": messages[-12:],
        "pool_size": len(pool),
        "preferred": funnel.get("preferred"),
    }
    save_sync_status(done)
    return done


def pull_one(code: str) -> dict:
    from .eastmoney import fetch_kline_with_source

    code = ts_code(code)
    try:
        rows, used = fetch_kline_with_source(code, limit=180)
        if len(rows) < 40:
            return {"ok": False, "message": f"{used} 日线不足，证据不足"}
        path = save_bars_csv(code, rows)
        return {"ok": True, "message": f"{code} 已用 {used} 写入 {path.name}，{len(rows)} 根确认收盘", "source": used, "bars": len(rows)}
    except Exception as exc:
        return {"ok": False, "message": f"腾讯/新浪/东财均失败：{exc}"}
