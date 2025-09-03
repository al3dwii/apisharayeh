# src/app/core/logging.py
from __future__ import annotations

import datetime as dt
import logging
import os
import sys
from typing import Tuple

from app.core.ctx import get_ctx

# Context fields we want present on every record
CTX_FIELDS: Tuple[str, ...] = (
    "tenant_id",
    "run_id",
    "plan_id",
    "node_id",
    "trace_id",
)

# Keep a handle to the original factory
_old_factory = logging.getLogRecordFactory()


def _record_factory(*args, **kwargs):
    """
    Global LogRecord factory that attaches context fields to *every* record,
    so formatters like '%(tenant_id)s' won't explode for third-party logs.
    """
    rec: logging.LogRecord = _old_factory(*args, **kwargs)
    ctx = get_ctx()  # {'tenant_id': ..., ...}

    # Inject context values or None if absent
    for f in CTX_FIELDS:
        if not hasattr(rec, f):
            rec.__dict__[f] = ctx.get(f)

    return rec


logging.setLogRecordFactory(_record_factory)


class _SafeFormatter(logging.Formatter):
    """ISO8601 UTC timestamps and resilience to missing context fields."""
    def format(self, record: logging.LogRecord) -> str:
        # Ensure asctime in ISO8601 UTC
        if not hasattr(record, "asctime"):
            record.asctime = dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
        # Guarantee all context fields exist (even if None)
        for f in CTX_FIELDS:
            if not hasattr(record, f):
                record.__dict__[f] = None
        return super().format(record)


def _build_handler() -> logging.Handler:
    h = logging.StreamHandler(sys.stdout)
    fmt = os.getenv(
        "LOG_FORMAT",
        "%(asctime)s %(levelname)s %(name)s "
        "tenant=%(tenant_id)s run=%(run_id)s plan=%(plan_id)s node=%(node_id)s "
        "msg=%(message)s"
    )
    h.setFormatter(_SafeFormatter(fmt))
    return h


def _configure_root() -> None:
    root = logging.getLogger()
    if not root.handlers:
        root.addHandler(_build_handler())
    root.setLevel(os.getenv("LOG_LEVEL", "INFO"))

    # Calm down noisy libs
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("temporalio").setLevel(logging.INFO)


# Configure at import time so even early third-party logs are safe
_configure_root()


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
