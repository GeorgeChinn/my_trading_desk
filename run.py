"""Start the local desk API."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))

import uvicorn  # noqa: E402

if __name__ == "__main__":
    backend = ROOT / "backend"
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    reload = os.environ.get("RELOAD", "1") not in ("0", "false", "False")
    print("GeorgeChin Personal Trade")
    print(f"监听 {host}:{port}  reload={reload}")
    print("前端热更新：http://127.0.0.1:5173")
    kwargs = {
        "app": "app.main:app",
        "app_dir": str(backend),
        "host": host,
        "port": port,
        "reload": reload,
    }
    if reload:
        kwargs["reload_dirs"] = [str(backend)]
        kwargs["reload_includes"] = ["*.py"]
    uvicorn.run(**kwargs)
