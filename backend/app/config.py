from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
CSV_DIR = DATA_DIR / "csv"
JOURNAL_DIR = ROOT / "journal"
RULES_PATH = ROOT / "RULES.md"
PROFILE_PATH = ROOT / "PROFILE.md"

UNIVERSE_PATH = DATA_DIR / "universe.json"
POOL_SNAPSHOT_PATH = DATA_DIR / "pool_snapshot.json"
SYNC_STATUS_PATH = DATA_DIR / "sync_status.json"
WATCHES_PATH = DATA_DIR / "watches.json"
TRADES_PATH = DATA_DIR / "trades.json"
IDEAS_PATH = DATA_DIR / "ideas.json"
JOURNALS_INDEX = DATA_DIR / "journals.json"
SETTINGS_PATH = DATA_DIR / "settings.json"
JUDGEMENTS_PATH = DATA_DIR / "judgements.json"

# RULES.md numbers — read-only copies for the engine. Do not invent new ones.
MACD_FAST = 7
MACD_SLOW = 28
MACD_SIGNAL = 4
POOL_FLOAT_MCAP_YI = 300.0
POOL_AMOUNT_YI = 5.0
VETO_AMOUNT_YI = 1.0
POOL_MIN_PRICE = 5.0
KDJ_LOW = 20.0
KDJ_HIGH = 80.0
DIF_LOOKBACK = 20
HHV_LOOKBACK = 20

ALLOWED_STATUS = ("排除", "观察", "等待", "试仓", "标准仓", "禁止")
SCAN_SUMMARY_BUCKETS = ("符合", "继续跟踪", "观察", "排除")

BUILTIN_CONDITIONS = [
    {
        "id": "ma5_reclaim",
        "text": "日线收盘重新站上5日均线",
    },
    {
        "id": "macd_green_shrink",
        "text": "MACD绿柱缩短不创新低",
    },
]


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CSV_DIR.mkdir(parents=True, exist_ok=True)
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
