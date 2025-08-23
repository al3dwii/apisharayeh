from __future__ import annotations
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from typing import Callable
import anyio

try:
    from app.core.config import settings  # type: ignore
except Exception:  # pragma: no cover
    class _S:
        MAX_UPLOAD_MB = 50
    settings = _S()  # type: ignore


class MaxBodySizeMiddleware(BaseHTTPMiddleware):
    """
    Reject requests whose bodies exceed MAX_UPLOAD_MB.
    Uses Content-Length when available; otherwise reads the stream and enforces a cap.
    """
    async def dispatch(self, request: Request, call_next: Callable):
        cap = int(getattr(settings, "MAX_UPLOAD_MB", 50)) * 1024 * 1024
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > cap:
            return JSONResponse({"detail": "request body too large"}, status_code=413)

        # For streaming/chunked uploads, wrap the receive channel
        received = 0

        async def limited_receive():
            nonlocal received
            message = await request.receive()
            if message["type"] == "http.request":
                body = message.get("body", b"")
                received += len(body or b"")
                if received > cap:
                    # drain the rest quickly
                    with anyio.move_on_after(0):
                        while message.get("more_body"):
                            message = await request.receive()
                    return {"type": "http.disconnect"}
            return message

        try:
            request._receive = limited_receive  # type: ignore
        except Exception:
            pass

        response = await call_next(request)
        return response
