from prometheus_client import Counter, Histogram, make_asgi_app
from starlette.middleware.base import BaseHTTPMiddleware
import time

REQUEST_LATENCY = Histogram(
    "http_request_latency_seconds",
    "Latency of HTTP requests",
    ["path", "method", "status"],
    buckets=[0.005,0.01,0.025,0.05,0.1,0.25,0.5,1,2,5],
)
REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["path", "method", "status"],
)

TOKENS_USED = Counter(
    "tokens_used_total",
    "Tokens used (sum of in+out)",
    ["tenant_id"],
)

class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        start = time.perf_counter()
        resp = await call_next(request)
        latency = time.perf_counter() - start
        path = request.url.path
        method = request.method
        status = str(resp.status_code)
        REQUEST_LATENCY.labels(path, method, status).observe(latency)
        REQUEST_COUNT.labels(path, method, status).inc()
        return resp

metrics_app = make_asgi_app()
