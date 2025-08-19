from __future__ import annotations
import asyncio
import inspect
from typing import Any, Dict, Callable, Optional


class EventEmitter:
    """
    Emits kernel events. Supports both sync and async callbacks.
    If called from worker threads and the callback is async,
    we schedule it on the provided asyncio loop.
    """

    def __init__(self, on_emit: Optional[Callable[[str, Dict[str, Any]], Any]] = None, loop: Optional[asyncio.AbstractEventLoop] = None):
        self._on_emit = on_emit
        self._loop = loop

    def emit(self, event_type: str, payload: Dict[str, Any]) -> None:
        if not self._on_emit:
            return

        cb = self._on_emit
        try:
            if inspect.iscoroutinefunction(cb):
                # Prefer the loop provided by the server
                if self._loop and self._loop.is_running():
                    asyncio.run_coroutine_threadsafe(cb(event_type, payload), self._loop)
                else:
                    # Try current loop if in the same thread
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(cb(event_type, payload))
                    except RuntimeError:
                        # No running loop (in worker thread) and no loop provided → last resort
                        asyncio.run(cb(event_type, payload))
            else:
                cb(event_type, payload)
        except Exception:
            # Never let event delivery crash the kernel path
            pass
