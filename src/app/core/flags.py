# src/app/core/flags.py
from __future__ import annotations
import os

def _flag(name: str, default: str = "1") -> bool:
    return os.getenv(name, default).strip() in ("1", "true", "True", "YES", "yes")

# Step 1 (Registry enforcement) – off by default until we wire it
FLAG_REGISTRY_ENFORCE = _flag("O2_FLAG_REGISTRY_ENFORCE", "0")

# Step 3 (Idempotency cache) – off by default until we wire it
FLAG_IDEMPOTENCY_CACHE = _flag("O2_FLAG_IDEMPOTENCY_CACHE", "0")
