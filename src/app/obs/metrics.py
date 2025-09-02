from typing import Optional
import os
from time import perf_counter

from prometheus_client import start_http_server, Counter, Histogram

NODE_EVENTS = Counter("o2_node_events_total", "Node events", ["type", "tool"])
ACT_LAT = Histogram("o2_activity_seconds", "Activity seconds", ["tool"])

_booted: Optional[int] = None


def boot_metrics() -> None:
    """Start a Prometheus metrics exporter once."""
    global _booted
    if _booted:
        return
    port = int(os.getenv("METRICS_PORT", "9108"))
    start_http_server(port)
    _booted = port


class Timer:
    """Context manager to record activity duration per tool."""

    def __init__(self, tool: str):
        self.tool = tool
        self.t0 = 0.0

    def __enter__(self):
        self.t0 = perf_counter()
        return self

    def __exit__(self, *_):
        ACT_LAT.labels(self.tool).observe(perf_counter() - self.t0)
