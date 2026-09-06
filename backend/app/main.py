from __future__ import annotations

import os
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .auth import COOKIE, cookie_ok, expected_token, password_ok
from .config import (
    ALLOWED_STATUS,
    BUILTIN_CONDITIONS,
    CSV_DIR,
    PROFILE_PATH,
    ROOT,
    RULES_PATH,
    SCAN_CACHE_DIR,
    ensure_dirs,
)
from .engine.bars import attach_indicators, list_csv_files, load_bars, ts_code
from .engine.clock import asof_date
from .engine.cycles import cycles_for_pool, cycles_for_stock, cycles_page
from .engine.history import backfill_all_ashare
from .engine.live import pull_one, sync_live
from .engine.rules_bind import parse_flags, refresh_bind
from .engine.rulesets import get_ruleset, list_rulesets, public_ruleset
from .engine.scanner import classify_stock, funnel_reminders, scan_universe, summarize
from .engine.scheduler import schedule_snapshot, start_scheduler, stop_scheduler
from .engine.watch import queue_counts, refresh_watch

from .store import (
    load_ideas,
    load_pool_snapshot,
    load_settings,
    load_sync_status,
    load_trades,
    load_universe,
    load_watches,
    read_json,
    save_ideas,
    save_settings,
    save_trades,
    save_watches,
    write_json,
)

ensure_dirs()

_sync_lock = threading.Lock()
_sync_thread: threading.Thread | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    start_scheduler()
    if not load_universe():
        threading.Thread(target=lambda: sync_live(False), daemon=True, name="boot-sync").start()
    yield
    stop_scheduler()


app = FastAPI(title="GeorgeChin Personal Trade", docs_url=None, redoc_url=None, lifespan=lifespan)
_cors = [
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:5174",
    "http://localhost:5174",
    "http://127.0.0.1:8000",
]
_cors += [x.strip() for x in os.environ.get("CORS_ORIGINS", "").split(",") if x.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if request.method == "OPTIONS":
        return await call_next(request)
    if path in ("/api/login", "/api/logout", "/api/health", "/api/session") or not path.startswith("/api"):
        return await call_next(request)
    if cookie_ok(request.cookies.get(COOKIE)):
        return await call_next(request)
    return JSONResponse({"detail": "未登录"}, status_code=401)


class LoginIn(BaseModel):
    password: str = ""


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
    schedule_enabled: bool | None = None


class PullIn(BaseModel):
    code: str


def _universe_map() -> dict:
    return {ts_code(item["code"]): item for item in load_universe()}


def _name_of(code: str) -> str:
    meta = _universe_map().get(ts_code(code))
    if meta and (meta.get("name") or "").strip() and meta.get("name") != ts_code(code):
        return str(meta["name"]).strip()
    bars = load_bars(code, last_n=1)
    if bars and (bars[-1].get("name") or "").strip():
        return str(bars[-1]["name"]).strip()
    return ts_code(code)


def _refreshed_watches() -> list[dict]:
    settings = load_settings()
    uni = _universe_map()
    items = load_watches()
    out = [refresh_watch(item, uni, settings) for item in items]
    save_watches(out)
    return out


def _scan_cache_path(ruleset_id: str):
    ensure_dirs()
    return SCAN_CACHE_DIR / f"{ruleset_id}.json"


def _stamp_rows(rows: list, rs: dict) -> list[dict]:
    rid = rs.get("id") or "rules"
    eng = rs.get("engine") or ""
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if row.get("ruleset") and row["ruleset"] != rid:
            continue
        row["ruleset"] = rid
        row["engine"] = eng
        out.append(row)
    return out


def _cache_mixed(cached: dict, rs: dict) -> bool:
    rid = rs.get("id") or "rules"
    eng = rs.get("engine")
    if cached.get("ruleset") not in (None, rid):
        return True
    if cached.get("engine") not in (None, eng):
        return True
    for row in cached.get("rows") or []:
        if isinstance(row, dict) and row.get("ruleset") and row["ruleset"] != rid:
            return True
    return False


def _scan_bundle(ruleset_id: str | None = None):
    rs = get_ruleset(ruleset_id)
    if rs is None:
        return None
    flags = parse_flags(rs["text"])
    bind = refresh_bind(rs["text"], rs["id"])
    settings = load_settings()
    trades = load_trades()
    asof = asof_date(settings.get("last_trade_date") or "")
    token = f"{bind.get('rules_hash')}:{asof}:{rs.get('engine')}:{len(trades)}:session"
    cache_path = _scan_cache_path(rs["id"])
    cached = read_json(cache_path, {}) if cache_path.exists() else {}
    if (
        isinstance(cached, dict)
        and cached.get("token") == token
        and isinstance(cached.get("rows"), list)
        and not _cache_mixed(cached, rs)
    ):
        rows = _stamp_rows(cached["rows"], rs)
        if rs.get("engine") == "pullback_restart":
            from .engine.structure_one import scan_structure_one as s1_scan

            s1_scan.funnel = cached.get("boards") or []
            s1_scan.market = cached.get("market")
        return rs, flags, bind, rows
    rows = _stamp_rows(
        scan_universe(
            load_universe(),
            settings,
            trades,
            flags=flags,
            engine=rs["engine"],
        ),
        rs,
    )
    payload = {
        "token": token,
        "ruleset": rs["id"],
        "engine": rs.get("engine"),
        "rows": rows,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    if rs.get("engine") == "pullback_restart":
        from .engine.structure_one import scan_structure_one as s1_scan

        payload["boards"] = list(getattr(s1_scan, "funnel", None) or [])
        payload["market"] = getattr(s1_scan, "market", None)
    write_json(cache_path, payload)
    return rs, flags, bind, rows


def _scan_rows(ruleset_id: str | None = None) -> list[dict]:
    bundle = _scan_bundle(ruleset_id)
    if not bundle:
        return []
    return bundle[3]


@app.get("/api/session")
def session_get(request: Request):
    return {"ok": cookie_ok(request.cookies.get(COOKIE))}


@app.post("/api/login")
def login(payload: LoginIn):
    if not password_ok(payload.password):
        raise HTTPException(401, "密码不对")
    resp = JSONResponse({"ok": True})
    resp.set_cookie(COOKIE, expected_token(), httponly=True, samesite="lax", path="/", max_age=60 * 60 * 24 * 30)
    return resp


@app.post("/api/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE, path="/")
    return resp


@app.get("/api/health")
def health():
    files = list_csv_files()
    settings = load_settings()
    source = settings.get("data_source") or "csv"
    live = source not in ("csv", "", None)
    label = settings.get("data_label")
    if not label:
        label = "真实行情已连接" if live or files else "尚未连接真实行情"
    return {
        "ok": True,
        "connected": bool(live) or len(load_universe()) > 0,
        "label": label,
        "csv_count": len(files),
        "pool_count": len(load_universe()),
        "data_source": source,
        "last_trade_date": settings.get("last_trade_date") or "",
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
        "names": summary.get("names") or {},
        "position_block": "总闸：排除 → 观察 → 买入 → 卖出。买入不是成交指令",
        "market_regime": load_settings().get("market_regime"),
        "person_present": load_settings().get("person_present"),
        "pool_count": len(load_universe()),
        "pool_trade_date": load_settings().get("last_trade_date") or "",
        "data_source": load_settings().get("data_source") or "csv",
        "reminders": funnel_reminders(load_settings()),
        "scan_ruleset": "rules",
        "ruleset": public_ruleset(get_ruleset("rules")),
        "rulesets": [public_ruleset(item) for item in list_rulesets()],
    }


@app.get("/api/rulesets")
def rulesets():
    return {"items": [public_ruleset(item) for item in list_rulesets()]}


@app.get("/api/scan")
def scan(ruleset: str = Query("rules")):
    bundle = _scan_bundle(ruleset)
    if not bundle:
        raise HTTPException(404, "没有这个规则文件")
    rs, _flags, bind, rows = bundle
    grouped = {key: [] for key in ("排除", "观察", "买入", "卖出")}
    for row in rows:
        grouped.setdefault(row["status"], []).append(row)
    snap = load_pool_snapshot()
    tallied = summarize(rows)
    reminders = funnel_reminders(load_settings()) + list(bind.get("unimplemented") or [])
    if not rs.get("engine_ok"):
        reminders = [rs["engine_note"]] + reminders
    if rs.get("engine") == "pullback_restart":
        from .engine.structure_one import _load_industry_map

        if len(_load_industry_map()) < 100:
            reminders.append("第3条 底池：板块归属表为空或过少。请到数据与设置刷新板块后再扫 RULES2。")
    buy_n = (tallied.get("by_gate") or {}).get("买入") or 0
    if buy_n > 1:
        reminders.append(f"第6条 / 第8条：买入池 {buy_n} 只。当日全市场新开 ≤ 1 只试仓，禁止一次打满。")
    pullback = rs.get("engine") == "pullback_restart"
    pool_count = len(rows) if pullback else len(load_universe())
    pool_note = (
        "RULES2：先第3.2条筛板块（近3日≥沪深300且非最弱），再在过关板块里挑个股。"
        if pullback
        else "PROFILE 同时跟踪 100 只。下列按当前规则全量列出，不截断。"
    )
    boards = []
    market = None
    if pullback:
        from .engine.structure_one import scan_structure_one as s1_scan

        boards = list(getattr(s1_scan, "funnel", None) or [])
        market = getattr(s1_scan, "market", None)
        passed_n = sum(1 for b in boards if b.get("pass"))
        reminders.append(f"第3.2条 主线：过关 {passed_n} / {len(boards)} 个申万一级。不弱即可；转弱则整组出池。")
    return {
        "rows": rows,
        **tallied,
        "grouped": grouped,
        "boards": boards,
        "market": market,
        "position_block": "总闸：排除 → 观察 → 买入 → 卖出。买入不是成交指令。",
        "reminders": reminders,
        "rules_bind": bind,
        "ruleset": public_ruleset(rs),
        "rulesets": [public_ruleset(item) for item in list_rulesets()],
        "pool": {
            "count": pool_count,
            "trade_date": asof_date(snap.get("trade_date") or load_settings().get("last_trade_date")),
            "source": snap.get("source") or load_settings().get("data_source"),
            "preferred": snap.get("preferred"),
            "funnel": snap,
            "note": pool_note,
        },
    }


def _classify_for(code: str, ruleset_id: str | None = None) -> dict:
    rs = get_ruleset(ruleset_id)
    if rs and rs.get("engine") == "pullback_restart":
        from .engine.structure_one import classify_one_s1

        row = classify_one_s1(code, load_settings(), load_trades())
    elif rs and rs.get("engine") == "low_golden":
        uni = _universe_map()
        meta = uni.get(ts_code(code)) or {"code": code, "name": _name_of(code)}
        row = classify_stock(meta, load_settings(), load_trades())
    else:
        note = (rs or {}).get("engine_note") or "没有这个规则文件"
        row = {
            "code": ts_code(code),
            "name": _name_of(code),
            "status": "排除",
            "gate": "排除",
            "hit_rules": [],
            "missing_rules": [note],
            "facts": {},
            "fact_note": "这是事实记录",
            "position_block": note,
        }
    if rs:
        row["ruleset"] = rs["id"]
        row["engine"] = rs.get("engine")
    return row


@app.get("/api/scan/{code}")
def scan_one(code: str, ruleset: str = Query("rules")):
    return _classify_for(code, ruleset)


def _stamp_cycle_payload(payload: dict, rs: dict) -> dict:
    rid = rs["id"]
    eng = rs.get("engine")
    kept = []
    for seg in payload.get("segments") or []:
        if not isinstance(seg, dict):
            continue
        if seg.get("ruleset") and seg["ruleset"] != rid:
            continue
        seg["ruleset"] = rid
        seg["engine"] = eng
        kept.append(seg)
    payload["segments"] = kept
    payload["rulesets"] = [public_ruleset(item) for item in list_rulesets()]
    return payload


def _pool_gate_set(gate: str) -> set[str] | None:
    raw = (gate or "").strip()
    if raw == "在池":
        return {"观察", "买入"}
    if raw in ("观察", "买入"):
        return {raw}
    return None


@app.get("/api/cycles")
def cycles(
    ruleset: str = Query("rules"),
    tab: str = Query("all"),
    q: str = Query(""),
    sort: str = Query("default"),
    order: str = Query("desc"),
    page: int = Query(1),
    page_size: int = Query(40),
    code: str = Query(""),
    gate: str = Query(""),
):
    rs = get_ruleset(ruleset)
    if rs is None:
        raise HTTPException(404, "没有这个规则文件")
    if (code or "").strip():
        want = ts_code(code)
        payload = cycles_for_stock(want, _name_of(want), rs)
        return _stamp_cycle_payload(payload, rs)
    wanted = _pool_gate_set(gate)
    if wanted:
        rows = _scan_rows(rs["id"])
        items = [
            row
            for row in rows
            if (row.get("status") or row.get("gate")) in wanted
            and (not row.get("ruleset") or row.get("ruleset") == rs["id"])
        ]
        payload = cycles_for_pool(items, rs)
        payload["gate"] = gate
        return _stamp_cycle_payload(payload, rs)
    flags = parse_flags(rs["text"])
    payload = cycles_page(
        load_universe(),
        flags=flags,
        ruleset=rs,
        tab=tab,
        q=q,
        sort=sort,
        order=order,
        page=page,
        page_size=page_size,
    )
    return _stamp_cycle_payload(payload, rs)


@app.get("/api/chart/{code}")
def chart(code: str, ruleset: str = Query("rules")):
    bars = attach_indicators(load_bars(code))
    if not bars:
        raise HTTPException(404, "没有这只股票的本地 CSV，证据不足")
    return {
        "code": ts_code(code),
        "name": _name_of(code),
        "fact_note": "这是事实记录",
        "indicators": "MACD(7,28,4) + KDJ + 均线辅助展示",
        "bars": bars,
        "scan": _classify_for(code, ruleset),
        "ruleset": public_ruleset(get_ruleset(ruleset)),
        "rulesets": [public_ruleset(item) for item in list_rulesets()],
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
        raise HTTPException(400, "状态只能是：排除、观察、买入、卖出")
    warning = None
    if payload.status == "买入":
        warning = "总闸买入只表示路径到达。这是你自己的判断记录，不是成交指令。"
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


@app.get("/api/rules")
def rules():
    items = list_rulesets()
    return {
        "editable": False,
        "banner": "我的规则只读。仓位上限以对应 RULES 文件为准，网站不能改数字。总闸买入只表示路径到达，不是成交指令。",
        "profile": PROFILE_PATH.read_text(encoding="utf-8") if PROFILE_PATH.exists() else "",
        "rules": RULES_PATH.read_text(encoding="utf-8") if RULES_PATH.exists() else "",
        "items": [
            {
                **public_ruleset(item),
                "text": item.get("text") or "",
            }
            for item in items
        ],
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
    public["pool_count"] = len(load_universe())
    public["pool_snapshot"] = load_pool_snapshot()
    public["sync"] = load_sync_status()
    public["schedule"] = schedule_snapshot()
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
    return pull_one(payload.code)


@app.get("/api/pool")
def pool_get():
    items = load_universe()
    snap = load_pool_snapshot()
    return {
        "items": items,
        "count": len(items),
        "preferred": sum(1 for x in items if x.get("index_member")),
        "snapshot": snap,
        "profile_bandwidth": 100,
        "note": "PROFILE 同时跟踪 100 只。股池按第3条全量保留，扫描不截断。",
    }


@app.get("/api/sync")
def sync_status():
    return load_sync_status()


@app.post("/api/sync")
def sync_start(force: bool = Query(False)):
    global _sync_thread
    current = load_sync_status()
    if current.get("state") == "running" and _sync_thread and _sync_thread.is_alive():
        return {"ok": True, "started": False, "message": "同步已在进行", "status": current}

    def run():
        with _sync_lock:
            sync_live(force_bars=force)

    _sync_thread = threading.Thread(target=run, daemon=True)
    _sync_thread.start()
    return {"ok": True, "started": True, "message": "已开始：按第3条筛全部入池股并拉取确认收盘"}


@app.post("/api/sync/history")
def sync_history():
    global _sync_thread
    current = load_sync_status()
    if current.get("state") == "running" and _sync_thread and _sync_thread.is_alive():
        return {"ok": True, "started": False, "message": "同步已在进行", "status": current}

    def run():
        with _sync_lock:
            backfill_all_ashare()

    _sync_thread = threading.Thread(target=run, daemon=True, name="ashare-history")
    _sync_thread.start()
    return {"ok": True, "started": True, "message": "已开始补全全 A 近 3 年确认日线。不改规则扫描池。可在本页看进度。"}


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


@app.get("/api/sources")
def sources_probe():
    from .engine.eastmoney import probe_sources

    return {
        "chain": ["tencent", "sina", "eastmoney"],
        "items": probe_sources(),
        "note": "日线按腾讯 → 新浪 → 东财切换。全市场筛池优先新浪快照。不使用示例 CSV 当数据源。",
    }


@app.get("/api/schedule")
def schedule_get():
    return schedule_snapshot()


@app.post("/api/seed")
def seed_endpoint():
    return {"ok": False, "message": "不再写入示例日线。请用腾讯/新浪/东财同步确认收盘。"}


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
