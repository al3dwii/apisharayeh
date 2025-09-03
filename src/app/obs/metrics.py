from __future__ import annotations

import os
import threading
from typing import Optional

from prometheus_client import Counter, Gauge, start_http_server

from app.core.logging import get_logger

log = get_logger(__name__)

_METRICS_PORT = int(os.getenv("O2_METRICS_PORT", "9111"))

# ---- registry (global default) ----
# Labels kept minimal; extend as needed.
o2_node_started   = Counter("o2_node_started_total", "Nodes started", ["tenant_id", "tool_id"])
o2_node_succeeded = Counter("o2_node_succeeded_total", "Nodes succeeded", ["tenant_id", "tool_id"])
o2_node_failed    = Counter("o2_node_failed_total", "Nodes failed", ["tenant_id", "tool_id"])
o2_node_cached    = Counter("o2_node_cached_total", "Nodes short-circuited from cache", ["tenant_id", "tool_id"])

o2_nodes_inflight = Gauge("o2_nodes_inflight", "Currently running nodes")
o2_plan_cc_current = Gauge("o2_plan_concurrency_current", "Current plan-level concurrency")


_started = False
_lock = threading.Lock()


def boot_metrics(port: Optional[int] = None) -> None:
    """
    Start Prometheus HTTP exporter exactly once.
    Safe to call multiple times.
    """
    global _started
    if _started:
        return
    with _lock:
        if _started:
            return
        p = int(port or _METRICS_PORT)
        try:
            start_http_server(p)
            _started = True
            log.info("Prometheus exporter started on :%d", p)
        except OSError as e:
            # Port already in use – treat as non-fatal
            log.warning("Prometheus exporter not started (port=%d): %r", p, e)
            _started = True  # avoid retry storm
