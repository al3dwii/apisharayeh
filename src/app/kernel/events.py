# src/app/kernel/events.py
from __future__ import annotations
import asyncio
import inspect
import threading
import time
from typing import Any, Callable, Dict, Optional


class EventEmitter:
    """
    Non-blocking event bridge between the worker thread (DSL/ops) and the FastAPI loop.

    - Accepts either sync or async `on_emit(event_type, payload)` callback.
    - If called from a worker thread and `loop` is provided, schedules the callback
      onto that loop without blocking.
    - Swallows exceptions from the callback to avoid breaking the worker.
    """

    def __init__(
        self,
        on_emit: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
        loop: Optional[asyncio.AbstractEventLoop] = None,
    ) -> None:
        self._on_emit = on_emit
        self._loop = loop

    def emit(self, event_type: str, payload: Dict[str, Any]) -> None:
        """Fire-and-forget: never block the caller thread."""
        if not self._on_emit:
            return

        # Enrich with timestamp here; ExecutionContext may add job/project ids.
        payload = dict(payload or {})
        evt = {"type": event_type, "payload": payload, "ts": time.time()}

        cb = self._on_emit
        try:
            if inspect.iscoroutinefunction(cb):
                # Async callback
                coro = cb(event_type, payload)
                loop = self._loop or asyncio.get_event_loop()

                if loop.is_running():
                    # From worker thread → schedule safely onto the loop
                    try:
                        asyncio.run_coroutine_threadsafe(coro, loop)
                    except RuntimeError:
                        # Fallback: if we're actually on the loop thread, create a task
                        asyncio.create_task(coro)
                else:
                    # No running loop (rare in server) → run it directly
                    asyncio.run(coro)

            else:
                # Sync callback
                loop = self._loop
                if loop and loop.is_running() and threading.current_thread() is not threading.main_thread():
                    loop.call_soon_threadsafe(cb, event_type, payload)
                else:
                    cb(event_type, payload)

        except Exception:
            # Never let event delivery errors break the pipeline
            # (Intentionally silent; inspect server logs if needed)
            pass
