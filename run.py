"""Start the local desk API (and seed sample CSV on first run)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))

import uvicorn  # noqa: E402

if __name__ == "__main__":
    print("GeorgeChin Personal Trade")
    print("Open http://127.0.0.1:8000  (after frontend build)")
    print("Or keep Vite at http://127.0.0.1:5173 with this API running.")
    uvicorn.run("app.main:app", app_dir=str(ROOT / "backend"), host="127.0.0.1", port=8000, reload=False)
