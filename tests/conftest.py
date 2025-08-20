# tests/conftest.py
import os, sys, pathlib

# tests/conftest.py
from pathlib import Path

# Force local plugins for tests unless overridden
os.environ.setdefault("PLUGINS_DIR", str(Path("plugins").resolve()))
os.environ.setdefault("SERVICE_FLAGS", "*")


# Add <repo>/src to sys.path so `import app...` works under pytest
ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
