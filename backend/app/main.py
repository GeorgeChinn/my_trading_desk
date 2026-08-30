from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import (
    ALLOWED_STATUS,
    BUILTIN_CONDITIONS,
    CSV_DIR,
    PROFILE_PATH,
    ROOT,
    RULES_PATH,
    ensure_dirs,
)
from .engine.bars import attach_indicators, csv_path_for, list_csv_files, load_bars, ts_code
from .engine.scanner import classify_stock, scan_universe, summarize
from .engine.tushare_client import pull_daily
from .engine.watch import queue_counts, refresh_watch
from .journal_io import list_journals, read_journal, today_str, write_journal
from .seed import seed as seed_sample
from .store import (
    load_ideas,
    load_settings,
    load_trades,
    load_universe,
    load_watches,
    save_ideas,
    save_settings,
    save_trades,
    save_watches,
)

ensure_dirs()
seed_sample(force=False)

app = FastAPI(title="GeorgeChin Personal Trade", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173", "http://127.0.0.1:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class WatchIn(BaseModel):
    code: str
    condition_id: str = "ma5_reclaim"
    condition_text: str = ""
    name: str = ""


class JudgementIn(BaseModel):
    status: str
    note: str = ""


class TradeIn(BaseModel):
    code: str
    name: str = ""
    direction: str
    position_pct: float = Field(..., ge=0, le=100)
    reason: str = ""
    date: str = ""


class IdeaIn(BaseModel):
    text: str
    code: str = ""


class SettingsIn(BaseModel):
    person_present: bool | None = None
    market_regime: str | None = None
    tushare_token: str | None = None


class JournalIn(BaseModel):
    market: str = ""
    mood: str = ""
    theme: str = ""
    candidates: str = ""
    action: str = ""
    path_fit: str = ""
    hit: str = ""
    broke: str = ""
    change: str = ""
    q1: str = ""
    q2: str = ""


class PullIn(BaseModel):
    code: str


def _universe_map() -> dict:
    return {ts_code(item["code"]): item for item in load_universe()}


def _name_of(code: str) -> str:
    meta = _universe_map().get(ts_code(code))
    return (meta or {}).get("name") or ts_code(code)


def _refreshed_watches() -> list[dict]:
    settings = load_settings()
    uni = _universe_map()
    items = load_watches()
    out = [refresh_watch(item, uni, settings) for item in items]
    save_watches(out)
    return out


def _scan_rows() -> list[dict]:
    return scan_universe(load_universe(), load_settings(), load_trades())


@app.get("/api/health")
def health():
    files = list_csv_files()
    return {
        "ok": True,
        "connected": len(files) > 0,
        "label": "本地数据已连接" if files else "本地 CSV 为空",
        "csv_count": len(files),
        "person": "GeorgeChin",
        "space": "本地个人交易空间",
    }


@app.get("/api/home")
def home():
    watches = _refreshed_watches()
    rows = _scan_rows()
    summary = summarize(rows)
    queues = queue_counts(watches)
    cards = []
    for item in watches:
        trig = item.get("trigger")
        if not trig:
            continue
        cards.append(
            {
                "id": item["id"],
                "code": item["code"],
                "name": item.get("name"),
                "condition_text": item.get("condition_text") or trig.get("condition_text"),
                "fact_note": "这是事实记录",
                "trigger": trig,
                "latest": item.get("latest"),
                "judgement": item.get("judgement"),
                "viewed": item.get("viewed", False),
            }
        )
    return {
        "health": health(),
        "path": "波段持有",
        "triggered_count": len(cards),
        "cards": cards,
        "queues": queues,
        "scan_summary": summary["summary"],
        "by_gate": summary["by_gate"],
        "position_block": "仓位阈值空缺，不得升到试仓/标准仓",
        "market_regime": load_settings().get("market_regime"),
        "person_present": load_settings().get("person_present"),
    }


@app.get("/api/scan")
def scan():
    rows = _scan_rows()
    grouped = {key: [] for key in ("排除", "观察", "等待", "试仓", "标准仓", "禁止")}
    for row in rows:
        grouped.setdefault(row["status"], []).append(row)
    return {"rows": rows, **summarize(rows), "grouped": grouped, "position_block": "仓位阈值空缺，不得升到试仓/标准仓"}


@app.get("/api/scan/{code}")
def scan_one(code: str):
    uni = _universe_map()
    meta = uni.get(ts_code(code)) or {"code": code, "name": _name_of(code)}
    return classify_stock(meta, load_settings(), load_trades())


@app.get("/api/chart/{code}")
def chart(code: str):
    bars = attach_indicators(load_bars(code))
    if not bars:
        raise HTTPException(404, "没有这只股票的本地 CSV，证据不足")
    return {
        "code": ts_code(code),
        "name": _name_of(code),
        "fact_note": "这是事实记录",
        "indicators": "MACD(7,28,4) + KDJ + 均线辅助展示",
        "bars": bars,
        "scan": scan_one(code),
    }


@app.get("/api/conditions")
def conditions():
    return {"items": BUILTIN_CONDITIONS}


@app.get("/api/watch")
def watch_list():
    items = _refreshed_watches()
    return {"items": items, "queues": queue_counts(items), "conditions": BUILTIN_CONDITIONS}


@app.post("/api/watch")
def watch_add(payload: WatchIn):
    code = ts_code(payload.code)
    if not code:
        raise HTTPException(400, "需要股票代码")
    items = load_watches()
    text = payload.condition_text.strip()
    if not text:
        match = next((c for c in BUILTIN_CONDITIONS if c["id"] == payload.condition_id), None)
        text = (match or BUILTIN_CONDITIONS[0])["text"]
        cid = (match or BUILTIN_CONDITIONS[0])["id"]
    else:
        cid = payload.condition_id if payload.condition_id in {c["id"] for c in BUILTIN_CONDITIONS} else "custom"
    item = {
        "id": uuid.uuid4().hex[:12],
        "code": code,
        "name": payload.name or _name_of(code),
        "condition_id": cid,
        "condition_text": text,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "viewed": False,
        "monitoring": True,
        "triggered": False,
        "judgement": None,
    }
    items.append(item)
    save_watches(items)
    return {"item": refresh_watch(item, _universe_map(), load_settings())}


@app.delete("/api/watch/{watch_id}")
def watch_del(watch_id: str):
    items = [item for item in load_watches() if item.get("id") != watch_id]
    save_watches(items)
    return {"ok": True}


@app.post("/api/watch/{watch_id}/viewed")
def watch_viewed(watch_id: str):
    items = load_watches()
    found = None
    for item in items:
        if item.get("id") == watch_id:
            item["viewed"] = True
            found = item
    if not found:
        raise HTTPException(404, "观察不存在")
    save_watches(items)
    return {"item": found}


@app.post("/api/watch/{watch_id}/judgement")
def watch_judge(watch_id: str, payload: JudgementIn):
    if payload.status not in ALLOWED_STATUS:
        raise HTTPException(400, "状态只能是：排除、观察、等待、试仓、标准仓、禁止")
    warning = None
    if payload.status in ("试仓", "标准仓"):
        warning = "RULES 未给出仓位数字。这是你自己的判断记录，不是系统升级，系统不得把路径匹配写成可开仓。"
    items = load_watches()
    found = None
    for item in items:
        if item.get("id") == watch_id:
            item["judgement"] = {
                "status": payload.status,
                "note": payload.note,
                "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "warning": warning,
                "fact_note": "这是事实记录",
            }
            item["viewed"] = True
            found = item
    if not found:
        raise HTTPException(404, "观察不存在")
    save_watches(items)
    return {"item": found, "warning": warning}


@app.get("/api/trades")
def trades():
    return {"items": load_trades()}


@app.post("/api/trades")
def trade_add(payload: TradeIn):
    allowed_dir = {"开仓", "加仓", "减仓", "清仓", "记录"}
    if payload.direction not in allowed_dir:
        raise HTTPException(400, "方向只接受：开仓 / 加仓 / 减仓 / 清仓 / 记录")
    items = load_trades()
    item = {
        "id": uuid.uuid4().hex[:12],
        "code": ts_code(payload.code),
        "name": payload.name or _name_of(payload.code),
        "direction": payload.direction,
        "position_pct": payload.position_pct,
        "reason": payload.reason,
        "date": payload.date or datetime.now().strftime("%Y-%m-%d"),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "note": "手工记账，不同步任何外部账户",
    }
    items.insert(0, item)
    save_trades(items)
    return {"item": item}


@app.delete("/api/trades/{trade_id}")
def trade_del(trade_id: str):
    save_trades([item for item in load_trades() if item.get("id") != trade_id])
    return {"ok": True}


@app.get("/api/journal")
def journal_list():
    return {"items": list_journals(), "template": "journal/TEMPLATE.md"}


@app.get("/api/journal/{day}")
def journal_get(day: str):
    return read_journal(day)


@app.put("/api/journal/{day}")
def journal_put(day: str, payload: JournalIn):
    return write_journal(day, payload.model_dump())


@app.get("/api/journal-today")
def journal_today():
    return read_journal(today_str())


@app.get("/api/rules")
def rules():
    return {
        "editable": False,
        "banner": "我的规则只读。仓位上限以 RULES.md 为准，网站不能改数字。当前文中无试仓/标准仓阈值，扫描不得升到这两档。",
        "profile": PROFILE_PATH.read_text(encoding="utf-8") if PROFILE_PATH.exists() else "",
        "rules": RULES_PATH.read_text(encoding="utf-8") if RULES_PATH.exists() else "",
    }


@app.get("/api/settings")
def settings_get():
    data = load_settings()
    public = dict(data)
    token = public.get("tushare_token") or ""
    public["tushare_configured"] = bool(token)
    public["tushare_token"] = "********" if token else ""
    public["csv_files"] = list_csv_files()
    public["csv_dir"] = str(CSV_DIR)
    return public


@app.put("/api/settings")
def settings_put(payload: SettingsIn):
    patch = {k: v for k, v in payload.model_dump().items() if v is not None}
    if patch.get("market_regime") not in (None, "多", "空", "震荡", "未设置"):
        raise HTTPException(400, "市况只接受：多 / 空 / 震荡 / 未设置")
    saved = save_settings(patch)
    return settings_get() if saved else settings_get()


@app.post("/api/settings/csv")
async def upload_csv(file: UploadFile = File(...)):
    ensure_dirs()
    name = Path(file.filename or "upload.csv").name
    if not name.lower().endswith(".csv"):
        raise HTTPException(400, "只接受 CSV")
    raw = await file.read()
    # If filename is a stock code, keep it; otherwise peek first row.
    dest_name = name
    text = raw.decode("utf-8-sig")
    first = text.splitlines()[0] if text.splitlines() else ""
    target = CSV_DIR / dest_name
    if dest_name.replace(".csv", "").replace(".CSV", "").isdigit() is False:
        # try code column later; still save as given
        pass
    target.write_text(text, encoding="utf-8")
    return {"ok": True, "file": target.name, "header": first, "bytes": len(raw)}


@app.post("/api/settings/tushare")
def tushare_pull(payload: PullIn):
    settings = load_settings()
    token = settings.get("tushare_token") or ""
    result = pull_daily(payload.code, token)
    return result


@app.get("/api/ideas")
def ideas():
    return {"items": load_ideas()}


@app.post("/api/ideas")
def idea_add(payload: IdeaIn):
    items = load_ideas()
    item = {
        "id": uuid.uuid4().hex[:12],
        "text": payload.text.strip(),
        "code": ts_code(payload.code) if payload.code else "",
        "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    if not item["text"]:
        raise HTTPException(400, "想法不能空")
    items.insert(0, item)
    save_ideas(items)
    return {"item": item}


@app.post("/api/seed")
def seed_endpoint():
    return seed_sample(force=True)


DIST = ROOT / "frontend" / "dist"
if DIST.exists():
    assets = DIST / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(404, "not found")
        candidate = DIST / full_path
        if full_path and candidate.exists() and candidate.is_file():
            return FileResponse(candidate)
        index = DIST / "index.html"
        if index.exists():
            return FileResponse(index)
        raise HTTPException(404, "frontend dist missing")
