from __future__ import annotations
import os
import sys
import uvicorn

# --- Make sure ./src is on sys.path so `app.*` is importable ---
BASE_DIR = os.path.dirname(__file__)        # points to "<repo>/src"
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

if __name__ == "__main__":
    os.environ.setdefault("DEV_OFFLINE", "true")
    os.environ.setdefault("ARTIFACTS_DIR", "./artifacts")
    os.environ.setdefault("PLUGINS_DIR", "./plugins")
    # `app.server.app:app` now imports correctly because ./src is on sys.path
    uvicorn.run("app.server.app:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), reload=False)
