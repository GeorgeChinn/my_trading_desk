from __future__ import annotations

from datetime import datetime, timedelta

from .bars import save_bars_csv, suffix_for, ts_code


def pull_daily(code: str, token: str, start: str | None = None, end: str | None = None) -> dict:
    """Reserved Tushare pull. No token → caller must use CSV."""
    if not token:
        return {"ok": False, "message": "未配置 Tushare token，使用本地 CSV"}
    try:
        import tushare as ts  # type: ignore
    except ImportError:
        return {"ok": False, "message": "未安装 tushare，使用本地 CSV"}

    end = end or datetime.now().strftime("%Y%m%d")
    start = start or (datetime.now() - timedelta(days=400)).strftime("%Y%m%d")
    ts_code_full = f"{ts_code(code)}.{suffix_for(code)}"
    try:
        pro = ts.pro_api(token)
        frame = pro.daily(ts_code=ts_code_full, start_date=start, end_date=end)
    except Exception as exc:
        return {"ok": False, "message": f"Tushare 拉取失败，回退 CSV：{exc}"}
    if frame is None or frame.empty:
        return {"ok": False, "message": "Tushare 无数据，使用本地 CSV"}
    frame = frame.sort_values("trade_date")
    rows = []
    for _, rec in frame.iterrows():
        rows.append(
            {
                "date": f"{str(rec['trade_date'])[:4]}-{str(rec['trade_date'])[4:6]}-{str(rec['trade_date'])[6:8]}",
                "open": float(rec["open"]),
                "high": float(rec["high"]),
                "low": float(rec["low"]),
                "close": float(rec["close"]),
                "volume": float(rec["vol"]) * 100.0,
                "amount": float(rec["amount"]) * 1000.0,
            }
        )
    path = save_bars_csv(code, rows)
    return {"ok": True, "message": f"已写入 {path.name}", "bars": len(rows)}
